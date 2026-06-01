package com.vkrauth.app.ui.ble

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.R
import com.vkrauth.app.ble.BleScanner
import com.vkrauth.app.ble.ScannedReader
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BleReadersUiState(
    val bleAvailable: Boolean = true,
    val bleEnabled: Boolean = true,
    val missingPermissions: List<String> = emptyList(),
    val isScanning: Boolean = false,
    val readers: List<ScannedReader> = emptyList(),
    @StringRes val errorRes: Int? = null,
    val errorArg: String? = null
)

@HiltViewModel
class BleReadersViewModel @Inject constructor(
    private val scanner: BleScanner
) : ViewModel() {

    private val _state = MutableStateFlow(BleReadersUiState())
    val state: StateFlow<BleReadersUiState> = _state.asStateFlow()

    private var scanJob: Job? = null
    private val seenByAddress = linkedMapOf<String, ScannedReader>()

    init { refreshAvailability() }

    private fun refreshAvailability() {
        _state.update {
            it.copy(
                bleAvailable = scanner.isBleAvailable(),
                bleEnabled = scanner.isEnabled(),
                missingPermissions = scanner.missingPermissions()
            )
        }
    }

    fun startScan() {
        refreshAvailability()
        if (scanJob?.isActive == true) return
        if (!scanner.isBleAvailable() || !scanner.isEnabled()) {
            _state.update { it.copy(errorRes = R.string.ble_readers_error_unavailable, errorArg = null) }
            return
        }
        if (scanner.missingPermissions().isNotEmpty()) {
            _state.update { it.copy(errorRes = R.string.ble_readers_error_permissions, errorArg = null) }
            return
        }
        seenByAddress.clear()
        _state.update { it.copy(isScanning = true, errorRes = null, errorArg = null, readers = emptyList()) }
        scanJob = viewModelScope.launch {
            try {
                scanner.scan().collect { adv ->
                    val existing = seenByAddress[adv.deviceAddress]
                    val merged = if (existing == null) {
                        adv
                    } else {
                        existing.copy(rssi = adv.rssi, lastSeenAtMs = adv.lastSeenAtMs)
                    }
                    seenByAddress[adv.deviceAddress] = merged
                    // ListView порядок: сильнее сигнал — выше.
                    val sorted = seenByAddress.values.sortedByDescending { it.rssi }
                    _state.update { it.copy(readers = sorted) }
                }
            } catch (e: Exception) {
                _state.update { it.copy(errorRes = null, errorArg = e.message ?: "scan error", isScanning = false) }
            }
        }
    }

    fun stopScan() {
        scanJob?.cancel()
        scanJob = null
        _state.update { it.copy(isScanning = false) }
    }

    override fun onCleared() {
        stopScan()
        super.onCleared()
    }
}
