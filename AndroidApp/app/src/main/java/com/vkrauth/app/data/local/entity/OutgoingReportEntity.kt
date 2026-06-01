package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "outgoing_reports",
    indices = [Index(value = ["type", "targetReaderId"])]
)
data class OutgoingReportEntity(
    @PrimaryKey val reportId: String,
    val type: String,
    val targetReaderId: String,
    val bytes: ByteArray,
    val producedAtMs: Long,
    val retryCount: Int = 0
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is OutgoingReportEntity) return false
        return reportId == other.reportId
    }

    override fun hashCode(): Int = reportId.hashCode()
}
