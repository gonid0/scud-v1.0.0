package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingRevokeIntentDao {

    @Query(
        """
        SELECT * FROM pending_revoke_intents
        WHERE targetReaderId = :readerIdHex AND status = :status
        ORDER BY createdAtMs
        """
    )
    suspend fun forReader(readerIdHex: String, status: String): List<PendingRevokeIntentEntity>

    @Query("UPDATE pending_revoke_intents SET status = 'delivered' WHERE intentId = :intentId")
    suspend fun markDelivered(intentId: Long)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(intent: PendingRevokeIntentEntity): Long

    @Query("SELECT * FROM pending_revoke_intents ORDER BY createdAtMs DESC")
    fun observeAll(): Flow<List<PendingRevokeIntentEntity>>

    @Query("DELETE FROM pending_revoke_intents WHERE intentId = :intentId")
    suspend fun deleteById(intentId: Long)

    @Query("SELECT COUNT(*) FROM pending_revoke_intents WHERE status = 'pending'")
    fun observePendingCount(): Flow<Int>
}
