package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "permits")
data class PermitEntity(
    @PrimaryKey val permitId: String,
    val userId: Int,
    val readerId: String,
    val displayName: String,
    val description: String?,
    val validFromMs: Long,
    val validUntilMs: Long,
    val nParallel: Int,
    val maxTokenTtlSeconds: Int,
    // Пожизненный потолок выпуска ключей (приходит с сервера, поле permit'а).
    // null = без лимита. В отличие от nParallel (одновременно активные) это
    // суммарный лимит за всё время; счётчик «всего выпущено» считается локально
    // по всем строкам issued_keys этого пропуска.
    val maxTotalIssued: Int? = null,
    val activeKeysCount: Int = 0,
    val syncedAtMs: Long
)
