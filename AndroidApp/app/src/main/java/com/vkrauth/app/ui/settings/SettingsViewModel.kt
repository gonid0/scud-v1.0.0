package com.vkrauth.app.ui.settings

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.data.local.entity.AccountEntity
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.domain.usecase.LogoutUseCase
import com.vkrauth.app.domain.usecase.SwitchAccountUseCase
import com.vkrauth.app.locale.AppLanguage
import com.vkrauth.app.locale.LocaleManager
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val account: AccountEntity? = null,
    val accounts: List<AccountEntity> = emptyList(),
    val language: AppLanguage = AppLanguage.ENGLISH,
    val showConfirm: Boolean = false,
    val loggingOut: Boolean = false,
    val switching: Boolean = false,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    authRepository: AuthRepository,
    private val logoutUseCase: LogoutUseCase,
    private val switchAccountUseCase: SwitchAccountUseCase,
) : ViewModel() {

    private val local = MutableStateFlow(
        SettingsUiState(language = LocaleManager.current(appContext))
    )

    val state: StateFlow<SettingsUiState> = local.asStateFlow()

    init {
        viewModelScope.launch {
            authRepository.observeAccount().collect { account ->
                local.value = local.value.copy(account = account)
            }
        }
        viewModelScope.launch {
            authRepository.observeAllAccounts().collect { accounts ->
                local.value = local.value.copy(accounts = accounts)
            }
        }
    }

    /** Переключиться на сохранённый аккаунт (стирает локальные данные, ре-синк). */
    fun switchAccount(accountId: String) {
        if (accountId == local.value.account?.accountId) return
        if (local.value.switching) return
        local.value = local.value.copy(switching = true)
        viewModelScope.launch {
            switchAccountUseCase(accountId)
            local.value = local.value.copy(switching = false)
        }
    }

    /** Сохраняет выбранный язык. Применение происходит после Activity.recreate()
     *  (см. SettingsScreen) — attachBaseContext перечитает persist'нутое значение. */
    fun setLanguage(language: AppLanguage) {
        if (language == local.value.language) return
        LocaleManager.persist(appContext, language)
        local.value = local.value.copy(language = language)
    }

    fun askLogout() {
        local.value = local.value.copy(showConfirm = true)
    }

    fun cancelLogout() {
        local.value = local.value.copy(showConfirm = false)
    }

    /**
     * onFullyLoggedOut вызывается ТОЛЬКО если не осталось ни одного аккаунта
     * (нужно уйти на экран авторизации). Если переключились на другой аккаунт —
     * остаёмся в приложении, состояние обновится через observe.
     */
    fun confirmLogout(onFullyLoggedOut: () -> Unit) {
        local.value = local.value.copy(showConfirm = false, loggingOut = true)
        viewModelScope.launch {
            val fullyLoggedOut = logoutUseCase().getOrDefault(true)
            local.value = local.value.copy(loggingOut = false)
            if (fullyLoggedOut) onFullyLoggedOut()
        }
    }
}
