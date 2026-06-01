package com.vkrauth.app.hce

import androidx.annotation.StringRes
import com.vkrauth.app.R

sealed interface TapUiState {
    data object Waiting : TapUiState

    data class InProgress(
        // null → ридер неизвестен; UI покажет R.string.tap_unknown_reader.
        val readerName: String?,
        val completedOps: List<CompletedOp>,
        @StringRes val currentActivityRes: Int = R.string.tap_exchanging_with_reader
    ) : TapUiState

    data class Completed(
        val readerName: String?,
        val completedOps: List<CompletedOp>,
        @StringRes val finalMessageRes: Int,
        // Динамический аргумент для format-строк (например verdict в tap_denied).
        val finalMessageArg: String? = null,
        val success: Boolean
    ) : TapUiState

    data class Failed(@StringRes val messageRes: Int) : TapUiState
}

data class CompletedOp(
    val name: String,
    val result: String,
    val success: Boolean
)
