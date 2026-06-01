package com.vkrauth.app.domain.usecase

import android.os.Build
import android.util.Base64
import com.vkrauth.app.BuildConfig
import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.local.entity.AccountEntity
import com.vkrauth.app.data.remote.dto.DeviceInfoDto
import com.vkrauth.app.data.remote.dto.LoginRequest
import com.vkrauth.app.data.remote.dto.RegisterDeviceRequest
import com.vkrauth.app.data.repository.AuthRepository
import javax.inject.Inject

class LoginUseCase @Inject constructor(
    private val authRepository: AuthRepository,
    private val keyManager: KeyManager
) {
    suspend operator fun invoke(
        domainInput: String,
        login: String,
        password: String
    ): Result<Unit> = runCatching {
        val normDomain = AuthRepository.normalizeDomain(domainInput)
        val baseUrl = AuthRepository.buildBaseUrlForDomain(normDomain)
        val api = authRepository.api(baseUrl)

        val loginResp = api.login(
            LoginRequest(
                login = login,
                password = password,
                deviceInfo = DeviceInfoDto(
                    model = Build.MODEL ?: "unknown",
                    os = "Android ${Build.VERSION.RELEASE ?: "unknown"}",
                    appVersion = BuildConfig.VERSION_NAME
                )
            )
        )

        val pubkey = if (!keyManager.hasKeyPair()) {
            keyManager.generateAndStore()
        } else {
            keyManager.getPublicKey()
        }
        val pubkeyB64 = Base64.encodeToString(pubkey, Base64.NO_WRAP)

        val entity = AccountEntity(
            accountId = AccountEntity.idFor(normDomain, login),
            domain = normDomain,
            login = login,
            userId = loginResp.userId,
            userGroupId = loginResp.userGroupId,
            displayName = loginResp.displayName,
            sessionToken = loginResp.sessionToken,
            refreshToken = loginResp.refreshToken,
            deviceId = null,
            phonePubkeyBase64 = pubkeyB64,
            isActive = true,
        )
        authRepository.saveAccount(entity, baseUrl)

        val regResp = api.registerDevice(
            RegisterDeviceRequest(
                phonePubkey = pubkeyB64,
                deviceLabel = "${Build.MODEL} (${Build.VERSION.RELEASE})"
            )
        )
        authRepository.updateDeviceId(regResp.deviceId)
    }
}
