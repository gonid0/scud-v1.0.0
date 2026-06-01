package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.vkrauth.app.data.local.entity.ContactHistoryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface ContactHistoryDao {

    @Insert
    suspend fun insert(entry: ContactHistoryEntity)

    @Query("SELECT * FROM contact_history ORDER BY occurredAtMs DESC LIMIT :limit")
    fun observeRecent(limit: Int = 50): Flow<List<ContactHistoryEntity>>
}
