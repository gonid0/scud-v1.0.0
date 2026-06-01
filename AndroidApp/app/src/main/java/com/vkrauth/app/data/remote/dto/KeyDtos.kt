package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Общий DTO выпущенного ключа. Используется в ответах:
//   POST /app/keys/request (issued_key)       — minimal: key_id, full_key_bytes, issued_at, expires_at
//   GET  /app/permits/{id}/keys (items[*])    — дополнительно device_id, device_label, is_current_device, status
@Serializable
data class IssuedKeyDto(
    @SerialName("key_id") val keyIdHex: String,
    @SerialName("full_key_bytes") val fullKeyBase64: String,
    @SerialName("issued_at") val issuedAt: String,
    @SerialName("expires_at") val expiresAt: String,
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("device_label") val deviceLabel: String? = null,
    @SerialName("is_current_device") val isCurrentDevice: Boolean? = null,
    val status: String? = null
)

@Serializable
data class TimeGrantDto(
    @SerialName("grant_id") val grantId: String,
    @SerialName("full_grant_bytes") val fullGrantBase64: String,
    @SerialName("kind") val kind: String = "soft",
    @SerialName("expires_at") val expiresAt: String
)

// Backend возвращает {"items": [...]} для /app/permits/{id}/keys (см. api/app/permits.py).
@Serializable
data class KeyListResponse(
    @SerialName("items") val items: List<IssuedKeyDto> = emptyList()
)

@Serializable
data class RequestKeyRequest(
    @SerialName("permit_id") val permitId: String,
    @SerialName("validity_seconds") val validitySeconds: Int,
    @SerialName("request_grant") val requestGrant: Boolean,
    // Опциональный future-start: unix-секунды. Если null → бэкенд ставит now.
    // Нужен для выпуска ключа «на потом» (UI с датапикером start-end).
    @SerialName("issued_at_ts") val issuedAtTs: Long? = null
)

@Serializable
data class RequestKeyResponse(
    @SerialName("issued_key") val issuedKey: IssuedKeyDto,
    @SerialName("time_grant") val timeGrant: TimeGrantDto? = null
)
