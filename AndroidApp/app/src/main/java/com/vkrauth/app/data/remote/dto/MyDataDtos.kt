package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// GET /app/my-data — см. Backend/src/scud/api/app/my_data.py::MyDataResponse.
@Serializable
data class MyDataResponse(
    val user: UserDto,
    val permits: List<PermitDto> = emptyList(),
    val devices: List<DeviceDto> = emptyList()
)

// Backend отдаёт user как dict: {user_id, display_name, user_group_id}. Поле login
// не возвращается — оставлено optional на случай расширения.
@Serializable
data class UserDto(
    @SerialName("user_id") val userId: Int,
    val login: String? = null,
    @SerialName("display_name") val displayName: String,
    @SerialName("user_group_id") val userGroupId: String
)

@Serializable
data class DeviceDto(
    @SerialName("device_id") val deviceId: String,
    @SerialName("device_label") val deviceLabel: String? = null,
    @SerialName("registered_at") val registeredAt: String? = null,
    @SerialName("is_current") val isCurrent: Boolean = false
)

// Nested reader объект внутри PermitItem (только для my-data).
@Serializable
data class PermitReaderInfo(
    @SerialName("reader_id") val readerIdHex: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("group_id") val groupId: String
)

// Единый DTO permit-элемента. Оба endpoint'а (/app/permits и /app/my-data) возвращают
// совместимую форму, но /app/permits вкладывает поля reader_id/reader_display_name
// плоско, а /app/my-data — через объект `reader`. Поддерживаем оба варианта
// (одно из полей всегда заполнено).
@Serializable
data class PermitDto(
    @SerialName("permit_id") val permitId: String,
    @SerialName("display_name") val displayName: String,
    val description: String? = null,
    // Плоский вариант из /app/permits
    @SerialName("reader_id") val readerIdHex: String? = null,
    @SerialName("reader_display_name") val readerDisplayName: String? = null,
    // Вложенный вариант из /app/my-data
    val reader: PermitReaderInfo? = null,
    @SerialName("valid_from") val validFrom: String,
    @SerialName("valid_until") val validUntil: String,
    @SerialName("n_parallel") val nParallel: Int,
    @SerialName("max_token_ttl_seconds") val maxTokenTtlSeconds: Int,
    // Пожизненный лимит выпуска ключей; null = без лимита (или сервер не прислал).
    @SerialName("max_total_issued") val maxTotalIssued: Int? = null,
    @SerialName("active_keys_count") val activeKeysCount: Int = 0,
    @SerialName("has_grant_for_device") val hasGrantForDevice: Boolean? = null,
    @SerialName("has_grant_on_this_device") val hasGrantOnThisDevice: Boolean? = null
) {
    val effectiveReaderId: String get() = readerIdHex ?: reader?.readerIdHex ?: ""
    val hasGrant: Boolean get() = hasGrantForDevice ?: hasGrantOnThisDevice ?: false
}

@Serializable
data class PermitListResponse(
    @SerialName("items") val items: List<PermitDto> = emptyList()
)
