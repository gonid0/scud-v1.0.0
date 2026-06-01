package com.vkrauth.app.data.repository

import android.util.Base64
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import com.vkrauth.app.data.remote.dto.CourierAvailableItem
import com.vkrauth.app.data.remote.dto.DownloadRequest
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CourierRepository @Inject constructor(
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount
) {
    fun observePending(): Flow<List<PendingFilterDeliveryEntity>> = pendingFilterDao.observeAll()

    fun observePendingCount(): Flow<Int> = pendingFilterDao.observePendingCount()

    suspend fun fetchAvailable(): List<CourierAvailableItem> {
        val snapshot = currentAccount.get() ?: return emptyList()
        val api = apiFactory.create(snapshot.baseUrl)
        return api.courierAvailable().items
    }

    suspend fun download(deliveryId: String): PendingFilterDeliveryEntity {
        val snapshot = currentAccount.get() ?: error("no session")
        val api = apiFactory.create(snapshot.baseUrl)
        val resp = api.courierDownload(DownloadRequest(deliveryId))
        val entity = PendingFilterDeliveryEntity(
            deliveryId = resp.deliveryId,
            targetReaderId = resp.readerIdHex,
            filterVersion = resp.filterVersion,
            filterPackageBytes = Base64.decode(resp.filterPackageBase64, Base64.NO_WRAP),
            courierIdHex = resp.courierIdHex,
            downloadedAtMs = System.currentTimeMillis(),
            status = "downloaded"
        )
        pendingFilterDao.insert(entity)
        return entity
    }

    suspend fun forget(deliveryId: String) = pendingFilterDao.deleteById(deliveryId)
}
