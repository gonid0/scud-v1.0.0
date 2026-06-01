package com.vkrauth.app.data.repository

import android.util.Base64
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.TimeGrantDao
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.data.local.entity.TimeGrantEntity
import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.local.dao.PermitDao
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import java.time.Instant
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class KeysRepository @Inject constructor(
    private val issuedKeyDao: IssuedKeyDao,
    private val timeGrantDao: TimeGrantDao,
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val keyManager: KeyManager,
    private val permitDao: PermitDao
) {
    fun observeAll(): Flow<List<IssuedKeyEntity>> = issuedKeyDao.observeAll()

    fun observeForPermit(permitId: String): Flow<List<IssuedKeyEntity>> =
        issuedKeyDao.findByPermit(permitId)

    suspend fun activeGrantForPermit(permitId: String, nowMs: Long = System.currentTimeMillis()) =
        timeGrantDao.firstActiveForPermit(permitId, nowMs)

    // Подтягивает актуальные ключи permit'а с сервера. Сервер возвращает
    // ключи ВСЕХ устройств пользователя — таким образом локальная БД получает
    // полный список (свои + чужие), а флаг belongsToThisDevice вычисляется
    // по embedded pubkey.
    suspend fun refreshAllPermits(): Int {
        val permits = permitDao.observeActive(System.currentTimeMillis()).first()
        var total = 0
        for (p in permits) {
            runCatching { refreshForPermit(p.permitId) }.onSuccess { total += it.size }
        }
        return total
    }

    fun observeActiveOwnCount(nowMs: Long): Flow<Int> =
        issuedKeyDao.observeActiveOwnCount(nowMs)

    suspend fun insert(entity: IssuedKeyEntity) = issuedKeyDao.insert(entity)

    suspend fun findByKeyId(keyIdHex: String): IssuedKeyEntity? =
        issuedKeyDao.findByKeyId(keyIdHex)

    suspend fun refreshForPermit(permitId: String): List<IssuedKeyEntity> {
        val snapshot = currentAccount.get() ?: return emptyList()
        val api = apiFactory.create(snapshot.baseUrl)
        val resp = api.permitKeys(permitId)
        val ownPub = if (keyManager.hasKeyPair()) keyManager.getPublicKey() else ByteArray(0)
        val entities = resp.items.map { dto ->
            val bytes = Base64.decode(dto.fullKeyBase64, Base64.NO_WRAP)
            val readerId = Serialization.extractReaderIdFromKey(bytes).toHex()
            val embeddedPub = bytes.copyOfRange(17, 49)
            IssuedKeyEntity(
                keyIdHex = dto.keyIdHex,
                permitId = permitId,
                readerId = readerId,
                issuedAtMs = Instant.parse(dto.issuedAt).toEpochMilli(),
                expiresAtMs = Instant.parse(dto.expiresAt).toEpochMilli(),
                fullKeyBytes = bytes,
                belongsToThisDevice = embeddedPub.contentEquals(ownPub),
                // Сервер возвращает status только для permit_keys. Любое не "active"
                // маппим в нашу внутреннюю классификацию; неизвестные значения
                // считаем активными (безопасный дефолт, тап сам примет решение).
                status = when (dto.status) {
                    "active"            -> IssuedKeyStatus.ACTIVE
                    "revoked_by_server" -> IssuedKeyStatus.REVOKED_BY_SERVER
                    "revoked_by_reader" -> IssuedKeyStatus.REVOKED_BY_READER
                    "revoked_in_bloom"  -> IssuedKeyStatus.REVOKED_IN_BLOOM
                    "expired"           -> IssuedKeyStatus.EXPIRED
                    else                -> IssuedKeyStatus.ACTIVE
                }
            )
        }
        issuedKeyDao.insertAll(entities)
        return entities
    }

    suspend fun insertGrant(grant: TimeGrantEntity) = timeGrantDao.insert(grant)

    // Server-side revoke + локально пометить ключ как revoked_by_server
    // (не удалять — пользователь должен видеть отозванный ключ в архиве).
    suspend fun revokeOnServer(keyIdHex: String) {
        val snapshot = currentAccount.get() ?: return
        val api = apiFactory.create(snapshot.baseUrl)
        api.revokeKeyOnServer(keyIdHex)
        issuedKeyDao.setStatus(keyIdHex, IssuedKeyStatus.REVOKED_BY_SERVER)
    }

    // Локально пометить ключ как revoked_by_reader (вызывается, когда
    // пользователь поставил в очередь revoke через ридер).
    suspend fun markRevokedLocally(keyIdHex: String, status: String = IssuedKeyStatus.REVOKED_BY_READER) {
        issuedKeyDao.setStatus(keyIdHex, status)
    }

    suspend fun markExpired(nowMs: Long = System.currentTimeMillis()): Int =
        issuedKeyDao.markExpired(nowMs)

    suspend fun deleteInactive(nowMs: Long = System.currentTimeMillis()): Int =
        issuedKeyDao.deleteInactive(nowMs)

    suspend fun deleteInactiveForPermit(permitId: String, nowMs: Long = System.currentTimeMillis()): Int =
        issuedKeyDao.deleteInactiveForPermit(permitId, nowMs)

    suspend fun deleteByIds(keyIds: List<String>) = issuedKeyDao.deleteByIds(keyIds)
}
