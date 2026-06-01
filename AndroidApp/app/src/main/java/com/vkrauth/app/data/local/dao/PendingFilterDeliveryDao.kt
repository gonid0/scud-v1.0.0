package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingFilterDeliveryDao {

    @Query(
        """
        SELECT * FROM pending_filter_deliveries
        WHERE targetReaderId = :readerIdHex AND status = :status
        ORDER BY filterVersion DESC
        LIMIT 1
        """
    )
    suspend fun firstFor(readerIdHex: String, status: String): PendingFilterDeliveryEntity?

    @Query("UPDATE pending_filter_deliveries SET status = 'delivered' WHERE deliveryId = :deliveryId")
    suspend fun markDelivered(deliveryId: String)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(delivery: PendingFilterDeliveryEntity)

    @Query("SELECT * FROM pending_filter_deliveries ORDER BY downloadedAtMs DESC")
    fun observeAll(): Flow<List<PendingFilterDeliveryEntity>>

    @Query("DELETE FROM pending_filter_deliveries WHERE deliveryId = :deliveryId")
    suspend fun deleteById(deliveryId: String)

    @Query("SELECT COUNT(*) FROM pending_filter_deliveries WHERE status = 'downloaded'")
    fun observePendingCount(): Flow<Int>
}
