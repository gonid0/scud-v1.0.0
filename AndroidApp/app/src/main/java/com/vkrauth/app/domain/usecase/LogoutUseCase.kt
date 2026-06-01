package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.local.ScudDatabase
import com.vkrauth.app.data.remote.AuthSnapshot
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import com.vkrauth.app.data.repository.AuthRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject

class LogoutUseCase @Inject constructor(
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val db: ScudDatabase,
    private val keyManager: KeyManager
) {
    /**
     * Выход из АКТИВНОГО аккаунта. Возвращает true, если аккаунтов больше не
     * осталось (нужно уйти на экран авторизации); false — если переключились на
     * другой сохранённый аккаунт и остаёмся в приложении.
     *
     * Локальные данные стираются в обоих случаях (таблицы не разделены по
     * аккаунтам). Ключевую пару устройства чистим только при полном выходе —
     * пока есть другие аккаунты, тот же phone_pubkey им ещё нужен.
     */
    suspend operator fun invoke(): Result<Boolean> = runCatching {
        currentAccount.get()?.let { snapshot ->
            try {
                apiFactory.create(snapshot.baseUrl).logout()
            } catch (_: Exception) {
                // best-effort: still clear local state
            }
        }
        val dao = db.accountDao()
        val active = dao.find()
        val remaining = dao.listAll().filter { it.accountId != active?.accountId }
        // clearAllTables() — блокирующий вызов; вне Room-suspend-транзакции.
        // Оставшиеся аккаунты вставляем заново после полной очистки.
        withContext(Dispatchers.IO) { db.clearAllTables() }
        remaining.forEachIndexed { idx, acc ->
            dao.upsert(acc.copy(isActive = idx == 0))
        }
        if (remaining.isEmpty()) {
            keyManager.clear()
            currentAccount.set(null)
            true
        } else {
            val target = remaining.first()
            val baseUrl = AuthRepository.buildBaseUrlForDomain(target.domain)
            currentAccount.set(
                AuthSnapshot(target.sessionToken, target.refreshToken, baseUrl)
            )
            false
        }
    }
}
