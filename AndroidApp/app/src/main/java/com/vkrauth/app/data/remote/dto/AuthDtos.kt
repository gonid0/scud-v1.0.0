package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Backend LoginRequest.device_info (api/app/auth.py::DeviceInfo) ждёт поля
// model/os/app_version. Ранее Android слал os_version — отклонялось как лишний ключ.
@Serializable
data class DeviceInfoDto(
    @SerialName("model") val model: String,
    @SerialName("os") val os: String,
    @SerialName("app_version") val appVersion: String
)

@Serializable
data class LoginRequest(
    val login: String,
    val password: String,
    @SerialName("device_info") val deviceInfo: DeviceInfoDto
)

@Serializable
data class LoginResponse(
    @SerialName("session_token") val sessionToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("user_id") val userId: Int,
    @SerialName("user_group_id") val userGroupId: String,
    @SerialName("display_name") val displayName: String
)

@Serializable
data class RefreshRequest(
    @SerialName("refresh_token") val refreshToken: String
)

@Serializable
data class RegisterDeviceRequest(
    @SerialName("phone_pubkey") val phonePubkey: String,
    @SerialName("device_label") val deviceLabel: String
)

@Serializable
data class RegisterDeviceResponse(
    @SerialName("device_id") val deviceId: String
)

@Serializable
data class OkResponse(
    @SerialName("ok") val ok: Boolean = true
)
