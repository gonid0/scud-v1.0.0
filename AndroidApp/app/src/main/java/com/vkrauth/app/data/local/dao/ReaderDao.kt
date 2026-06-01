package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.vkrauth.app.data.local.entity.ReaderKnownEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface ReaderDao {

    @Query("SELECT * FROM readers_known WHERE readerId = :readerIdHex")
    suspend fun find(readerIdHex: String): ReaderKnownEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(reader: ReaderKnownEntity)

    @Query("SELECT * FROM readers_known")
    fun observeAll(): Flow<List<ReaderKnownEntity>>

    @Query("SELECT * FROM readers_known WHERE readerGroupId = :groupId")
    suspend fun findByGroup(groupId: String): List<ReaderKnownEntity>
}
