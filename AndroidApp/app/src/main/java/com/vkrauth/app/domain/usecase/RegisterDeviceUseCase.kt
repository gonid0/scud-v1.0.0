package com.vkrauth.app.domain.usecase

import android.os.Build
import android.util.Base64
import com.vkrauth.app.data.crypto.KeyManager
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import com.vkrauth.app.data.remote.dto.RegisterDeviceRequest
import com.vkrauth.app.data.repository.AuthRepository
import javax.inject.Inject

class RegisterDeviceUseCase @Inject constructor(
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val authRepository: AuthRepository,
    private val keyManager: KeyManager
) {
    suspend operator fun invoke(): Result<String> = runCatching {
        val snapshot = currentAccount.get() ?: error("no session")
        val api = apiFactory.create(snapshot.baseUrl)
        val pubkey = keyManager.getPublicKey()
        val resp = api.registerDevice(
            RegisterDeviceRequest(
                phonePubkey = Base64.encodeToString(pubkey, Base64.NO_WRAP),
                deviceLabel = "${Build.MODEL} (${Build.VERSION.RELEASE})"
            )
        )
        authRepository.updateDeviceId(resp.deviceId)
        resp.deviceId
    }
}
