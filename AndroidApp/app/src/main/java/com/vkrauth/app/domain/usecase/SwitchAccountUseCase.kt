package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.local.ScudDatabase
import com.vkrauth.app.data.remote.AuthSnapshot
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.repository.AuthRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject

/**
 * Переключение активного аккаунта (мультиаккаунт «сессии без пароля»).
 *
 * Локальные таблицы данных (permits, issued_keys, deliveries, отчёты, …) НЕ
 * разделены по аккаунтам, поэтому при смене аккаунта мы стираем их и
 * полагаемся на повторную синхронизацию с сервера под новым токеном. Сами
 * аккаунты переживают clearAllTables: считываем их до очистки и вставляем
 * обратно, помечая target активным.
 *
 * Ключевая пара устройства (KeyManager) НЕ трогается — она общая для всех
 * аккаунтов на этом телефоне (один phone_pubkey зарегистрирован у сервера).
 */
class SwitchAccountUseCase @Inject constructor(
    private val db: ScudDatabase,
    private val currentAccount: CurrentAccount,
) {
    suspend operator fun invoke(accountId: String): Boolean {
        val dao = db.accountDao()
        val accounts = dao.listAll()
        val target = accounts.find { it.accountId == accountId } ?: return false
        if (target.isActive && accounts.size == 1) {
            // Уже активен и он единственный — ничего не делаем.
            return true
        }
        // clearAllTables() — блокирующий вызов; держим его вне Room-suspend-
        // транзакции (иначе assertNotSuspendingTransaction). Аккаунты вставляем
        // заново уже после очистки.
        withContext(Dispatchers.IO) { db.clearAllTables() }
        accounts.forEach { acc ->
            dao.upsert(acc.copy(isActive = acc.accountId == accountId))
        }
        val baseUrl = AuthRepository.buildBaseUrlForDomain(target.domain)
        currentAccount.set(
            AuthSnapshot(target.sessionToken, target.refreshToken, baseUrl)
        )
        return true
    }
}
