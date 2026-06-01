package com.vkrauth.app.data.repository

import com.vkrauth.app.data.local.dao.AccountDao
import com.vkrauth.app.data.local.dao.PermitDao
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.data.remote.AuthSnapshot
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import kotlinx.coroutines.flow.Flow
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PermitsRepository @Inject constructor(
    private val permitDao: PermitDao,
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val accountDao: AccountDao
) {
    fun observeActive(nowMs: Long): Flow<List<PermitEntity>> = permitDao.observeActive(nowMs)

    fun observeOne(permitId: String): Flow<PermitEntity?> = permitDao.observeOne(permitId)

    fun observeActiveCount(nowMs: Long): Flow<Int> = permitDao.observeActiveCount(nowMs)

    suspend fun refresh(userId: Int): Int {
        val snapshot = requireSnapshot()
        val api = apiFactory.create(snapshot.baseUrl)
        val resp = api.permits()
        val nowMs = System.currentTimeMillis()
        val entities = resp.items.map { dto ->
            PermitEntity(
                permitId = dto.permitId,
                userId = userId,
                readerId = dto.effectiveReaderId,
                displayName = dto.displayName,
                description = dto.description,
                validFromMs = parseServerTimeMs(dto.validFrom),
                validUntilMs = parseServerTimeMs(dto.validUntil),
                nParallel = dto.nParallel,
                maxTokenTtlSeconds = dto.maxTokenTtlSeconds,
                maxTotalIssued = dto.maxTotalIssued,
                activeKeysCount = dto.activeKeysCount,
                syncedAtMs = nowMs
            )
        }
        permitDao.replaceAll(entities)
        // NB: локальный recompute убран — он считал все не-истёкшие issued_keys без
        // учёта status, затирая корректное значение от сервера (сервер учитывает
        // revoked_by_server/revoked_by_reader). Source of truth = бэкенд, refresh
        // вызывается после любой мутации (issue/revoke).
        return entities.size
    }

    suspend fun revoke(permitId: String) {
        val snapshot = requireSnapshot()
        val api = apiFactory.create(snapshot.baseUrl)
        api.revokePermit(permitId)
    }

    private suspend fun requireSnapshot(): AuthSnapshot {
        currentAccount.get()?.let { return it }
        val acc = accountDao.find() ?: error("not authorized")
        val baseUrl = AuthRepository.buildBaseUrlForDomain(acc.domain)
        val snap = AuthSnapshot(acc.sessionToken, acc.refreshToken, baseUrl)
        currentAccount.set(snap)
        return snap
    }

    private fun parseServerTimeMs(s: String): Long = runCatching {
        Instant.parse(s).toEpochMilli()
    }.recoverCatching {
        OffsetDateTime.parse(s).toInstant().toEpochMilli()
    }.recoverCatching {
        LocalDateTime.parse(s).toInstant(ZoneOffset.UTC).toEpochMilli()
    }.getOrThrow()
}
