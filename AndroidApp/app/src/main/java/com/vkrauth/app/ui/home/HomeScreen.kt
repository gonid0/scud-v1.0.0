package com.vkrauth.app.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Nfc
import androidx.compose.material.icons.filled.SensorDoor
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.OutlinedButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue

private fun blePermissions(): Array<String> =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    } else {
        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
    }

@Composable
fun HomeScreen(
    onTap: () -> Unit,
    onSettings: () -> Unit,
    onOpenBleReader: (String, String) -> Unit,
    viewModel: HomeViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val nearby by viewModel.nearby.collectAsStateWithLifecycle()
    val scroll = rememberScrollState()

    // BLE-скан требует рантайм-разрешений (BLUETOOTH_SCAN/CONNECT на API 31+,
    // ACCESS_FINE_LOCATION на ≤30). Без них startScan() молча выходит и список
    // «устройства рядом» всегда пуст. Запрашиваем один раз при входе + кнопка
    // «Разрешить» в подсказке; после grant'а повторяем startScan().
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { viewModel.startScan() }
    var permRequested by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(nearby.bleAvailable, nearby.bleEnabled, nearby.missingPermissions) {
        if (!permRequested && nearby.bleAvailable && nearby.bleEnabled &&
            nearby.missingPermissions.isNotEmpty()
        ) {
            permRequested = true
            permLauncher.launch(blePermissions())
        }
    }

    // Сканируем пока экран показан (mirror BleReadersScreen).
    DisposableEffect(Unit) {
        viewModel.startScan()
        onDispose { viewModel.stopScan() }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(scroll)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Greeting(name = state.login)
        HeroTapCard(onTap = onTap)
        StatRow(state)
        NearbySection(
            state = nearby,
            onOpenReader = onOpenBleReader,
            onRequestPermissions = { permLauncher.launch(blePermissions()) },
        )
    }
}

@Composable
private fun Greeting(name: String) {
    Column {
        Text(
            stringResource(R.string.home_greeting_prefix),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            name.ifBlank { stringResource(R.string.common_empty) },
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

/**
 * Компактная hero-карточка — главный CTA. Маленькая иконка + заголовок + кнопка
 * «начать» в одну строку. Большой пульсирующий halo убран ради плотного layout.
 */
@Composable
private fun HeroTapCard(onTap: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer,
            contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .background(
                        MaterialTheme.colorScheme.primary.copy(alpha = 0.22f),
                        CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Default.Nfc,
                    contentDescription = null,
                    modifier = Modifier.size(28.dp),
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    stringResource(R.string.home_hero_title),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    stringResource(R.string.home_hero_subtitle),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            FilledTonalButton(onClick = onTap) {
                Icon(Icons.Default.Nfc, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.size(6.dp))
                Text(stringResource(R.string.home_start_nfc_button), maxLines = 1)
            }
        }
    }
}

@Composable
private fun StatRow(state: HomeUiState) {
    // 4 плитки в одну строку. weight(1f) + maxLines=1, чтобы помещалось на телефоне.
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        StatTile(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.home_stat_permits),
            value = state.activePermits.toString(),
            icon = Icons.Default.Badge,
            color = MaterialTheme.colorScheme.primaryContainer,
        )
        StatTile(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.home_stat_keys),
            value = state.activeKeys.toString(),
            icon = Icons.Default.VpnKey,
            color = MaterialTheme.colorScheme.tertiaryContainer,
        )
        StatTile(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.home_stat_outgoing),
            value = state.outgoingReports.toString(),
            icon = Icons.Default.CloudUpload,
            color = MaterialTheme.colorScheme.secondaryContainer,
        )
        StatTile(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.home_stat_reader),
            value = state.readerTasks.toString(),
            icon = Icons.Default.SensorDoor,
            color = MaterialTheme.colorScheme.surfaceVariant,
        )
    }
}

@Composable
private fun StatTile(
    modifier: Modifier = Modifier,
    label: String,
    value: String,
    icon: ImageVector,
    color: Color,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        color = color,
    ) {
        Column(
            modifier = Modifier.padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // Верхняя строка: иконка слева, значение справа.
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
                Text(
                    value,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            // Подпись блока — снизу слева.
            Text(
                label,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun NearbySection(
    state: NearbyUiState,
    onOpenReader: (String, String) -> Unit,
    onRequestPermissions: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            stringResource(R.string.home_nearby_title),
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp),
        )

        when {
            !state.bleAvailable -> NearbyHint(stringResource(R.string.ble_readers_not_supported))
            !state.bleEnabled -> NearbyHint(stringResource(R.string.ble_readers_disabled))
            state.missingPermissions.isNotEmpty() -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                NearbyHint(
                    stringResource(R.string.ble_readers_missing_permissions, state.missingPermissions.joinToString(", "))
                )
                OutlinedButton(
                    onClick = onRequestPermissions,
                    modifier = Modifier.padding(start = 4.dp),
                ) { Text(stringResource(R.string.home_nearby_grant_permissions)) }
            }
            state.readers.isEmpty() -> NearbyHint(
                stringResource(
                    if (state.isScanning) R.string.home_nearby_scanning else R.string.home_nearby_empty_hint
                )
            )
            else -> state.readers.forEach { reader ->
                NearbyReaderCard(
                    reader = reader,
                    onClick = { onOpenReader(reader.deviceAddress, reader.shortIdHex) },
                )
            }
        }
    }
}

@Composable
private fun NearbyHint(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 4.dp),
    )
}

@Composable
private fun NearbyReaderCard(reader: NearbyReaderUi, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                reader.title.ifBlank {
                    stringResource(R.string.ble_readers_device_name_fallback, reader.shortIdHex.uppercase())
                },
                style = MaterialTheme.typography.titleMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                stringResource(R.string.ble_readers_device_details, reader.shortIdHex, reader.rssi),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatusChip(
                    on = reader.hasKey,
                    labelOn = stringResource(R.string.home_nearby_has_key),
                    labelOff = stringResource(R.string.home_nearby_no_key),
                )
                StatusChip(
                    on = reader.hasDeliveries,
                    labelOn = stringResource(R.string.home_nearby_has_deliveries),
                    labelOff = stringResource(R.string.home_nearby_no_deliveries),
                )
            }
        }
    }
}

@Composable
private fun StatusChip(on: Boolean, labelOn: String, labelOff: String) {
    val container = if (on) MaterialTheme.colorScheme.tertiaryContainer
        else MaterialTheme.colorScheme.surfaceVariant
    val content = if (on) MaterialTheme.colorScheme.onTertiaryContainer
        else MaterialTheme.colorScheme.onSurfaceVariant
    Surface(shape = RoundedCornerShape(10.dp), color = container, contentColor = content) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                if (on) Icons.Default.Check else Icons.Default.Close,
                contentDescription = null,
                modifier = Modifier.size(14.dp),
            )
            Text(
                if (on) labelOn else labelOff,
                style = MaterialTheme.typography.labelSmall,
                maxLines = 1,
            )
        }
    }
}
