package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.repository.KeysRepository
import javax.inject.Inject

class RevokeKeyOnServerUseCase @Inject constructor(
    private val keysRepository: KeysRepository
) {
    suspend operator fun invoke(keyIdHex: String): Result<Unit> = runCatching {
        keysRepository.revokeOnServer(keyIdHex)
        // NB: active_keys_count в /app/permits НЕ уменьшается на revoke_by_server —
        // ключ остаётся is_active=True до revoked_in_bloom (после применения FDI/receipt
        // на ридере). Это by design: отозванный сервером ключ физически принимается
        // ридером, пока не приедет новый bloom-фильтр. Refresh permits НЕ вызываем —
        // это только вызвало бы иллюзию «уже не активный» в UI.
    }
}
