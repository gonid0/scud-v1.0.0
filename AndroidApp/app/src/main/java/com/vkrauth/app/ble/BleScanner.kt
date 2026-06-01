package com.vkrauth.app.ble

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.ParcelUuid
import android.util.Log
import androidx.core.app.ActivityCompat
import com.vkrauth.app.data.crypto.toHex
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Scan-результат для UI: один ридер, как он виден сейчас.
 *
 * `shortReaderId` — первые 6 байт от BLAKE2s(reader_id) (manufacturer-data §16.2;
 * хэш, а не сырой reader_id — приватность от пассивного сканера). Только для
 * отображения/дедупа в списке скана; полный reader_id phone получает в INFO
 * после подключения и проверяет подпись.
 *
 * `caps` — байт capability-флагов из manuf-data (§16.2, X2): bit0 BLE_CAP_BULK
 * (ридер принимает bulk-операции по BLE), bit1 BLE_CAP_HANDOVER (NFC→BLE handover,
 * reserved). НЕ подписан и не входит в conformance-корпус — это лишь подсказка для
 * роутинга (см. TransportRouter); безопасность по-прежнему держит E2E-крипта в INFO.
 */
data class ScannedReader(
    val deviceAddress: String,
    val deviceName: String?,
    val shortReaderId: ByteArray,   // 6 B = BLAKE2s(reader_id)[0:6] (§16.2)
    val caps: Int,                  // capability flags byte (§16.2, X2); 0 if absent
    val rssi: Int,
    val firstSeenAtMs: Long,
    val lastSeenAtMs: Long
) {
    fun shortIdHex(): String = shortReaderId.toHex()

    /** bit0 BLE_CAP_BULK — ридер принимает bulk-операции (FILTER_UPDATE/GET_BLACKLIST) по BLE. */
    val supportsBulk: Boolean get() = (caps and BleConstants.BLE_CAP_BULK) != 0

    /** bit1 BLE_CAP_HANDOVER — ридер поддерживает NFC→BLE handover (reserved). */
    val supportsHandover: Boolean get() = (caps and BleConstants.BLE_CAP_HANDOVER) != 0

    override fun equals(other: Any?): Boolean = this === other || (other is ScannedReader && deviceAddress == other.deviceAddress)
    override fun hashCode(): Int = deviceAddress.hashCode()
}

@Singleton
class BleScanner @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val adapter: BluetoothAdapter? by lazy {
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
    }

    fun isBleAvailable(): Boolean =
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE) &&
            adapter != null

    fun isEnabled(): Boolean = adapter?.isEnabled == true

    fun missingPermissions(): List<String> {
        val needed = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        return needed.filter {
            ActivityCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
        }
    }

    /**
     * Длящийся поток результатов скана. Эмитит при каждом advertisement-пакете.
     * UI должен дедуплицировать по deviceAddress и держать таймер lastSeenAtMs
     * для исчезновения "ушедших" устройств.
     */
    @SuppressLint("MissingPermission")
    fun scan(): Flow<ScannedReader> = callbackFlow {
        val scanner = adapter?.bluetoothLeScanner
        if (scanner == null) {
            close(IllegalStateException("BLE scanner unavailable"))
            return@callbackFlow
        }

        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(BleConstants.SERVICE_UUID))
            .build()

        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_BALANCED)
            .setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES)
            .build()

        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                // mfg payload (mfg-id already stripped): [short_reader_id 6 B][caps 1 B] (§16.2).
                val mfg = result.scanRecord?.getManufacturerSpecificData(BleConstants.MANUFACTURER_ID)
                if (mfg == null || mfg.size < 6) return
                val now = System.currentTimeMillis()
                val short = mfg.copyOfRange(0, 6)
                // caps byte is optional for forward-compat: older advertisements may omit it.
                val caps = if (mfg.size > 6) mfg[6].toInt() and 0xFF else 0
                trySend(
                    ScannedReader(
                        deviceAddress = result.device.address ?: "",
                        deviceName = runCatching { result.device.name }.getOrNull()
                            ?: result.scanRecord?.deviceName,
                        shortReaderId = short,
                        caps = caps,
                        rssi = result.rssi,
                        firstSeenAtMs = now,
                        lastSeenAtMs = now
                    )
                )
            }

            override fun onScanFailed(errorCode: Int) {
                Log.e(TAG, "scan failed: $errorCode")
                close(IllegalStateException("scan failed: $errorCode"))
            }
        }

        try {
            scanner.startScan(listOf(filter), settings, callback)
        } catch (e: SecurityException) {
            close(e)
            return@callbackFlow
        }

        awaitClose {
            runCatching { scanner.stopScan(callback) }
        }
    }

    companion object {
        private const val TAG = "BleScanner"
    }
}
