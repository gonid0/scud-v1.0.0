package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "time_grants")
data class TimeGrantEntity(
    @PrimaryKey val grantId: String,
    val permitId: String,
    val readerId: String,
    val kind: String,
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    val fullGrantBytes: ByteArray
) {
    fun kindByte(): Byte = when (kind) {
        "hard" -> 0x02
        else -> 0x01
    }

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is TimeGrantEntity) return false
        return grantId == other.grantId
    }

    override fun hashCode(): Int = grantId.hashCode()
}
