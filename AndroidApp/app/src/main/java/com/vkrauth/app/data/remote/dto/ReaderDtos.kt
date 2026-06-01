package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ReaderResponse(
    @SerialName("reader_id") val readerIdHex: String,
    @SerialName("display_name") val displayName: String,
    val description: String? = null,
    @SerialName("reader_pubkey") val readerPubkey: String,
    @SerialName("group_id") val groupId: String
)

// Backend list_readers() возвращает {"items": [...]}, см. api/app/readers.py::list_readers.
@Serializable
data class ReaderListResponse(
    @SerialName("items") val items: List<ReaderResponse> = emptyList()
)
