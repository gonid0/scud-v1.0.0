package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.vkrauth.app.data.local.entity.TimeGrantEntity

@Dao
interface TimeGrantDao {

    @Query(
        """
        SELECT * FROM time_grants
        WHERE readerId = :readerIdHex AND expiresAtMs > :nowMs
        ORDER BY expiresAtMs DESC
        LIMIT 1
        """
    )
    suspend fun firstActiveForReader(readerIdHex: String, nowMs: Long): TimeGrantEntity?

    @Query("SELECT * FROM time_grants WHERE permitId = :permitId AND expiresAtMs > :nowMs LIMIT 1")
    suspend fun firstActiveForPermit(permitId: String, nowMs: Long): TimeGrantEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(grant: TimeGrantEntity)

    @Query("DELETE FROM time_grants WHERE expiresAtMs <= :nowMs")
    suspend fun deleteExpired(nowMs: Long)

    @Query("DELETE FROM time_grants WHERE grantId = :grantId")
    suspend fun deleteById(grantId: String)
}
