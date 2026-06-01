package com.vkrauth.app.hce

import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.dao.TimeGrantDao
import com.vkrauth.app.data.local.entity.OutgoingReportEntity
import com.vkrauth.app.data.remote.CurrentAccount
import java.util.UUID
import javax.inject.Inject

class TapDecisionTree @Inject constructor(
    private val timeGrantDao: TimeGrantDao,
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val issuedKeyDao: IssuedKeyDao,
    private val pendingRevokeDao: PendingRevokeIntentDao,
    private val keyManager: KeyManager,
    private val account: CurrentAccount
) {

    suspend fun buildOperations(session: TapSession): List<PreparedOperation> {
        val ops = mutableListOf<PreparedOperation>()
        val nowDeviceSec = System.currentTimeMillis() / 1000
        val nowMs = nowDeviceSec * 1000
        val readerIdBytes = session.readerId ?: return ops
        val readerIdHex = readerIdBytes.toHex()
        val readerTimeSec = session.readerTimeSec
        val driftSec = kotlin.math.abs(nowDeviceSec - readerTimeSec)

        val grant = timeGrantDao.firstActiveForReader(readerIdHex, nowMs)
        if (grant != null && driftSec > TIME_DRIFT_THRESHOLD_SEC) {
            // Замыкаем grant-данные сейчас, а сам подписанный statement
            // строится лениво в момент handleFetch (это экономит ~200-400 мс
            // от ответа на PUSH_INFO — KeyManager.sign идёт через
            // AndroidKeyStore и блокирует NFC-тред).
            val authorityId = Serialization.extractAuthorityIdFromGrant(grant.fullGrantBytes)
            val grantBytes = grant.fullGrantBytes
            val grantKind = grant.kindByte()
            ops.add(
                PreparedOperation(
                    innerOpcode = 0x12,
                    builder = { s ->
                        val nonce = s.currentFreshNonce ?: return@PreparedOperation ByteArray(0)
                        val statement = Serialization.buildTimeSyncStatement(
                            readerId = readerIdBytes,
                            authorityId = authorityId,
                            newTime = System.currentTimeMillis() / 1000,
                            usedNonce = nonce,
                            kind = grantKind,
                            keyManager = keyManager
                        )
                        Serialization.buildTimeSyncOperation(
                            grantBytes = grantBytes,
                            statementBytes = statement
                        )
                    },
                    debugName = "TIME_SYNC"
                )
            )
        }

        val pending = pendingFilterDao.firstFor(readerIdHex, status = "downloaded")
        if (pending != null && pending.filterVersion > session.filterVersion) {
            val opBytes = Serialization.buildFilterUpdateOperation(
                courierIdHex = pending.courierIdHex,
                filterPackageBytes = pending.filterPackageBytes
            )
            ops.add(
                PreparedOperation(
                    innerOpcode = 0x13,
                    bytes = opBytes,
                    onResult = { result ->
                        session.pendingDeliveredIds.add(pending.deliveryId)
                        try {
                            val receipt = Serialization.extractReceiptFromFilterResult(result)
                            session.pendingOutgoingReports.add(
                                OutgoingReportEntity(
                                    reportId = UUID.randomUUID().toString(),
                                    type = "delivery_receipt",
                                    targetReaderId = readerIdHex,
                                    bytes = receipt,
                                    producedAtMs = System.currentTimeMillis()
                                )
                            )
                        } catch (_: Exception) {
                            // result was not well-formed — we still mark delivered,
                            // firmware may have processed it.
                        }
                    },
                    debugName = "FILTER_UPDATE"
                )
            )
        }

        ops.add(
            PreparedOperation(
                innerOpcode = 0x11,
                bytes = byteArrayOf(0x11),
                onResult = { result ->
                    session.pendingOutgoingReports.add(
                        OutgoingReportEntity(
                            reportId = UUID.randomUUID().toString(),
                            type = "filter_delivery_info",
                            targetReaderId = readerIdHex,
                            bytes = result,
                            producedAtMs = System.currentTimeMillis()
                        )
                    )
                },
                debugName = "FDI"
            )
        )

        // ПОРЯДОК ОПЕРАЦИЙ = СЦЕНАРИЙ отзыва (Approach A: клиент управляет типом отзыва
        // через op ORDER, прошивка не меняется). Базовый порядок пакетов в сессии:
        // TIME_SYNC → FILTER → FDI → [ACCESS если «пропуск и отзыв»] → REVOKE →
        // GET_BLACKLIST → [ACCESS если нет отзывов]. Инвариант ридера: после успешного
        // REVOKE_KEY ключ в blacklist и тот же ключ на ACCESS будет ОТКЛОНЁН, поэтому
        // «пропуск и отзыв» требует ACCESS СТРОГО ПЕРЕД REVOKE того же ключа.
        //   1) нет отзывов       → ACCESS в конце;
        //   2) «пропуск и отзыв» → ACCESS перед REVOKE (ключ ещё валиден);
        //   3) «только отзыв»    → ACCESS не ставится — дверь молчит.
        // requester (он же ключ для ACCESS) вычисляем ОДИН раз; он остаётся active до
        // тапа (markRevokedLocally отложен до commit-on-success), иначе self-revoke
        // потерял бы requester'а.
        val pendingRevokes = pendingRevokeDao.forReader(readerIdHex, "pending")
        val requester = issuedKeyDao.firstActiveForReader(readerIdHex, nowMs, thisDevice = true)
        val wantsFinalAccess = pendingRevokes.isNotEmpty() && pendingRevokes.any { it.grantFinalAccess }

        fun buildAccessOperation(): PreparedOperation = PreparedOperation(
            innerOpcode = 0x01,
            bytes = byteArrayOf(),
            onResult = { result ->
                try {
                    val verdict = Serialization.parseAccessVerdict(result)
                    session.lastAccessVerdict = verdict.resultName
                    // H3 fold-in: GET_PASSAGE_RECEIPT (0x16) is GONE. On RES_OK the
                    // 192-B reader-signed passage_receipt is APPENDED to the verdict
                    // tail (result[42:234]). We slice that exact body and enqueue it
                    // as the SAME "passage_receipt" report uploaded today — the
                    // backend contract is byte-identical (bare 192-B body).
                    val receipt = verdict.passageReceipt
                    if (receipt != null) {
                        session.pendingOutgoingReports.add(
                            OutgoingReportEntity(
                                reportId = UUID.randomUUID().toString(),
                                type = "passage_receipt",
                                targetReaderId = readerIdHex,
                                bytes = receipt,
                                producedAtMs = System.currentTimeMillis()
                            )
                        )
                    }
                } catch (_: Exception) {
                }
            },
            debugName = "ACCESS"
        )

        // Ветка 2 («пропуск и отзыв»): ACCESS ПЕРЕД REVOKE_KEY (ключ ещё валиден).
        if (wantsFinalAccess && requester != null) {
            ops.add(buildAccessOperation())
        }

        // REVOKE_KEY перед GET_BLACKLIST: успешный отзыв добавляет запись в blacklist,
        // и следующий GET_BLACKLIST увезёт серверу свежий подписанный снимок.
        if (requester != null) {
            val requesterBytes = requester.fullKeyBytes
            for (intent in pendingRevokes) {
                val intentCopy = intent
                ops.add(
                    PreparedOperation(
                        innerOpcode = 0x15,
                        builder = { s ->
                            val nonce = s.currentFreshNonce ?: return@PreparedOperation ByteArray(0)
                            Serialization.buildRevokeKeyOperation(
                                requesterKey = requesterBytes,
                                targetKey = intentCopy.targetFullKeyBytes,
                                usedNonce = nonce,
                                readerTimeEcho = readerTimeSec,
                                readerIdBytes = readerIdBytes,
                                signer = keyManager
                            )
                        },
                        onResult = { result ->
                            if (Serialization.opResultSucceeded(result)) {
                                session.pendingDeliveredRevokes.add(intentCopy.intentId)
                                session.pendingRevokedKeyIds.add(intentCopy.targetKeyIdHex)
                                session.blacklistCount += 1
                            }
                        },
                        debugName = "REVOKE_KEY(${intent.targetKeyIdHex.take(8)})"
                    )
                )
            }
        }

        // GET_BLACKLIST после отзывов — подписанный снимок blacklist серверу. Гейт:
        // есть записи у ридера ИЛИ предстоят отзывы. (Тяжёлый ~271 B результат уходит по
        // reader→phone PUSH_CHUNK — работает после фикса oversized-APDU в firmware
        // send_push_chunk_series; раньше именно он рвал поле.)
        if (session.blacklistCount > 0 || pendingRevokes.isNotEmpty()) {
            ops.add(
                PreparedOperation(
                    innerOpcode = 0x14,
                    bytes = byteArrayOf(0x14),
                    onResult = { result ->
                        if (result.isNotEmpty() && result[0] == 0x94.toByte()) {
                            session.pendingOutgoingReports.add(
                                OutgoingReportEntity(
                                    reportId = UUID.randomUUID().toString(),
                                    type = "blacklist_report",
                                    targetReaderId = readerIdHex,
                                    bytes = result,
                                    producedAtMs = System.currentTimeMillis()
                                )
                            )
                        }
                    },
                    debugName = "GET_BLACKLIST"
                )
            )
        }

        // Ветка 1 (нет отзывов): обычный ACCESS в конце сессии (базовый порядок).
        // Ветка 3 («только отзыв»): ACCESS не добавляется — дверь намеренно молчит.
        if (pendingRevokes.isEmpty() && requester != null) {
            ops.add(buildAccessOperation())
        }

        return ops
    }

    companion object {
        const val TIME_DRIFT_THRESHOLD_SEC = 15L
    }
}
