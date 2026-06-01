package com.vkrauth.app.ui.ble

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import com.vkrauth.app.ble.ScannedReader
import com.vkrauth.app.data.crypto.toHex

/**
 * Экран «Активные ридеры (BLE)» — shared §16.6.
 *
 * UX:
 *   - При входе автоматически стартует scan.
 *   - Каждое устройство — карточка с short_reader_id, RSSI и device name.
 *   - Тап по карточке → onPickReader(адрес). Дальше — выбор ключа +
 *     запуск BleSession (это отдельный экран, не входит в этот файл).
 */
/**
 * Полный flow: сначала список, затем при клике — переход на BleSessionScreen.
 * Реализовано через локальный state, чтобы не привязываться к существующему
 * navigation-графу (он живёт в navigation/ и может быть либо Navigation Compose,
 * либо custom — этот экран должен работать в обоих случаях).
 */
@Composable
fun BleReadersScreen(
    onClose: () -> Unit,
    viewModel: BleReadersViewModel = hiltViewModel()
) {
    var pickedReader by remember { mutableStateOf<ScannedReader?>(null) }

    pickedReader?.let { picked ->
        BleSessionScreen(
            deviceAddress = picked.deviceAddress,
            shortId = picked.shortIdHex(),
            onClose = { pickedReader = null },
        )
        return
    }

    val state by viewModel.state.collectAsStateWithLifecycle()

    DisposableEffect(Unit) {
        viewModel.startScan()
        onDispose { viewModel.stopScan() }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text(
            stringResource(R.string.ble_readers_title),
            style = MaterialTheme.typography.titleLarge
        )

        if (!state.bleAvailable) {
            Text(stringResource(R.string.ble_readers_not_supported),
                color = MaterialTheme.colorScheme.error)
        }
        if (state.bleAvailable && !state.bleEnabled) {
            Text(stringResource(R.string.ble_readers_disabled),
                color = MaterialTheme.colorScheme.error)
        }
        if (state.missingPermissions.isNotEmpty()) {
            Text(stringResource(R.string.ble_readers_missing_permissions, state.missingPermissions.joinToString(", ")),
                color = MaterialTheme.colorScheme.error)
        }
        state.errorRes?.let {
            Text(stringResource(it), color = MaterialTheme.colorScheme.error)
        }
        if (state.errorRes == null) {
            state.errorArg?.let {
                Text(it, color = MaterialTheme.colorScheme.error)
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            if (state.isScanning) {
                CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                Text(stringResource(R.string.ble_readers_scanning))
            } else {
                Button(onClick = { viewModel.startScan() }) { Text(stringResource(R.string.ble_readers_scan_button)) }
            }
        }

        if (state.readers.isEmpty()) {
            Text(
                stringResource(R.string.ble_readers_empty_hint),
                style = MaterialTheme.typography.bodyMedium
            )
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(state.readers, key = { it.deviceAddress }) { reader ->
                    ReaderCard(reader = reader, onClick = { pickedReader = reader })
                }
            }
        }

        OutlinedButton(onClick = onClose, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.common_close))
        }
    }
}

@Composable
private fun ReaderCard(reader: ScannedReader, onClick: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                reader.deviceName ?: stringResource(R.string.ble_readers_device_name_fallback, reader.shortIdHex().uppercase()),
                style = MaterialTheme.typography.titleMedium
            )
            Text(
                stringResource(R.string.ble_readers_device_details, reader.shortIdHex(), reader.rssi),
                style = MaterialTheme.typography.bodySmall
            )
            Button(onClick = onClick, modifier = Modifier.padding(top = 6.dp)) {
                Text(stringResource(R.string.ble_readers_open_button))
            }
        }
    }
}
