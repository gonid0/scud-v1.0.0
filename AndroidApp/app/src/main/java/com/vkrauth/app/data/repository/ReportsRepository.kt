package com.vkrauth.app.data.repository

import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.entity.OutgoingReportEntity
import com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ReportsRepository @Inject constructor(
    private val outgoingReportDao: OutgoingReportDao,
    private val pendingRevokeIntentDao: PendingRevokeIntentDao
) {
    fun observeOutgoing(): Flow<List<OutgoingReportEntity>> = outgoingReportDao.observeAll()

    fun observeOutgoingCount(): Flow<Int> = outgoingReportDao.observeCount()

    fun observeRevokeIntents(): Flow<List<PendingRevokeIntentEntity>> = pendingRevokeIntentDao.observeAll()

    fun observeRevokeIntentsCount(): Flow<Int> = pendingRevokeIntentDao.observePendingCount()

    suspend fun insertRevokeIntent(intent: PendingRevokeIntentEntity): Long =
        pendingRevokeIntentDao.insert(intent)

    suspend fun deleteRevokeIntent(intentId: Long) = pendingRevokeIntentDao.deleteById(intentId)

    suspend fun saveReportDedup(report: OutgoingReportEntity) = outgoingReportDao.saveDedup(report)
}
