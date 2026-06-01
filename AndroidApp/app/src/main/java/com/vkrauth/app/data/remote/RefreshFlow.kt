package com.vkrauth.app.data.remote

import com.vkrauth.app.data.local.dao.AccountDao
import com.vkrauth.app.data.remote.dto.RefreshRequest
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

data class RefreshedTokens(val sessionToken: String, val refreshToken: String)

@Singleton
class RefreshFlow @Inject constructor(
    private val apiFactory: ScudApiFactory,
    private val accountDao: AccountDao,
    private val currentAccount: CurrentAccount
) {
    private val mutex = Mutex()

    suspend fun attemptRefresh(oldRefreshToken: String): RefreshedTokens? = mutex.withLock {
        val snapshot = currentAccount.get() ?: return null
        // Another caller may have already refreshed — use the latest token if it's different.
        if (snapshot.refreshToken != oldRefreshToken) {
            return RefreshedTokens(snapshot.sessionToken, snapshot.refreshToken)
        }
        return try {
            val api = apiFactory.create(snapshot.baseUrl)
            val resp = api.refresh(RefreshRequest(oldRefreshToken))
            currentAccount.updateTokens(resp.sessionToken, resp.refreshToken)
            accountDao.updateTokens(resp.sessionToken, resp.refreshToken)
            RefreshedTokens(resp.sessionToken, resp.refreshToken)
        } catch (_: Exception) {
            null
        }
    }
}
