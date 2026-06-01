package com.vkrauth.app.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ReportDto(
    val type: String,
    @SerialName("target_reader_id") val targetReaderId: String,
    val bytes: String
)

@Serializable
data class SubmitReportsRequest(
    val reports: List<ReportDto>
)

@Serializable
data class RejectedDto(
    val index: Int,
    val reason: String? = null
)

// Backend возвращает accepted как список UUID'ов отчётов (строки), rejected — список
// объектов {index, reason}. См. Backend/src/scud/api/app/reports.py::submit_reports.
@Serializable
data class SubmitReportsResponse(
    val accepted: List<String> = emptyList(),
    val rejected: List<RejectedDto> = emptyList()
)
