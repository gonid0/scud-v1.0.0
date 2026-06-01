package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import com.vkrauth.app.data.local.entity.OutgoingReportEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface OutgoingReportDao {

    @Transaction
    suspend fun saveDedup(report: OutgoingReportEntity) {
        when (report.type) {
            "filter_delivery_info", "blacklist_report" -> {
                deleteByTypeAndReader(report.type, report.targetReaderId)
            }
        }
        insert(report)
    }

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(report: OutgoingReportEntity)

    @Query("DELETE FROM outgoing_reports WHERE type = :type AND targetReaderId = :readerId")
    suspend fun deleteByTypeAndReader(type: String, readerId: String)

    @Query("SELECT * FROM outgoing_reports ORDER BY producedAtMs")
    fun observeAll(): Flow<List<OutgoingReportEntity>>

    @Query("SELECT * FROM outgoing_reports ORDER BY producedAtMs")
    suspend fun getAll(): List<OutgoingReportEntity>

    @Query("DELETE FROM outgoing_reports WHERE reportId IN (:ids)")
    suspend fun deleteByIds(ids: List<String>)

    @Query("UPDATE outgoing_reports SET retryCount = retryCount + 1 WHERE reportId IN (:ids)")
    suspend fun incrementRetryCount(ids: List<String>)

    @Query("UPDATE outgoing_reports SET retryCount = retryCount + 1")
    suspend fun incrementRetryCountAll()

    @Query("SELECT COUNT(*) FROM outgoing_reports")
    fun observeCount(): Flow<Int>
}
