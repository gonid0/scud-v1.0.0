package com.vkrauth.app.ui.ble

import android.content.Context
import android.util.Log
import androidx.annotation.StringRes
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.R
import com.vkrauth.app.ble.BleConstants
import com.vkrauth.app.ble.BleSession
import com.vkrauth.app.data.crypto.Blake2s
import com.vkrauth.app.data.crypto.Domains
import com.vkrauth.app.data.crypto.Ed25519
import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.data.local.dao.ContactHistoryDao
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.dao.ReaderDao
import com.vkrauth.app.data.local.entity.ContactHistoryEntity
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.repository.CourierRepository
import com.vkrauth.app.data.repository.ReportsRepository
import com.vkrauth.app.hce.CompletedOp
import com.vkrauth.app.hce.PreparedOperation
import com.vkrauth.app.hce.TapDecisionTree
import com.vkrauth.app.hce.TapLog
import com.vkrauth.app.hce.TapLogEntry
import com.vkrauth.app.hce.TapSession
import com.vkrauth.app.ui.common.ReaderTimeInfo
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Управляет жизненным циклом одного BLE-сеанса с ридером (shared §16.6).
 *
 * Логика (confirm-FIRST, как NFC-канал):
 *   1. prepare(macAddress, shortId) — БЕЗ подключения резолвит short_reader_id в
 *      ПОЛНЫЙ reader_id, перебирая кандидатов из ТРЁХ источников (активные ключи
 *      этого устройства ∪ скачанные pending-доставки ∪ pending-отзывы) тем же
 *      BLAKE2s-префиксом, что HomeViewModel. Сессию можно начать, если есть ключ
 *      ИЛИ доставка фильтра ИЛИ отзыв — показывается confirm-экран со сводкой
 *      того, что будет сделано (§16.8). Нет ничего → NothingToDo (pre-connect).
 *   2. confirmAndRun() — только после подтверждения юзером: открывает BleSession,
 *      ждёт INFO, верифицирует reader-signed INFO И сверяет info.reader_id с
 *      confirm-экраном (mismatch → abort: ответил другой ридер). Затем строит
 *      TapSession из INFO и гоняет ТОТ ЖЕ набор операций, что NFC-канал
 *      (decisionTree.buildOperations → лениво ACCESS / FILTER_UPDATE / FDI /
 *      REVOKE_KEY / GET_BLACKLIST / TIME_SYNC), зеркаля TapController.handleFetch.
 *   3. H3: на RES_OK passage_receipt (192 B) уже ПРИШИТ к хвосту verdict[42:234];
 *      ACCESS.onResult в decisionTree слайсит его в outgoing_reports (тот же
 *      контракт, что NFC/backend). Отдельного GET_PASSAGE_RECEIPT больше нет.
 *   4. После цикла коммитим (contactHistory.insert + session.commitPendingChanges),
 *      шлём CONTROL=END и закрываемся.
 *
 * H1: открытая BLE per-reader сессия — полнофункциональный канал: те же
 * FDI/TIME_SYNC/REVOKE_KEY/ACCESS гоняются по BLE тем же runOperation
 * write→await циклом, что и ACCESS (прошивка маршрутизирует их по обоим
 * транспортам). Relay-риск явно принят; confirm-экран — UX-мера (§16.8).
 *
 * BLE op-bytes контракт (см. BleSession.runOperation): на вход подаётся ПОЛНАЯ
 * операция, у которой inner_opcode — это БАЙТ 0 (как у рабочего ACCESS:
 * Serialization.buildAccessOperation возвращает [0x01||payload]). В отличие от
 * NFC, BLE НЕ выносит inner_opcode в отдельный байт фрейма — runOperation сам
 * добавляет лишь [op_seq] поверх и нарезает PDU. Все Serialization.build*Operation
 * (0x12/0x13/0x15) и сырые bytes (FDI 0x11, GET_BLACKLIST 0x14) уже несут
 * inner_opcode байтом 0, поэтому в runOperation идут как есть.
 */
