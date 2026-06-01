package com.vkrauth.app.ui.auth

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.R
import com.vkrauth.app.domain.usecase.LoginUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class AuthUiState(
    val domain: String = "",
    val login: String = "",
    val password: String = "",
    val loading: Boolean = false,
    // Локализуемая ошибка (errorRes) ИЛИ динамический текст от сервера (errorText).
    @StringRes val errorRes: Int? = null,
    val errorText: String? = null
)

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val loginUseCase: LoginUseCase
) : ViewModel() {

    private val _state = MutableStateFlow(AuthUiState())
    val state: StateFlow<AuthUiState> = _state.asStateFlow()

    fun onDomainChange(v: String) = _state.update { it.copy(domain = v, errorRes = null, errorText = null) }
    fun onLoginChange(v: String) = _state.update { it.copy(login = v, errorRes = null, errorText = null) }
    fun onPasswordChange(v: String) = _state.update { it.copy(password = v, errorRes = null, errorText = null) }

    fun submit(onSuccess: () -> Unit) {
        val s = _state.value
        if (s.domain.isBlank() || s.login.isBlank() || s.password.isBlank()) {
            _state.update { it.copy(errorRes = R.string.auth_empty_fields_error, errorText = null) }
            return
        }
        _state.update { it.copy(loading = true, errorRes = null, errorText = null) }
        viewModelScope.launch {
            val result = loginUseCase(s.domain, s.login, s.password)
            result.fold(
                onSuccess = {
                    _state.update { it.copy(loading = false) }
                    onSuccess()
                },
                onFailure = { t ->
                    val message = t.message
                    _state.update {
                        if (message != null) {
                            it.copy(loading = false, errorText = message, errorRes = null)
                        } else {
                            it.copy(loading = false, errorRes = R.string.auth_generic_error, errorText = null)
                        }
                    }
                }
            )
        }
    }
}

fun buildBaseUrl(input: String): String {
    val trimmed = input.trim().removeSuffix("/")
    val colonIdx = trimmed.lastIndexOf(':')
    val hasPort = colonIdx > 0 && trimmed.substring(colonIdx + 1).toIntOrNull() != null
    val prefix = if (hasPort) "http://" else "https://"
    return "$prefix$trimmed/"
}
