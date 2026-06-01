package com.vkrauth.app.ui.permits

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.data.repository.PermitsRepository
import com.vkrauth.app.domain.usecase.RefreshPermitsUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class PermitSortMode { ExpiresAsc, ExpiresDesc, KeysAsc, KeysDesc }

data class PermitsUiState(
    val visiblePermits: List<PermitEntity> = emptyList(),
    val sortMode: PermitSortMode = PermitSortMode.ExpiresAsc,
    // onlyEmpty = false (OFF) → показываем все пропуска (дефолт).
    // onlyEmpty = true  (ON)  → оставляем ТОЛЬКО пропуска с 0 активных ключей.
    val onlyEmpty: Boolean = false,
    val selectionMode: Boolean = false,
    val selectedIds: Set<String> = emptySet(),
    val allSelected: Boolean = false,
    val canBulkRevoke: Boolean = false,
    val showConfirmDialog: List<PermitEntity>? = null,
    val refreshing: Boolean = false,
    val refreshError: String? = null
)

@HiltViewModel
class PermitsViewModel @Inject constructor(
    private val permitsRepository: PermitsRepository,
    private val refreshPermitsUseCase: RefreshPermitsUseCase
) : ViewModel() {

    private val nowMs = System.currentTimeMillis()

    private val local = MutableStateFlow(
        PermitsUiState()
    )

    init {
        refresh()
    }

    val state: StateFlow<PermitsUiState> = combine(
        permitsRepository.observeActive(nowMs),
        local
    ) { permits, ui ->
        val filtered = if (ui.onlyEmpty) permits.filter { it.activeKeysCount == 0 } else permits
        val sorted = when (ui.sortMode) {
            PermitSortMode.ExpiresAsc -> filtered.sortedBy { it.validUntilMs }
            PermitSortMode.ExpiresDesc -> filtered.sortedByDescending { it.validUntilMs }
            PermitSortMode.KeysAsc -> filtered.sortedBy { it.activeKeysCount }
            PermitSortMode.KeysDesc -> filtered.sortedByDescending { it.activeKeysCount }
        }
        val selected = ui.selectedIds.intersect(sorted.map { it.permitId }.toSet())
        val canBulk = selected.isNotEmpty() &&
                sorted.filter { it.permitId in selected }.all { it.activeKeysCount == 0 }
        ui.copy(
            visiblePermits = sorted,
            selectedIds = selected,
            allSelected = selected.size == sorted.size && sorted.isNotEmpty(),
            canBulkRevoke = canBulk
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), PermitsUiState())

    fun refresh() {
        viewModelScope.launch {
            local.update { it.copy(refreshing = true, refreshError = null) }
            val result = refreshPermitsUseCase()
            local.update {
                it.copy(
                    refreshing = false,
                    refreshError = result.exceptionOrNull()?.message
                )
            }
        }
    }

    fun dismissError() = local.update { it.copy(refreshError = null) }

    fun onSortChange(mode: PermitSortMode) = local.update { it.copy(sortMode = mode) }

    fun onOnlyEmptyChange(v: Boolean) = local.update { it.copy(onlyEmpty = v) }

    fun enterSelectionMode(initial: String) = local.update {
        it.copy(selectionMode = true, selectedIds = it.selectedIds + initial)
    }

    fun exitSelectionMode() = local.update {
        it.copy(selectionMode = false, selectedIds = emptySet())
    }

    fun toggleSelect(permitId: String) = local.update {
        val newSet = if (permitId in it.selectedIds) it.selectedIds - permitId else it.selectedIds + permitId
        it.copy(selectedIds = newSet)
    }

    fun toggleSelectAll() = local.update { cur ->
        val all = cur.visiblePermits.map { it.permitId }.toSet()
        val newSet = if (cur.selectedIds.containsAll(all) && all.isNotEmpty()) emptySet() else all
        cur.copy(selectedIds = newSet)
    }

    fun attemptSwipeRevoke(permitId: String): Boolean {
        val permit = state.value.visiblePermits.firstOrNull { it.permitId == permitId } ?: return false
        if (permit.activeKeysCount > 0) return false
        local.update { it.copy(showConfirmDialog = listOf(permit)) }
        return true
    }

    fun showBulkRevokeDialog() {
        val s = state.value
        val permits = s.visiblePermits.filter { it.permitId in s.selectedIds }
        local.update { it.copy(showConfirmDialog = permits) }
    }

    fun dismissDialog() = local.update { it.copy(showConfirmDialog = null) }

    fun confirmRevoke() {
        val permits = state.value.showConfirmDialog ?: return
        viewModelScope.launch {
            permits.map { async { runCatching { permitsRepository.revoke(it.permitId) } } }.awaitAll()
            refreshPermitsUseCase()
            local.update { it.copy(showConfirmDialog = null, selectedIds = emptySet(), selectionMode = false) }
        }
    }
}
