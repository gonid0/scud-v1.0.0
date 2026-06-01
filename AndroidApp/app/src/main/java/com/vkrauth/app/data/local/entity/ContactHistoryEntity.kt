package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "contact_history")
data class ContactHistoryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val readerId: String,
    val occurredAtMs: Long,
    val summary: String,
    val verdict: String?
)
