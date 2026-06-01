package com.vkrauth.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.ble.BleScanner
import com.vkrauth.app.ble.ScannedReader
import com.vkrauth.app.data.crypto.Blake2s
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.data.repository.CourierRepository
import com.vkrauth.app.data.repository.KeysRepository
import com.vkrauth.app.data.repository.PermitsRepository
import com.vkrauth.app.data.repository.ReportsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class HomeUiState(
    val login: String = "",
    val activePermits: Int = 0,
    val activeKeys: Int = 0,
    val outgoingReports: Int = 0,
    val readerTasks: Int = 0
)

/** Один обнаруженный поблизости ридер для секции «Устройства рядом». */
data class NearbyReaderUi(
    val deviceAddress: String,
    val title: String,            // device name или null → UI подставит SCUD-<shortid>
    val shortIdHex: String,       // BLAKE2s(reader_id,16)[0:6] в hex (как в adv)
    val rssi: Int,
    val hasKey: Boolean,          // на телефоне есть активный ключ для этого ридера
    val hasDeliveries: Boolean,   // есть pending delivery или pending revoke для ридера
    val supportsBulk: Boolean
)

data class NearbyUiState(
    val bleAvailable: Boolean = true,
    val bleEnabled: Boolean = true,
    val missingPermissions: List<String> = emptyList(),
    val isScanning: Boolean = false,
    val readers: List<NearbyReaderUi> = emptyList()
)

@HiltViewModel
class HomeViewModel @Inject constructor(
    authRepository: AuthRepository,
    permitsRepository: PermitsRepository,
    private val keysRepository: KeysRepository,
    private val reportsRepository: ReportsRepository,
    private val courierRepository: CourierRepository,
    private val scanner: BleScanner
) : ViewModel() {

    private val nowMs = System.currentTimeMillis()

    val state: StateFlow<HomeUiState> = combine(
        authRepository.observeAccount(),
        permitsRepository.observeActiveCount(nowMs),
        keysRepository.observeActiveOwnCount(nowMs),
        reportsRepository.observeOutgoingCount(),
        combine(reportsRepository.observeRevokeIntentsCount(), courierRepository.observePendingCount()) { a, b -> a + b }
    ) { account, permits, keys, outgoing, reader ->
        HomeUiState(
            login = account?.displayName ?: "",
            activePermits = permits,
            activeKeys = keys,
            outgoingReports = outgoing,
            readerTasks = reader
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeUiState())

    // ---- Nearby BLE readers (mirror BleReadersViewModel) ----------------------

    private val _nearby = MutableStateFlow(NearbyUiState())
    val nearby: StateFlow<NearbyUiState> = _nearby.asStateFlow()

    private var scanJob: Job? = null
    private val seenByAddress = linkedMapOf<String, ScannedReader>()

    // Snapshot of shortIdHex → flags, computed at scan start. Reader_id (16B) is
    // NOT advertised — adv carries short_reader_id = BLAKE2s(reader_id,16)[0:6],
    // so we hash every known reader_id and match on the 6-byte prefix.
    // NB: snapshot, not live — refreshed on each startScan() (documented contract).
    private var keyReaderShortIds: Set<String> = emptySet()
    private var deliveryReaderShortIds: Set<String> = emptySet()

    init { refreshAvailability() }

    private fun refreshAvailability() {
        _nearby.update {
            it.copy(
                bleAvailable = scanner.isBleAvailable(),
                bleEnabled = scanner.isEnabled(),
                missingPermissions = scanner.missingPermissions()
            )
        }
    }

    /** short_reader_id (hex) advertised by a reader whose full reader_id is [readerIdHex]. */
    private fun shortIdFor(readerIdHex: String): String? = runCatching {
        val full = Serialization.hexToBytes(readerIdHex)
        Blake2s.compute(full, 128).copyOfRange(0, 6).toHex()
    }.getOrNull()

    fun startScan() {
        refreshAvailability()
        if (scanJob?.isActive == true) return
        if (!scanner.isBleAvailable() || !scanner.isEnabled()) return
        if (scanner.missingPermissions().isNotEmpty()) return

        seenByAddress.clear()
        _nearby.update { it.copy(isScanning = true, readers = emptyList()) }

        scanJob = viewModelScope.launch {
            // Snapshot the per-reader match sets once, at scan start.
            refreshMatchSnapshot()
            try {
                scanner.scan().collect { adv ->
                    val existing = seenByAddress[adv.deviceAddress]
                    val merged = if (existing == null) adv
                        else existing.copy(rssi = adv.rssi, lastSeenAtMs = adv.lastSeenAtMs)
                    seenByAddress[adv.deviceAddress] = merged
                    val sorted = seenByAddress.values
                        .sortedByDescending { it.rssi }
                        .map { it.toUi() }
                    _nearby.update { it.copy(readers = sorted) }
                }
            } catch (_: Exception) {
                _nearby.update { it.copy(isScanning = false) }
            }
        }
    }

    private suspend fun refreshMatchSnapshot() {
        val now = System.currentTimeMillis()

        val keyReaderIds = keysRepository.observeAll().first()
            .filter { it.status == IssuedKeyStatus.ACTIVE && it.expiresAtMs > now && it.belongsToThisDevice }
            .map { it.readerId }
            .toSet()

        val deliveryReaderIds = courierRepository.observePending().first()
            .filter { it.status == "downloaded" }
            .map { it.targetReaderId }
            .toSet()

        val revokeReaderIds = reportsRepository.observeRevokeIntents().first()
            .filter { it.status == "pending" }
            .map { it.targetReaderId }
            .toSet()

        keyReaderShortIds = keyReaderIds.mapNotNull { shortIdFor(it) }.toSet()
        deliveryReaderShortIds = (deliveryReaderIds + revokeReaderIds).mapNotNull { shortIdFor(it) }.toSet()
    }

    private fun ScannedReader.toUi(): NearbyReaderUi {
        val sid = shortIdHex()
        return NearbyReaderUi(
            deviceAddress = deviceAddress,
            title = deviceName ?: "",
            shortIdHex = sid,
            rssi = rssi,
            hasKey = sid in keyReaderShortIds,
            hasDeliveries = sid in deliveryReaderShortIds,
            supportsBulk = supportsBulk
        )
    }

    fun stopScan() {
        scanJob?.cancel()
        scanJob = null
        _nearby.update { it.copy(isScanning = false) }
    }

    override fun onCleared() {
        stopScan()
        super.onCleared()
    }
}
