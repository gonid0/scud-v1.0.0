package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.data.repository.PermitsRepository
import javax.inject.Inject

class RefreshPermitsUseCase @Inject constructor(
    private val permitsRepository: PermitsRepository,
    private val authRepository: AuthRepository
) {
    suspend operator fun invoke(): Result<Int> = runCatching {
        val account = authRepository.currentAccount() ?: error("no account")
        permitsRepository.refresh(account.userId)
    }
}