@HiltViewModel
class BleSessionViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val issuedKeyDao: IssuedKeyDao,
    private val outgoingReportDao: OutgoingReportDao,
    private val readerDao: ReaderDao,
    private val keyManager: KeyManager,
    private val decisionTree: TapDecisionTree,
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val pendingRevokeDao: PendingRevokeIntentDao,
    private val contactHistoryDao: ContactHistoryDao,
    private val courierRepository: CourierRepository,
    private val reportsRepository: ReportsRepository,
    private val tapLog: TapLog,
    savedState: SavedStateHandle
) : ViewModel() {

    sealed interface UiState {
        data object Idle : UiState
        /**
         * Pre-connect confirm screen (§16.8). reader_id/name come from LOCAL trusted
         * data (resolved from the advertised short id), NOT from the reader — INFO
         * isn't fetched yet. hasKey/hasDelivery/hasRevoke summarise what the session
         * will DO (a session is initiable when ANY of the three is true). The
         * post-connect INFO reader_id match + signature verify (in confirmAndRun) is
         * the safety guard.
         */
        data class Confirm(
            val readerIdHex: String,
            val readerName: String?,
            val hasKey: Boolean,
            val hasDelivery: Boolean,
            val hasRevoke: Boolean
        ) : UiState
        data class Connecting(val address: String) : UiState
        /** Neither a key nor a pending delivery/revoke for the picked reader. */
        data class NothingToDo(
            val shortIdHex: String,
            val readerIdHex: String?
        ) : UiState
        data class Sending(@StringRes val statusRes: Int) : UiState
        /**
         * Door verdict is non-null only when an ACCESS op ran; opsCompleted counts
         * every executed op (delivery/revoke/etc.) so a key-less delivery/revoke
         * session still shows a meaningful summary.
         */
        data class Completed(
            val readerIdHex: String,
            val verdict: String?,
            val passageReceiptCaptured: Boolean,
            val opsCompleted: Int
        ) : UiState
        data class Failed(@StringRes val errorRes: Int, val errorArg: String? = null) : UiState
    }

    private val _state = MutableStateFlow<UiState>(UiState.Idle)
    val state: StateFlow<UiState> = _state.asStateFlow()

    /** Живой лог BLE-сеанса для on-screen панели (общий с NFC через TapLog). */
    val logEntries: StateFlow<List<TapLogEntry>> = tapLog.entries

    /**
     * Прогресс операций по мере выполнения — та же модель CompletedOp, что показывает
     * NFC-экран (TapScreen): пользователь видит, какие операции выполняются и с каким
     * результатом, в реальном времени по ходу сессии.
     */
    private val _ops = MutableStateFlow<List<CompletedOp>>(emptyList())
    val ops: StateFlow<List<CompletedOp>> = _ops.asStateFlow()

    /** Время ридера из INFO (§5.2) + drift к часам телефона — блок «Время ридера». */
    private val _readerTime = MutableStateFlow<ReaderTimeInfo?>(null)
    val readerTime: StateFlow<ReaderTimeInfo?> = _readerTime.asStateFlow()

    fun clearLogs() = tapLog.clear()

    private var session: BleSession? = null
    private var pendingAddress: String? = null

    /**
     * Pre-connect step (§16.8). Resolves the advertised short_reader_id to the FULL
     * reader_id WITHOUT touching the radio, then shows the confirm screen. The full
     * reader_id is not advertised — adv carries short_reader_id = BLAKE2s(reader_id,16)
     * [0:6], so we hash every candidate reader_id and match on the 6-byte prefix,
     * exactly like HomeViewModel.shortIdFor().
     *
     * Candidate reader_ids come from THREE sources (same DAOs/repos HomeViewModel uses
     * in refreshMatchSnapshot): active this-device keys ∪ pending downloaded deliveries
     * ∪ pending revokes. A session is initiable when there is a key OR a pending
     * delivery OR a pending revoke — not only when a key exists.
     */
    fun prepare(address: String, shortIdHex: String) {
        pendingAddress = address
        viewModelScope.launch {
            tapLog.info("BLE prepare: resolving short_reader_id=$shortIdHex (no connect)")
            val now = System.currentTimeMillis()

            val keyReaderIds = issuedKeyDao.observeAll().first()
                .filter {
                    it.status == IssuedKeyStatus.ACTIVE &&
                        it.belongsToThisDevice &&
                        it.expiresAtMs > now
                }
                .map { it.readerId }
                .toSet()
            val deliveryReaderIds = courierRepository.observePending().first()
                .filter { it.status == "downloaded" }
                .map { it.targetReaderId }
                .toSet()
            val revokeReaderIds = reportsRepository.observeRevokeIntents().first()
                .filter { it.status == "pending" }
                .map { it.targetReaderId }
                .toSet()

            // Resolve the full reader_id by matching the short id against EVERY
            // candidate (finds the reader even with no key).
            val readerIdHex = (keyReaderIds + deliveryReaderIds + revokeReaderIds)
                .firstOrNull { shortIdFor(it) == shortIdHex }

            if (readerIdHex == null) {
                tapLog.warn("BLE nothing pending for short_reader_id=$shortIdHex (no key/delivery/revoke)")
                _state.value = UiState.NothingToDo(shortIdHex = shortIdHex, readerIdHex = null)
                return@launch
            }

            val hasKey = readerIdHex in keyReaderIds
            val hasDelivery = readerIdHex in deliveryReaderIds
            val hasRevoke = readerIdHex in revokeReaderIds
            val readerName = readerDao.find(readerIdHex)?.displayName
            tapLog.info(
                "BLE reader resolved: ${readerIdHex.take(16)} name=${readerName ?: "?"} " +
                    "key=$hasKey delivery=$hasDelivery revoke=$hasRevoke → confirm"
            )
            _state.value = UiState.Confirm(
                readerIdHex = readerIdHex,
                readerName = readerName,
                hasKey = hasKey,
                hasDelivery = hasDelivery,
                hasRevoke = hasRevoke
            )
        }
    }

    /** short_reader_id (hex) advertised by a reader whose full reader_id is [readerIdHex]. */
    private fun shortIdFor(readerIdHex: String): String? = runCatching {
        val full = Serialization.hexToBytes(readerIdHex)
        Blake2s.compute(full, 128).copyOfRange(0, 6).toHex()
    }.getOrNull()

    /**
     * Confirm-triggered flow: connect → INFO verify + reader_id match → run the SAME
     * op set as NFC. Mirrors TapController exactly (H1: a full-capability channel):
     * we build a standalone TapSession from INFO, let decisionTree.buildOperations
     * emit the right queue for {key, delivery, revoke, time-sync}, and run each op via
     * BleSession.runOperation in the same framing the working ACCESS path used.
     *
     * §16.8 safety guard: the confirm screen showed reader_id/name from LOCAL trusted
     * data; here we additionally verify the reader-signed INFO signature AND assert
     * info.reader_id == the confirmed reader_id. A mismatch means a DIFFERENT reader
     * answered — we abort instead of proceeding. session.readerPubkey is set from the
     * known reader only when the INFO signature verifies (null otherwise) — mirroring
     * TapController's SIGNED_OPCODES skip.
     *
     * H3 fold-in: GET_PASSAGE_RECEIPT (0x16) is GONE. On RES_OK the 192-B
     * reader-signed passage_receipt is APPENDED to the ACCESS_VERDICT tail
     * (verdict[42:234]); the ACCESS onResult in TapDecisionTree slices it and enqueues
     * the SAME bare 192-B "passage_receipt" report uploaded today — byte-identical.
     */
    fun confirmAndRun() {
        val current = _state.value as? UiState.Confirm ?: return
        val address = pendingAddress ?: return
        viewModelScope.launch {
            _ops.value = emptyList()
            _state.value = UiState.Connecting(address)
            tapLog.info("BLE session: connecting to $address")
            try {
                val device = androidx.core.content.ContextCompat
                    .getSystemService(context, android.bluetooth.BluetoothManager::class.java)
                    ?.adapter?.getRemoteDevice(address)
                    ?: throw IllegalStateException("Bluetooth adapter unavailable")

                val s = BleSession(context, device, tapLog).also { session = it }
                val infoBytes = s.connect(timeoutMs = 12_000)
                val info = Serialization.parseInfo(infoBytes)
                _readerTime.value = ReaderTimeInfo.of(info.readerTime)

                // §16.8: reader_id берём из reader-signed INFO и ВЕРИФИЦИРУЕМ подпись
                // против известного pubkey ридера (как NFC-путь в TapController),
                // плюс сверяем с подтверждённым reader_id. Mismatch → ответил другой
                // ридер: НЕ продолжаем.
                val infoReaderIdHex = info.readerId.toHex()
                val known = readerDao.find(infoReaderIdHex)
                val infoVerified = known != null && Ed25519.verify(
                    known.readerPubkey,
                    Domains.INF + info.signedRange,
                    info.readerSignature
                )
                tapLog.info(
                    "BLE INFO parsed: reader=${infoReaderIdHex.take(16)} verified=$infoVerified"
                )
                if (infoReaderIdHex != current.readerIdHex) {
                    tapLog.error(
                        "BLE reader_id mismatch: confirmed=${current.readerIdHex.take(16)} " +
                            "answered=${infoReaderIdHex.take(16)} → abort"
                    )
                    _state.value = UiState.Failed(R.string.ble_session_error_reader_mismatch)
                    return@launch
                }

                // Build a standalone TapSession from INFO — same fields handlePushInfo
                // sets. readerPubkey is non-null ONLY when the INFO signature verified;
                // signed ops (ACCESS 0x01 / TIME_SYNC 0x12 / REVOKE_KEY 0x15) are
                // skipped when it is null, exactly like TapController.
                val tapSession = TapSession().apply {
                    readerId = info.readerId
                    readerTimeSec = info.readerTime
                    currentFreshNonce = info.freshNonce
                    maxApduSize = info.maxApduSize
                    filterVersion = info.filterVersion
                    blacklistCount = info.blacklistCount
                    sessionSeq = info.sessionSeq
                    readerPubkey = if (infoVerified) known?.readerPubkey else null
                }
                if (!infoVerified) {
                    tapLog.warn("BLE INFO not verified — signed ops (ACCESS/TIME_SYNC/REVOKE) will be skipped")
                }

                _state.value = UiState.Sending(R.string.ble_session_running_ops)
                val opsRun = runBleOperations(s, tapSession)

                // Commit AFTER the loop — same single commit point as TapController:
                // ContactHistory + pending filter deliveries/revokes/outgoing reports.
                contactHistoryDao.insert(
                    ContactHistoryEntity(
                        readerId = tapSession.readerId?.toHex() ?: current.readerIdHex,
                        occurredAtMs = System.currentTimeMillis(),
                        summary = tapSession.historyJson(),
                        verdict = tapSession.lastAccessVerdict
                    )
                )
                tapSession.commitPendingChanges(
                    pendingFilterDao, pendingRevokeDao, outgoingReportDao, issuedKeyDao
                )
                tapLog.info(
                    "BLE session committed: ops=$opsRun verdict=${tapSession.lastAccessVerdict ?: "-"} " +
                        "reports=${tapSession.pendingOutgoingReports.size}"
                )

                s.sendControl(BleConstants.CTL_END)
                _state.value = UiState.Completed(
                    readerIdHex = current.readerIdHex,
                    verdict = tapSession.lastAccessVerdict,
                    passageReceiptCaptured =
                        tapSession.pendingOutgoingReports.any { it.type == "passage_receipt" },
                    opsCompleted = opsRun
                )
            } catch (e: Exception) {
                tapLog.error("BLE session error: ${e.message}")
                _state.value = UiState.Failed(R.string.ble_session_error_connect, e.message)
            } finally {
                session?.close()
                session = null
            }
        }
    }

    /**
     * BLE run-loop — mirrors TapController.handleFetch op-by-op (just over GATT
     * instead of APDU FETCH/PUSH). Builds the queue via decisionTree.buildOperations,
     * then for each op:
     *   - lazy-build ACCESS (0x01) with session.currentFreshNonce + the freshest
     *     active key, else op.builder?.invoke(session) ?: op.bytes;
     *   - skip SIGNED_OPCODES (0x01/0x12/0x15) when session.readerPubkey == null;
     *   - feed [inner_opcode||payload] to runOperation (verified contract — the
     *     working ACCESS path fed buildAccessOperation's output, which is [0x01||…]);
     *   - dispatch op.onResult(resultBytes) and thread extractNextNonce into
     *     session.currentFreshNonce.
     * Returns the number of ops actually executed.
     */
    private suspend fun runBleOperations(s: BleSession, session: TapSession): Int {
        val allOps = decisionTree.buildOperations(session)
        val ops = allOps.filter { session.readerPubkey != null || it.innerOpcode !in SIGNED_OPCODES }
        val skipped = allOps.size - ops.size
        if (skipped > 0) {
            tapLog.warn("BLE пропущены signed-ops: $skipped (нет проверенного pubkey ридера)")
        }
        tapLog.info("BLE очередь операций: ${ops.size} [${ops.joinToString(",") { it.debugName }}]")

        var executed = 0
        for (op in ops) {
            // Lazy-build op bytes (same logic as TapController.handleFetch). null →
            // skip this op (no key / no nonce / build failure) — non-local continue is
            // unavailable on this Kotlin language version, so we signal via null.
            val opBytes = buildOpBytes(op, session) ?: continue

            // op-bytes contract: [inner_opcode||payload] fed AS-IS — runOperation only
            // prepends [op_seq] and chunks. No separate inner_opcode frame byte (NFC's
            // OP_SINGLE/OP_CHUNKED framing is APDU-only).
            val resultBytes = try {
                s.runOperation(opBytes)
            } catch (e: Exception) {
                Log.e(TAG, "BLE runOperation ${op.debugName} failed", e)
                tapLog.error("BLE ${op.debugName}: ${e.message}")
                continue
            }
            executed++

            try {
                op.onResult(resultBytes)
            } catch (e: Exception) {
                Log.e(TAG, "BLE onResult ${op.debugName} failed", e)
                tapLog.error("BLE onResult(${op.debugName}): ${e.message}")
            }
            session.operationLog.add("${op.debugName}=${resultBytes.size}B")
            // Публикуем выполненную операцию в прогресс (как NFC appendCompletedOp) —
            // экран показывает её сразу, ещё до конца сессии.
            _ops.update { it + completedOpFrom(op.debugName, resultBytes) }
            val nextNonce = Serialization.extractNextNonce(resultBytes)
            if (nextNonce != null) session.currentFreshNonce = nextNonce
        }
        return executed
    }

    /**
     * Lazy-builds the wire bytes for one queued op — same branching as
     * TapController.handleFetch. Returns null to mean "skip this op" (missing
     * key/nonce, empty builder output, or a build exception). Kept as a separate
     * function so it can `return null` cleanly (the run-loop turns that into a
     * `continue`), avoiding non-local jumps inside inline lambdas.
     */
    private suspend fun buildOpBytes(
        op: PreparedOperation,
        session: TapSession
    ): ByteArray? {
        if (op.innerOpcode == 0x01.toByte()) {
            val readerIdHex = session.readerId!!.toHex()
            val usable = issuedKeyDao.firstActiveForReader(
                readerIdHex, System.currentTimeMillis(), thisDevice = true
            )
            if (usable == null) {
                tapLog.warn("BLE нет активного ключа для ридера — ACCESS пропущен")
                return null
            }
            val nonce = session.currentFreshNonce
            if (nonce == null) {
                tapLog.warn("BLE нет fresh nonce — ACCESS пропущен")
                return null
            }
            return try {
                Serialization.buildAccessOperation(
                    issuedKey = usable.fullKeyBytes,
                    usedNonce = nonce,
                    readerTimeEcho = session.readerTimeSec,
                    readerId = session.readerId!!,
                    keyManager = keyManager
                )
            } catch (e: Exception) {
                Log.e(TAG, "BLE build ACCESS failed", e)
                tapLog.error("BLE сборка ACCESS: ${e.message}")
                null
            }
        }

        val builder = op.builder ?: return op.bytes
        val built = try {
            builder(session)
        } catch (e: Exception) {
            Log.e(TAG, "BLE build ${op.debugName} failed", e)
            tapLog.error("BLE сборка ${op.debugName}: ${e.message}")
            return null
        }
        if (built.isEmpty()) {
            tapLog.warn("BLE ${op.debugName}: builder вернул пустой payload — пропуск")
            return null
        }
        return built
    }

    /** Выполненная операция → строка прогресса (имя + читаемый результат + успех),
     *  по той же логике, что NFC (TapController.appendCompletedOp + markerName). */
    private fun completedOpFrom(name: String, result: ByteArray): CompletedOp {
        val resultName = markerName(result.firstOrNull() ?: 0)
        val success = Serialization.opResultSucceeded(result) ||
            (result.isNotEmpty() && result[0] == 0x81.toByte() && result.size >= 2 && result[1] == 0x00.toByte()) ||
            (result.isNotEmpty() && result[0] == 0x91.toByte()) ||
            (result.isNotEmpty() && result[0] == 0x94.toByte())
        return CompletedOp(name, resultName, success)
    }

    private fun markerName(marker: Byte): String = when (marker) {
        0x81.toByte() -> "ACCESS_VERDICT"
        0x91.toByte() -> "FDI"
        0x92.toByte() -> "TSYNC"
        0x93.toByte() -> "FILTER"
        0x94.toByte() -> "BLACKLIST"
        0x95.toByte() -> "REVOKE"
        else -> "0x%02X".format(marker.toInt() and 0xFF)
    }

    fun cancel() {
        session?.close()
        session = null
        _ops.value = emptyList()
        _readerTime.value = null
        _state.value = UiState.Idle
    }

    override fun onCleared() {
        cancel()
        super.onCleared()
    }

    private companion object {
        private const val TAG = "BleSessionViewModel"
        // Mirror TapController: ops whose payload is signed against a reader nonce —
        // meaningless without a verified reader pubkey, so they are skipped then.
        private val SIGNED_OPCODES = listOf<Byte>(0x01, 0x12, 0x15)
    }
}
