package com.vkrauth.app.hce

import android.util.Log
import com.vkrauth.app.R
import com.vkrauth.app.data.crypto.Domains
import com.vkrauth.app.data.crypto.Ed25519
import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.dao.ContactHistoryDao
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.dao.ReaderDao
import com.vkrauth.app.data.local.entity.ContactHistoryEntity
import com.vkrauth.app.di.ApplicationScope
import com.vkrauth.app.ui.common.HapticFeedback
import com.vkrauth.app.ui.common.ReaderTimeInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.random.Random

@Singleton
class TapController @Inject constructor(
    private val sessionHolder: TapSessionHolder,
    private val decisionTree: TapDecisionTree,
    private val keyManager: KeyManager,
    private val readerDao: ReaderDao,
    private val issuedKeyDao: IssuedKeyDao,
    private val contactHistoryDao: ContactHistoryDao,
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val pendingRevokeDao: PendingRevokeIntentDao,
    private val outgoingReportDao: OutgoingReportDao,
    private val tapLog: TapLog,
    private val haptic: HapticFeedback,
    @ApplicationScope private val scope: CoroutineScope
) {
    // N4: suspend-safe lock. APDU обрабатываются вне NFC binder-потока (корутина),
    // поэтому JVM-монитор synchronized заменён на kotlinx Mutex — его можно держать
    // через suspend-вызовы DAO/Keystore. Ридер шлёт APDU строго по одному (ждёт
    // ответ), так что contention минимальна; lock сериализует обработку APDU с
    // reset()/onDeactivated()/watchdog.
    private val amutex = Mutex()

    private val _uiState = MutableStateFlow<TapUiState>(TapUiState.Waiting)
    val uiState: StateFlow<TapUiState> = _uiState.asStateFlow()

    // true, пока внутренний экран Tap (TapScreen) виден на переднем плане. Ставится
    // из TapViewModel.startTapMode/stopTapMode. Когда true — TapNotifier НЕ всплывает
    // полноэкранным оверлеем (in-app экран уже показывает этапы сессии); иначе (фон
    // или другой экран приложения) оверлей нужен. Читается из TapNotifier.
    @Volatile
    var inAppTapUiVisible: Boolean = false

    // Время ридера из INFO (§5.2) + drift к часам телефона — для блока «Время ридера»
    // на экране Tap и в оверлее. Ставится в handlePushInfo, обнуляется в reset().
    private val _readerTime = MutableStateFlow<ReaderTimeInfo?>(null)
    val readerTime: StateFlow<ReaderTimeInfo?> = _readerTime.asStateFlow()

    @Volatile
    private var inactivityWatchdog: kotlinx.coroutines.Job? = null

    // Количество подряд неудачных проверок подписи PUSH_INFO от одного ридера.
    // При первых N фейлах мы выходим из сессии через SW_UNKNOWN — ридер
    // пришлёт PUSH_INFO заново на следующем SELECT_AID, и если предыдущий
    // FAIL был из-за RF-битов, свежие байты пройдут verify.
    @Volatile
    private var sigFailStreak = 0
    private val SIG_FAIL_RETRY_LIMIT = 2

    // reset() инициализирует экран. Если в данный момент идёт активная
    // сессия — НЕ прерываем её: второй вызов reset() может прилететь из
    // LaunchedEffect при рекомпозиции экрана и иначе отломал бы ongoing-tap.
    fun reset() {
        scope.launch {
            val proceeded = amutex.withLock {
                if (sessionHolder.current != null) {
                    // Активная сессия — защищаемся: не прерываем ongoing-tap
                    // (повторный reset() из LaunchedEffect при рекомпозиции).
                    tapLog.info("reset() пропущен: идёт активная сессия")
                    false
                } else {
                    inactivityWatchdog?.cancel()
                    inactivityWatchdog = null
                    sessionHolder.clear()
                    true
                }
            }
            if (proceeded) {
                tapLog.clear()
                tapLog.info("Ожидание READER…")
                _readerTime.value = null
                _uiState.value = TapUiState.Waiting
            }
        }
    }

    fun forceTimeout() {
        scope.launch {
            amutex.withLock {
                inactivityWatchdog?.cancel()
                inactivityWatchdog = null
                sessionHolder.clear()
            }
            tapLog.warn("Таймаут ожидания")
            _uiState.value = TapUiState.Failed(R.string.tap_time_expired)
        }
    }

    // N4: вызывается из ScudHceService.processCommandApdu. Не блокирует NFC
    // binder-поток — вся работа (DAO + Keystore) уходит в корутину, ответ
    // доставляется через `respond` (HostApduService.sendResponseApdu).
    fun handleApdu(apdu: ByteArray, respond: (ByteArray) -> Unit) {
        scope.launch(Dispatchers.IO) {
            val resp = try {
                amutex.withLock { handleApduInner(apdu) }
            } catch (e: Exception) {
                Log.e(TAG, "handleApdu failed", e)
                SW_UNKNOWN
            }
            respond(resp)
        }
    }

    private suspend fun handleApduInner(apdu: ByteArray): ByteArray {
        if (apdu.size < 4) {
            tapLog.warn("APDU слишком короткий: ${apdu.size}B")
            return SW_WRONG_LENGTH
        }

        val cla = apdu[0]
        val ins = apdu[1]

        if (cla == 0x00.toByte() && ins == 0xA4.toByte() && apdu[2] == 0x04.toByte()) {
            val session = sessionHolder.startNew()
            session.touch()
            tapLog.info("SELECT AID → новая сессия, APDU=${apdu.size}B")
            return SW_OK
        }

        val session = sessionHolder.current
        if (session == null) {
            tapLog.warn("APDU ins=0x%02X без сессии (len=${apdu.size}B) — отвечаю SESSION_LOST".format(ins.toInt() and 0xFF))
            // Returning explicit ERROR(SESSION_LOST) in a valid-looking FETCH envelope
            // so reader knows to restart; see shared §4.5.
            return if (ins == 0xC2.toByte()) {
                byteArrayOf(0x03, 0x02) + SW_OK
            } else {
                SW_REF_NOT_FOUND
            }
        }
        session.touch()

        return when (ins) {
            0xC1.toByte() -> handlePushInfo(session, apdu)
            0xC2.toByte() -> handleFetch(session, apdu)
            0xC3.toByte() -> handleReadChunk(session, apdu)
            0xC4.toByte() -> handlePushChunk(session, apdu)
            0xC5.toByte() -> handleEnd(session)
            else -> {
                tapLog.warn("Неизвестный INS 0x%02X (len=${apdu.size}B)".format(ins.toInt() and 0xFF))
                SW_UNKNOWN
            }
        }
    }

    fun onDeactivated(reason: Int) {
        val reasonName = when (reason) {
            0 -> "DEACTIVATION_LINK_LOSS"
            1 -> "DEACTIVATION_DESELECTED"
            else -> "UNKNOWN($reason)"
        }
        scope.launch {
            val snapshot: TapSession? = amutex.withLock {
                inactivityWatchdog?.cancel()
                inactivityWatchdog = null
                tapLog.info("Деактивация поля: $reasonName")
                val s = sessionHolder.current
                sessionHolder.clear()
                s
            }
            // Если handleEnd уже сделал финализацию — session.committed=true и мы выйдем
            // сразу. Иначе (аномальный обрыв, нет END от ридера) коммитим здесь.
            if (snapshot != null && !snapshot.committed) {
                val durationMs = System.currentTimeMillis() - snapshot.createdAt
                tapLog.info("Сессия закрыта без END: ${durationMs}мс, ops=${snapshot.operationLog.size} (${snapshot.operationLog.joinToString(",")})")
                publishCompleted(snapshot, finalMessageOverrideRes = null)
                finishSessionAsync(snapshot, origin = "onDeactivated")
            }
        }
    }

    // Единая точка сохранения всего, что накопилось за сессию:
    // - ContactHistory
    // - pending filter deliveries (markDelivered)
    // - pending revoke intents (delete)
    // - outgoing reports (dedup save, включая blacklist_report, filter_delivery_info)
    // Должна вызываться ровно один раз на сессию (guard через session.committed).
    private fun finishSessionAsync(session: TapSession, origin: String) {
        if (session.committed) return
        session.committed = true
        scope.launch(Dispatchers.IO) {
            try {
                contactHistoryDao.insert(
                    ContactHistoryEntity(
                        readerId = session.readerId?.toHex() ?: "unknown",
                        occurredAtMs = System.currentTimeMillis(),
                        summary = session.historyJson(),
                        verdict = session.lastAccessVerdict
                    )
                )
                val pfCount = session.pendingDeliveredIds.size
                val prCount = session.pendingDeliveredRevokes.size
                val orCount = session.pendingOutgoingReports.size
                session.commitPendingChanges(pendingFilterDao, pendingRevokeDao, outgoingReportDao, issuedKeyDao)
                if (pfCount + prCount + orCount > 0) {
                    tapLog.info("Сохранено ($origin): filters=$pfCount revokes=$prCount reports=$orCount")
                } else {
                    tapLog.info("Нечего сохранять ($origin)")
                }
            } catch (e: Exception) {
                Log.e(TAG, "commitPendingChanges failed", e)
                tapLog.error("Ошибка commitPending: ${e.message}")
            }
        }
    }

    private suspend fun handlePushInfo(session: TapSession, apdu: ByteArray): ByteArray {
        if (apdu.size < 5 + 146) {
            tapLog.warn("PUSH_INFO: короткий APDU ${apdu.size}B, ожидалось ≥151")
            return SW_WRONG_LENGTH
        }
        val lc = apdu[4].toInt() and 0xFF
        if (lc != 146) {
            tapLog.warn("PUSH_INFO: Lc=$lc, ожидалось 146")
            return SW_WRONG_LENGTH
        }
        val infoBytes = apdu.copyOfRange(5, 5 + 146)

        val info = try {
            Serialization.parseInfo(infoBytes)
        } catch (e: Exception) {
            tapLog.error("PUSH_INFO parse: ${e.message}")
            return SW_WRONG_LENGTH
        }

        session.readerId = info.readerId
        session.readerTimeSec = info.readerTime
        _readerTime.value = ReaderTimeInfo.of(info.readerTime)
        session.currentFreshNonce = info.freshNonce
        session.maxApduSize = info.maxApduSize
        session.filterVersion = info.filterVersion
        session.blacklistCount = info.blacklistCount
        session.sessionSeq = info.sessionSeq

        val readerIdHex = info.readerId.toHex()
        val nowSec = System.currentTimeMillis() / 1000
        val driftSec = nowSec - info.readerTime
        tapLog.recv("PUSH_INFO reader=${readerIdHex.take(12)}… seq=${info.sessionSeq} filterV=${info.filterVersion} blacklist=${info.blacklistCount} mtu=${info.maxApduSize} drift=${driftSec}s")
        val readerKnown = readerDao.find(readerIdHex)
        if (readerKnown != null) {
            val signedPayload = Domains.INF + info.signedRange
            val ok = Ed25519.verify(readerKnown.readerPubkey, signedPayload, info.readerSignature)
            if (ok) {
                if (sigFailStreak > 0) tapLog.info("Подпись OK после ${sigFailStreak} retry")
                sigFailStreak = 0
                session.readerPubkey = readerKnown.readerPubkey
                tapLog.info("Ридер «${readerKnown.displayName}» [${readerKnown.readerGroupId.take(8)}…], подпись OK")
            } else {
                sigFailStreak++
                if (sigFailStreak <= SIG_FAIL_RETRY_LIMIT) {
                    // RF-корапт в signed-диапазоне. Принимаем PUSH_INFO (SW_OK),
                    // но на первом FETCH вернём FETCH_ERROR=SESSION_LOST — ридер
                    // переотправит PUSH_INFO с новым fresh_nonce и другим
                    // подписанным payload'ом. Если предыдущий FAIL был из-за
                    // битов в эфире, повторная передача пройдёт verify.
                    tapLog.warn("Подпись FAIL #${sigFailStreak} — запрошу SESSION_LOST на след. FETCH")
                    session.pushInfoSigRetryPending = true
                    session.readerPubkey = null
                    // Не строим очередь — она будет построена после успешного
                    // повторного PUSH_INFO.
                    _uiState.value = TapUiState.InProgress(
                        readerName = readerKnown.displayName,
                        completedOps = emptyList()
                    )
                    armInactivityWatchdog(session)
                    return SW_OK
                }
                tapLog.error("Подпись FAIL #${sigFailStreak} — отказ от retry, signed-ops пропускаются")
                session.readerPubkey = null
            }
        } else {
            session.readerPubkey = null
            tapLog.warn("Ридер $readerIdHex неизвестен — signed-операции будут пропущены")
        }

        run {
            val allOps = decisionTree.buildOperations(session)
            val skippedSigned = allOps.filter { session.readerPubkey == null && it.innerOpcode in SIGNED_OPCODES }
            val ops = allOps.filter { session.readerPubkey != null || it.innerOpcode !in SIGNED_OPCODES }
            session.operationsQueue.addAll(ops)
            if (skippedSigned.isNotEmpty()) {
                tapLog.warn("Пропущены signed-ops: ${skippedSigned.joinToString(",") { it.debugName }}")
            }
            tapLog.info("Очередь операций: ${ops.size} [${ops.joinToString(",") {
                val sizeLabel = if (it.builder != null) "lazy" else "${it.bytes.size}B"
                "${it.debugName}($sizeLabel)"
            }}]")
        }

        _uiState.value = TapUiState.InProgress(
            readerName = readerKnown?.displayName,
            completedOps = emptyList()
        )
        armInactivityWatchdog(session)

        return SW_OK
    }

    private fun armInactivityWatchdog(session: TapSession) {
        inactivityWatchdog?.cancel()
        inactivityWatchdog = scope.launch(Dispatchers.Default) {
            while (true) {
                kotlinx.coroutines.delay(1000L)
                val sessionNow = amutex.withLock { sessionHolder.current }
                if (sessionNow !== session) return@launch
                val idleMs = System.currentTimeMillis() - session.lastActivityAt
                if (idleMs > INACTIVITY_TIMEOUT_MS) {
                    tapLog.warn("Ридер не отвечает ${idleMs / 1000}с → сессия прервана")
                    amutex.withLock {
                        if (sessionHolder.current === session) sessionHolder.clear()
                    }
                    _uiState.value = TapUiState.Failed(R.string.tap_connection_lost)
                    return@launch
                }
            }
        }
    }

    private suspend fun handleFetch(session: TapSession, apdu: ByteArray): ByteArray {
        // Если предыдущий PUSH_INFO не прошёл verify — просим ридера
        // переотправить его (FETCH_STATUS_ERROR=0x03, reason=SESSION_LOST=0x02).
        // Reader очистит prev_result и шлёт свежий PUSH_INFO с новым nonce.
        if (session.pushInfoSigRetryPending) {
            session.pushInfoSigRetryPending = false
            tapLog.info("FETCH → запрашиваем повтор PUSH_INFO (SESSION_LOST)")
            return buildResponse(byteArrayOf(0x03, 0x02), SW_OK)
        }

        if (apdu.size < 5) return SW_WRONG_LENGTH
        val lc = apdu[4].toInt() and 0xFF
        if (apdu.size < 5 + lc) return SW_WRONG_LENGTH
        val data = apdu.copyOfRange(5, 5 + lc)

        if (data.size < 2) return SW_WRONG_LENGTH
        val b0 = data[0].toInt() and 0xFF
        val b1 = data[1].toInt() and 0xFF

        val prevResultBytes: ByteArray? = when {
            b0 == 0 && b1 == 0 -> null
            b0 == 0xFF && b1 == 0xFF -> {
                if (data.size < 6) return SW_WRONG_LENGTH
                val msgId = ByteBuffer.wrap(data, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
                val buf = session.incomingChunks[msgId]
                if (buf == null || buf.filled != buf.total) return SW_REF_NOT_FOUND
                session.incomingChunks.remove(msgId)
                buf.bytes
            }
            else -> {
                val len = b0 or (b1 shl 8)
                if (data.size < 2 + len) return SW_WRONG_LENGTH
                data.copyOfRange(2, 2 + len)
            }
        }

        if (prevResultBytes != null) {
            val prevOp = session.lastSentOperation
            if (prevOp != null) {
                try {
                    prevOp.onResult(prevResultBytes)
                } catch (e: Exception) {
                    Log.e(TAG, "onResult callback failed", e)
                    tapLog.error("onResult(${prevOp.debugName}): ${e.message}")
                }
                val resultName = if (prevResultBytes.isNotEmpty()) {
                    markerName(prevResultBytes[0])
                } else "?"
                // Смещение кода результата зависит от структуры ответа:
                //   ACCESS_VERDICT (marker 0x81): marker(1) result(1) reader_time(8)… → код в [1];
                //   OP_RESULT-конверт (0x9x):     marker(1) in_reply_to(1) code(1) …  → код в [2].
                // Раньше всегда читали [2] → для ACCESS показывало мусор (байт reader_time).
                val resultCode = when {
                    prevResultBytes.isEmpty() -> ""
                    prevResultBytes[0] == 0x81.toByte() && prevResultBytes.size >= 2 ->
                        "code=0x%02X".format(prevResultBytes[1].toInt() and 0xFF)
                    prevResultBytes.size >= 3 ->
                        "code=0x%02X".format(prevResultBytes[2].toInt() and 0xFF)
                    else -> ""
                }
                session.operationLog.add("${prevOp.debugName}=$resultName")
                appendCompletedOp(prevOp.debugName, resultName, prevResultBytes)
                tapLog.recv("${prevOp.debugName} → $resultName ${resultCode} (${prevResultBytes.size}B)")
            } else {
                tapLog.warn("Получен prev_result (${prevResultBytes.size}B), но lastSentOperation=null")
            }
            val nextNonce = Serialization.extractNextNonce(prevResultBytes)
            if (nextNonce != null) session.currentFreshNonce = nextNonce
        }
        session.lastSentOperation = null

        val op = session.operationsQueue.removeFirstOrNull()
            ?: run {
                tapLog.info("Очередь пуста → END (выполнено ${session.operationLog.size} op)")
                publishCompleted(session, finalMessageOverrideRes = null)
                return buildResponse(byteArrayOf(0x00), SW_OK)
            }

        val opBytes: ByteArray = when (op.innerOpcode) {
            0x01.toByte() -> {
                // Lazy-build ACCESS with current nonce and freshest key.
                val readerIdHex = session.readerId!!.toHex()
                val usable = issuedKeyDao.firstActiveForReader(
                    readerIdHex,
                    System.currentTimeMillis(),
                    thisDevice = true
                ) ?: run {
                    tapLog.warn("Нет активного ключа для ридера")
                    return buildResponse(byteArrayOf(0x03, 0x01), SW_OK)
                }
                val nonce = session.currentFreshNonce
                    ?: run {
                        tapLog.warn("Нет fresh nonce")
                        return buildResponse(byteArrayOf(0x03, 0x01), SW_OK)
                    }
                try {
                    Serialization.buildAccessOperation(
                        issuedKey = usable.fullKeyBytes,
                        usedNonce = nonce,
                        readerTimeEcho = session.readerTimeSec,
                        readerId = session.readerId!!,
                        keyManager = keyManager
                    )
                } catch (e: Exception) {
                    Log.e(TAG, "build ACCESS failed", e)
                    tapLog.error("Сборка ACCESS: ${e.message}")
                    return buildResponse(byteArrayOf(0x03, 0x03), SW_OK)
                }
            }
            else -> {
                // Если у операции задан builder — строим лениво (AndroidKeyStore
                // вызовы вынесены сюда из buildOperations, чтобы ответ на PUSH_INFO
                // возвращался быстро).
                val builder = op.builder
                if (builder != null) {
                    try {
                        builder(session).also { built ->
                            if (built.isEmpty()) {
                                tapLog.warn("${op.debugName}: builder вернул пустой payload")
                                return buildResponse(byteArrayOf(0x03, 0x01), SW_OK)
                            }
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "build ${op.debugName} failed", e)
                        tapLog.error("Сборка ${op.debugName}: ${e.message}")
                        return buildResponse(byteArrayOf(0x03, 0x03), SW_OK)
                    }
                } else op.bytes
            }
        }

        session.lastSentOperation = op
        tapLog.send("OP ${op.debugName} (${opBytes.size}B)")

        val mtu = session.maxApduSize
        val singleBudget = mtu - 2 // reserve for SW
        return if (opBytes.size + 4 <= singleBudget) {
            val buf = ByteBuffer.allocate(4 + opBytes.size).order(ByteOrder.LITTLE_ENDIAN)
            buf.put(0x01)
            buf.put(op.innerOpcode)
            buf.putShort(opBytes.size.toShort())
            buf.put(opBytes)
            buildResponse(buf.array(), SW_OK)
        } else {
            val firstChunkMax = singleBudget - 12
            val msgId = Random.nextInt()
            session.outgoingChunks[msgId] = opBytes
            val firstChunkLen = minOf(opBytes.size, firstChunkMax)
            val buf = ByteBuffer.allocate(12 + firstChunkLen).order(ByteOrder.LITTLE_ENDIAN)
            buf.put(0x02)
            buf.put(op.innerOpcode)
            buf.putInt(msgId)
            buf.putInt(opBytes.size)
            buf.putShort(firstChunkLen.toShort())
            buf.put(opBytes, 0, firstChunkLen)
            tapLog.info("OP ${op.debugName} chunked: msgId=0x%08X total=${opBytes.size}B chunk=${firstChunkLen}B".format(msgId))
            buildResponse(buf.array(), SW_OK)
        }
    }

    private fun handleReadChunk(session: TapSession, apdu: ByteArray): ByteArray {
        if (apdu.size < 5 + 10) return SW_WRONG_LENGTH
        val data = apdu.copyOfRange(5, 15)
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        val msgId = buf.int
        val offset = buf.int
        val maxLen = buf.short.toInt() and 0xFFFF

        val fullBytes = session.outgoingChunks[msgId] ?: run {
            tapLog.warn("READ_CHUNK: msgId=0x%08X не найден".format(msgId))
            return SW_REF_NOT_FOUND
        }
        if (offset < 0 || offset > fullBytes.size) {
            tapLog.warn("READ_CHUNK: offset=$offset вне диапазона [0..${fullBytes.size}]")
            return SW_WRONG_LENGTH
        }
        val remaining = fullBytes.size - offset

        val chunkLen = minOf(remaining, maxLen)
        val isLast = chunkLen == remaining
        val flags = if (isLast) 0x01.toByte() else 0x00.toByte()

        val out = ByteBuffer.allocate(3 + chunkLen).order(ByteOrder.LITTLE_ENDIAN)
        out.putShort(chunkLen.toShort())
        out.put(flags)
        out.put(fullBytes, offset, chunkLen)

        tapLog.send("READ_CHUNK msgId=0x%08X off=$offset len=${chunkLen}B${if (isLast) " [last]" else ""}".format(msgId))

        if (isLast) {
            session.outgoingChunks.remove(msgId)
        }

        return buildResponse(out.array(), SW_OK)
    }

    private fun handlePushChunk(session: TapSession, apdu: ByteArray): ByteArray {
        if (apdu.size < 5 + 15) return SW_WRONG_LENGTH
        val lc = apdu[4].toInt() and 0xFF
        if (apdu.size < 5 + lc) return SW_WRONG_LENGTH
        val data = apdu.copyOfRange(5, 5 + lc)
        if (data.size < 15) return SW_WRONG_LENGTH

        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        val msgId = buf.int
        val offset = buf.int
        val total = buf.int
        val flags = buf.get()
        val chunkLen = buf.short.toInt() and 0xFFFF
        if (chunkLen > data.size - 15) return SW_WRONG_LENGTH
        val chunkBytes = ByteArray(chunkLen).also { buf.get(it) }

        val buffer = session.incomingChunks.getOrPut(msgId) { IncomingChunkBuffer(total) }
        if (buffer.total != total || offset + chunkLen > total) {
            tapLog.warn("PUSH_CHUNK: размер не сходится (total=$total vs ${buffer.total}, off=$offset+$chunkLen>$total)")
            return SW_WRONG_LENGTH
        }

        System.arraycopy(chunkBytes, 0, buffer.bytes, offset, chunkLen)
        buffer.filled += chunkLen
        buffer.lastSeen = System.currentTimeMillis()

        val isLast = (flags.toInt() and 0x01) != 0
        tapLog.recv("PUSH_CHUNK msgId=0x%08X off=$offset len=${chunkLen}B ${buffer.filled}/${buffer.total}${if (isLast) " [last]" else ""}".format(msgId))

        // Enforce max 2 active incoming buffers (shared §4.7).
        if (session.incomingChunks.size > MAX_INCOMING_BUFFERS) {
            val oldest = session.incomingChunks.entries.minByOrNull { it.value.lastSeen }
            if (oldest != null && oldest.key != msgId) {
                tapLog.warn("PUSH_CHUNK: выкинут старый incoming 0x%08X (превышен MAX_INCOMING_BUFFERS)".format(oldest.key))
                session.incomingChunks.remove(oldest.key)
            }
        }

        return SW_OK
    }

    private fun handleEnd(session: TapSession): ByteArray {
        val durationMs = System.currentTimeMillis() - session.createdAt
        tapLog.info("END от READER (сессия ${durationMs}мс, ${session.operationLog.size} op)")
        publishCompleted(session, finalMessageOverrideRes = R.string.tap_session_completed)
        // Commit BEFORE clearing the session — иначе onDeactivated увидит null snapshot
        // и накопленные отчёты (FDI, BLK, REVOKE_KEY ack) потеряются.
        finishSessionAsync(session, origin = "END")
        inactivityWatchdog?.cancel()
        inactivityWatchdog = null
        sessionHolder.clear()
        return SW_OK
    }

    private fun publishCompleted(session: TapSession, finalMessageOverrideRes: Int?) {
        val cur = _uiState.value
        val readerName = when (cur) {
            is TapUiState.InProgress -> cur.readerName
            is TapUiState.Completed -> cur.readerName
            else -> null
        }
        val completed = when (cur) {
            is TapUiState.InProgress -> cur.completedOps
            is TapUiState.Completed -> cur.completedOps
            else -> emptyList()
        }
        val verdict = session.lastAccessVerdict
        val success = verdict == "OK" || (verdict == null && completed.any { it.success })
        // Сообщение моделируется как @StringRes + опциональный динамический аргумент
        // (verdict для tap_denied), чтобы Composable отрисовал его в нужной локали.
        val finalRes: Int
        val finalArg: String?
        when {
            finalMessageOverrideRes != null -> { finalRes = finalMessageOverrideRes; finalArg = null }
            verdict == "OK" -> { finalRes = R.string.tap_door_opened; finalArg = null }
            verdict != null -> { finalRes = R.string.tap_denied; finalArg = verdict }
            completed.isNotEmpty() -> { finalRes = R.string.tap_done; finalArg = null }
            else -> { finalRes = R.string.tap_session_completed; finalArg = null }
        }
        _uiState.value = TapUiState.Completed(
            readerName = readerName,
            completedOps = completed,
            finalMessageRes = finalRes,
            finalMessageArg = finalArg,
            success = success
        )
        // Haptic+sound feedback в момент verdict — критично для UX, потому что
        // в этот момент юзер часто НЕ смотрит на экран (телефон у двери).
        when {
            verdict == "OK" -> haptic.granted()
            verdict != null -> haptic.denied()
            else -> haptic.neutral()
        }
    }

    private fun appendCompletedOp(name: String, resultName: String, rawResult: ByteArray) {
        val success = Serialization.opResultSucceeded(rawResult) ||
                (rawResult.isNotEmpty() && rawResult[0] == 0x81.toByte() && rawResult.size >= 2 && rawResult[1] == 0x00.toByte()) ||
                (rawResult.isNotEmpty() && rawResult[0] == 0x91.toByte()) ||
                (rawResult.isNotEmpty() && rawResult[0] == 0x94.toByte())
        val cur = _uiState.value
        val baseReader = when (cur) {
            is TapUiState.InProgress -> cur.readerName
            is TapUiState.Completed -> cur.readerName
            else -> null
        }
        val baseOps = when (cur) {
            is TapUiState.InProgress -> cur.completedOps
            is TapUiState.Completed -> cur.completedOps
            else -> emptyList()
        }
        _uiState.value = TapUiState.InProgress(
            readerName = baseReader,
            completedOps = baseOps + CompletedOp(name, resultName, success)
        )
    }

    private fun markerName(marker: Byte): String = when (marker) {
        0x81.toByte() -> "ACCESS_VERDICT"
        0x91.toByte() -> "FDI"
        0x92.toByte() -> "TSYNC_RESULT"
        0x93.toByte() -> "FLT_RESULT"
        0x94.toByte() -> "BLK"
        0x95.toByte() -> "REV_RESULT"
        else -> "0x%02X".format(marker.toInt() and 0xFF)
    }

    private fun buildResponse(payload: ByteArray, sw: ByteArray): ByteArray {
        return payload + sw
    }

    companion object {
        private const val TAG = "TapController"
        private const val MAX_INCOMING_BUFFERS = 2
        private const val INACTIVITY_TIMEOUT_MS = 5_000L
        private val SIGNED_OPCODES = listOf<Byte>(0x01, 0x12, 0x15)

        val SW_OK = byteArrayOf(0x90.toByte(), 0x00)
        val SW_WRONG_LENGTH = byteArrayOf(0x67.toByte(), 0x00)
        val SW_REF_NOT_FOUND = byteArrayOf(0x6A.toByte(), 0x88.toByte())
        val SW_UNKNOWN = byteArrayOf(0x6F.toByte(), 0x00)
    }
}
