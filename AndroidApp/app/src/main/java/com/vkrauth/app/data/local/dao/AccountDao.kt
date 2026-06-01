package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import com.vkrauth.app.data.local.entity.AccountEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface AccountDao {
    @Upsert
    suspend fun upsert(account: AccountEntity)

    // --- Активный аккаунт (используется для API/сессии) ---------------------
    @Query("SELECT * FROM account WHERE isActive = 1 LIMIT 1")
    suspend fun find(): AccountEntity?

    @Query("SELECT * FROM account WHERE isActive = 1 LIMIT 1")
    fun observe(): Flow<AccountEntity?>

    @Query("UPDATE account SET deviceId = :deviceId WHERE isActive = 1")
    suspend fun updateDeviceId(deviceId: String)

    @Query("UPDATE account SET sessionToken = :sessionToken, refreshToken = :refreshToken WHERE isActive = 1")
    suspend fun updateTokens(sessionToken: String, refreshToken: String)

    // --- Все аккаунты / переключение ----------------------------------------
    @Query("SELECT * FROM account ORDER BY displayName")
    fun observeAll(): Flow<List<AccountEntity>>

    @Query("SELECT * FROM account ORDER BY displayName")
    suspend fun listAll(): List<AccountEntity>

    @Query("SELECT * FROM account WHERE accountId = :accountId")
    suspend fun findById(accountId: String): AccountEntity?

    @Query("UPDATE account SET isActive = 0")
    suspend fun deactivateAll()

    @Query("UPDATE account SET isActive = 1 WHERE accountId = :accountId")
    suspend fun activate(accountId: String)

    /** Сделать активным ровно один аккаунт (сбрасывает остальные). */
    @Transaction
    suspend fun setActive(accountId: String) {
        deactivateAll()
        activate(accountId)
    }

    @Query("DELETE FROM account WHERE accountId = :accountId")
    suspend fun deleteById(accountId: String)

    @Query("SELECT COUNT(*) FROM account")
    suspend fun count(): Int

    @Query("DELETE FROM account")
    suspend fun clear()
}
