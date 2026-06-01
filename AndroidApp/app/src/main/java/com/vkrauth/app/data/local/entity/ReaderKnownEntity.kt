package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "readers_known")
data class ReaderKnownEntity(
    @PrimaryKey val readerId: String,
    val displayName: String,
    val description: String?,
    val readerPubkey: ByteArray,
    val readerGroupId: String
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is ReaderKnownEntity) return false
        return readerId == other.readerId
    }

    override fun hashCode(): Int = readerId.hashCode()
}
