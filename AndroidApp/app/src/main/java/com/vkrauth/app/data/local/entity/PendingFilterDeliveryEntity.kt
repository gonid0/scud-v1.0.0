package com.vkrauth.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "pending_filter_deliveries")
data class PendingFilterDeliveryEntity(
    @PrimaryKey val deliveryId: String,
    val targetReaderId: String,
    val filterVersion: Long,
    val filterPackageBytes: ByteArray,
    val courierIdHex: String,
    val downloadedAtMs: Long,
    val status: String
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is PendingFilterDeliveryEntity) return false
        return deliveryId == other.deliveryId
    }

    override fun hashCode(): Int = deliveryId.hashCode()
}
