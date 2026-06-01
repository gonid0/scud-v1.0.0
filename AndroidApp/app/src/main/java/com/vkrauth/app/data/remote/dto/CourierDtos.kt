package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Элемент GET /app/courier/available. Поля синхронизированы с Backend
// `domain/courier.py::list_available`.
@Serializable
data class CourierAvailableItem(
    @SerialName("delivery_id") val deliveryId: String,
    @SerialName("target_reader_id") val readerIdHex: String,
    @SerialName("target_reader_display_name") val readerName: String,
    @SerialName("filter_version") val filterVersion: Long,
    @SerialName("package_size_bytes") val packageSizeBytes: Long = 0,
    @SerialName("created_at") val createdAt: String
)

@Serializable
data class CourierAvailableResponse(
    val items: List<CourierAvailableItem> = emptyList()
)

// POST /app/courier/download. Backend ждёт именно `delivery_id`.
@Serializable
data class DownloadRequest(
    @SerialName("delivery_id") val deliveryId: String
)

@Serializable
data class DownloadResponse(
    @SerialName("delivery_id") val deliveryId: String,
    @SerialName("target_reader_id") val readerIdHex: String,
    @SerialName("filter_version") val filterVersion: Long,
    @SerialName("filter_package_bytes") val filterPackageBase64: String,
    @SerialName("courier_id") val courierIdHex: String
)
