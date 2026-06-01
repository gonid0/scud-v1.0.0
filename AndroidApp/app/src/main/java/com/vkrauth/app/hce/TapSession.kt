package com.vkrauth.app.hce

import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.data.local.entity.OutgoingReportEntity

// PreparedOperation — одна операция в очереди.
//
// Если `builder != null`, байты операции строятся лениво в момент handleFetch —
// это позволяет вынести дорогие вещи (Android Keystore sign) из
// handlePushInfo, где блокировка NFC-треда чреват HCE-дропом.
// Иначе отдаются уже готовые `bytes`.
data class PreparedOperation(
    val innerOpcode: Byte,
    val bytes: ByteArray = EMPTY,
    val builder: ((TapSession) -> ByteArray)? = null,
    val onResult: (ByteArray) -> Unit = {},
    val debugName: String
) {
    companion object {
        private val EMPTY = ByteArray(0)
    }
}

class IncomingChunkBuffer(val total: Int) {
    val bytes = ByteArray(total)
    var filled = 0
    var lastSeen = System.currentTimeMillis()
}

class TapSession {
    val createdAt: Long = System.currentTimeMillis()
    var lastActivityAt: Long = System.currentTimeMillis()

    // true после того, как TapController.finishSessionAsync один раз запустил
    // сохранение всей накопленной телеметрии и pending-задач. Защищает от
    // двойного коммита (handleEnd + onDeactivated).
    @Volatile
    var committed: Boolean = false

    // Поднимается в handlePushInfo, если подпись не прошла и мы решили
    // запросить повтор через FETCH_ERROR=SESSION_LOST. На первом FETCH
    // сбрасывается и возвращается ошибка ридеру → он переотправит PUSH_INFO
    // с новым fresh_nonce, что очистит потенциальный RF-корапт.
    @Volatile
    var pushInfoSigRetryPending: Boolean = false

    var readerId: ByteArray? = null
    var readerPubkey: ByteArray? = null
    var readerTimeSec: Long = 0
    var currentFreshNonce: ByteArray? = null
    var maxApduSize: Int = 256
    var sessionSeq: Int = 0
    var filterVersion: Long = 0
    var blacklistCount: Int = 0

    val operationsQueue: ArrayDeque<PreparedOperation> = ArrayDeque()
    var lastSentOperation: PreparedOperation? = null

    val outgoingChunks: MutableMap<Int, ByteArray> = mutableMapOf()
    val incomingChunks: MutableMap<Int, IncomingChunkBuffer> = mutableMapOf()

    var lastAccessVerdict: String? = null

    val operationLog: MutableList<String> = mutableListOf()

    val pendingDeliveredIds: MutableList<String> = mutableListOf()
    val pendingDeliveredRevokes: MutableList<Long> = mutableListOf()
    // keyIdHex ключей, отзыв которых ридер ПОДТВЕРДИЛ в этой сессии. Локальная
    // пометка status=revoked_by_reader откладывается до commitPendingChanges,
    // чтобы ключ оставался валидным до фактического успеха REVOKE_KEY (он же
    // requester / ACCESS-ключ до момента отзыва).
    val pendingRevokedKeyIds: MutableList<String> = mutableListOf()
    val pendingOutgoingReports: MutableList<OutgoingReportEntity> = mutableListOf()

    fun touch() {
        lastActivityAt = System.currentTimeMillis()
    }

    fun historyJson(): String = buildString {
        append("{\"reader_id\":\"")
        append(readerId?.toHex() ?: "")
        append("\",\"ops\":[")
        append(operationLog.joinToString(",") { "\"$it\"" })
        append("]}")
    }

    suspend fun commitPendingChanges(
        pendingFilterDao: PendingFilterDeliveryDao,
        pendingRevokeDao: PendingRevokeIntentDao,
        outgoingReportDao: OutgoingReportDao,
        issuedKeyDao: IssuedKeyDao
    ) {
        pendingDeliveredIds.forEach { pendingFilterDao.markDelivered(it) }
        // REVOKE_KEY доставлено на ридер и принято (result ok) — задача полностью
        // отработала, запись больше не нужна, просто удаляем из локальной БД.
        pendingDeliveredRevokes.forEach { pendingRevokeDao.deleteById(it) }
        // Только теперь, по подтверждённому отзыву, помечаем ключ локально как
        // revoked_by_reader (до тапа он намеренно оставался active — см.
        // QueueRevokeOnReaderUseCase). После этого firstActiveForReader его не
        // выберет, а UI экрана ключей покажет «отозван через ридер».
        pendingRevokedKeyIds.forEach { issuedKeyDao.setStatus(it, IssuedKeyStatus.REVOKED_BY_READER) }
        pendingOutgoingReports.forEach { outgoingReportDao.saveDedup(it) }
    }
}
