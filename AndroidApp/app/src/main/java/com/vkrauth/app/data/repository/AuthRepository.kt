package com.vkrauth.app.data.repository

import com.vkrauth.app.data.local.dao.AccountDao
import com.vkrauth.app.data.local.entity.AccountEntity
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.AuthSnapshot
import com.vkrauth.app.data.remote.ScudApiFactory
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthRepository @Inject constructor(
    private val accountDao: AccountDao,
    private val currentAccount: CurrentAccount,
    private val apiFactory: ScudApiFactory
) {
    fun observeAccount(): Flow<AccountEntity?> = accountDao.observe()

    fun observeAllAccounts(): Flow<List<AccountEntity>> = accountDao.observeAll()

    suspend fun currentAccount(): AccountEntity? = accountDao.find()

    suspend fun saveAccount(account: AccountEntity, baseUrl: String) {
        accountDao.upsert(account)
        // Новый/повторный вход делает аккаунт активным (сбрасывая остальные).
        accountDao.setActive(account.accountId)
        currentAccount.set(AuthSnapshot(account.sessionToken, account.refreshToken, baseUrl))
    }

    suspend fun restoreFromStorage() {
        val acc = accountDao.find() ?: return
        val baseUrl = buildBaseUrlForDomain(acc.domain)
        currentAccount.set(AuthSnapshot(acc.sessionToken, acc.refreshToken, baseUrl))
    }

    suspend fun ensureSnapshot(): AuthSnapshot? {
        currentAccount.get()?.let { return it }
        restoreFromStorage()
        return currentAccount.get()
    }

    suspend fun updateDeviceId(deviceId: String) = accountDao.updateDeviceId(deviceId)

    suspend fun clear() {
        accountDao.clear()
        currentAccount.set(null)
    }

    fun api(baseUrl: String) = apiFactory.create(baseUrl)

    companion object {
        /** Канонизация введённого домена (trim + без хвостового '/'). Используется и
         *  для baseUrl, и для accountId, чтобы 'example.com' и 'example.com/' не плодили
         *  два разных аккаунта на один и тот же сервер. */
        fun normalizeDomain(input: String): String = input.trim().removeSuffix("/")

        fun buildBaseUrlForDomain(input: String): String {
            val trimmed = normalizeDomain(input)
            val colonIdx = trimmed.lastIndexOf(':')
            val hasPort = colonIdx > 0 && trimmed.substring(colonIdx + 1).toIntOrNull() != null
            val prefix = if (hasPort) "http://" else "https://"
            return "$prefix$trimmed/"
        }
    }
}
