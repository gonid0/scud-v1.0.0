package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import com.vkrauth.app.data.local.entity.PermitEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PermitDao {
    @Query("SELECT * FROM permits WHERE validUntilMs > :nowMs ORDER BY validUntilMs")
    fun observeActive(nowMs: Long): Flow<List<PermitEntity>>

    @Query("SELECT * FROM permits WHERE permitId = :permitId")
    suspend fun find(permitId: String): PermitEntity?

    @Query("SELECT * FROM permits WHERE permitId = :permitId")
    fun observeOne(permitId: String): Flow<PermitEntity?>

    @Query(
        """
        UPDATE permits SET activeKeysCount = (
            SELECT COUNT(*) FROM issued_keys
            WHERE issued_keys.permitId = permits.permitId
              AND issued_keys.expiresAtMs > :nowMs
        )
        """
    )
    suspend fun recomputeActiveCounts(nowMs: Long)

    @Upsert
    suspend fun upsertAll(items: List<PermitEntity>)

    @Query("DELETE FROM permits WHERE permitId NOT IN (:keepIds)")
    suspend fun deleteMissing(keepIds: List<String>)

    @Query("SELECT COUNT(*) FROM permits WHERE validUntilMs > :nowMs")
    fun observeActiveCount(nowMs: Long): Flow<Int>

    @Transaction
    suspend fun replaceAll(items: List<PermitEntity>) {
        upsertAll(items)
        deleteMissing(items.map { it.permitId })
    }
}
