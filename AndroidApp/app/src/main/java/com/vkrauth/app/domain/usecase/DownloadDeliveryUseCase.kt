package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity
import com.vkrauth.app.data.repository.CourierRepository
import javax.inject.Inject

class DownloadDeliveryUseCase @Inject constructor(
    private val courierRepository: CourierRepository
) {
    suspend operator fun invoke(deliveryId: String): Result<PendingFilterDeliveryEntity> = runCatching {
        courierRepository.download(deliveryId)
    }
}
