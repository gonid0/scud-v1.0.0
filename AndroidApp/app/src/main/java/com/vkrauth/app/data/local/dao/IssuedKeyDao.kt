package com.vkrauth.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface IssuedKeyDao {

    // Основной выбор ключа для тапа. Фильтр:
    //   status='active' AND expiresAtMs > now
    //   [AND belongsToThisDevice = 1]  если thisDevice=true
    // Сортировка: active первыми (хотя в этом запросе все active по условию),
    // затем по expiresAtMs DESC (выбираем тот, что живёт дольше).
    @Query(
        """
        SELECT * FROM issued_keys
        WHERE readerId = :readerIdHex
          AND status = 'active'
          AND expiresAtMs > :nowMs
          AND (:thisDevice = 0 OR belongsToThisDevice = 1)
        ORDER BY expiresAtMs DESC
        LIMIT 1
        """
    )
    suspend fun firstActiveForReader(
        readerIdHex: String,
        nowMs: Long,
        thisDevice: Boolean
    ): IssuedKeyEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(key: IssuedKeyEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(keys: List<IssuedKeyEntity>)

    @Query("SELECT * FROM issued_keys ORDER BY expiresAtMs DESC")
    fun observeAll(): Flow<List<IssuedKeyEntity>>

    // Сортировка по приоритету: active → revoked_by_server → revoked_by_reader →
    // revoked_in_bloom → expired. Внутри каждой группы — по убыванию expiresAtMs.
    @Query(
        """
        SELECT * FROM issued_keys
        WHERE permitId = :permitId
        ORDER BY
          CASE status
            WHEN 'active'            THEN 0
            WHEN 'revoked_by_server' THEN 1
            WHEN 'revoked_by_reader' THEN 2
            WHEN 'revoked_in_bloom'  THEN 3
            WHEN 'expired'           THEN 4
            ELSE 5
          END,
          expiresAtMs DESC
        """
    )
    fun findByPermit(permitId: String): Flow<List<IssuedKeyEntity>>

    @Query("SELECT * FROM issued_keys WHERE keyIdHex = :keyIdHex")
    suspend fun findByKeyId(keyIdHex: String): IssuedKeyEntity?

    @Query("DELETE FROM issued_keys WHERE keyIdHex = :keyIdHex")
    suspend fun deleteByKeyId(keyIdHex: String)

    @Query("DELETE FROM issued_keys WHERE keyIdHex IN (:keyIds)")
    suspend fun deleteByIds(keyIds: List<String>)

    // Мягкое обновление статуса. Нужно, чтобы архив был видимым (история
    // отозванных ключей показывается как «архив» на экране ключей).
    @Query("UPDATE issued_keys SET status = :status WHERE keyIdHex = :keyIdHex")
    suspend fun setStatus(keyIdHex: String, status: String)

    // Помечает истёкшие active-ключи как 'expired'. Вызывается периодически
    // (на открытии экрана ключей / раз в N секунд в HomeViewModel).
    @Query(
        """
        UPDATE issued_keys
        SET status = 'expired'
        WHERE status = 'active' AND expiresAtMs <= :nowMs
        """
    )
    suspend fun markExpired(nowMs: Long): Int

    // Архивная чистка: удаляет всё что не 'active' ИЛИ с истёкшим TTL.
    @Query(
        """
        DELETE FROM issued_keys
        WHERE status != 'active' OR expiresAtMs <= :nowMs
        """
    )
    suspend fun deleteInactive(nowMs: Long): Int

    @Query(
        """
        DELETE FROM issued_keys
        WHERE permitId = :permitId
          AND (status != 'active' OR expiresAtMs <= :nowMs)
        """
    )
    suspend fun deleteInactiveForPermit(permitId: String, nowMs: Long): Int

    @Query(
        """
        SELECT COUNT(*) FROM issued_keys
        WHERE belongsToThisDevice = 1
          AND status = 'active'
          AND expiresAtMs > :nowMs
        """
    )
    fun observeActiveOwnCount(nowMs: Long): Flow<Int>
}
