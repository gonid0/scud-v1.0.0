@file:OptIn(
    androidx.compose.foundation.ExperimentalFoundationApi::class,
    androidx.compose.material3.ExperimentalMaterial3Api::class
)

package com.vkrauth.app.ui.keys

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Nfc
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import com.vkrauth.app.ui.common.EmptyState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.BottomAppBar
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.vkrauth.app.R
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.ui.theme.StatusActiveDark
import com.vkrauth.app.ui.theme.StatusActiveLight
import com.vkrauth.app.ui.theme.StatusExpiredDark
import com.vkrauth.app.ui.theme.StatusExpiredLight
import com.vkrauth.app.ui.theme.StatusPendingDark
import com.vkrauth.app.ui.theme.StatusPendingLight
import com.vkrauth.app.ui.theme.StatusRevokedDark
import com.vkrauth.app.ui.theme.StatusRevokedLight
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun KeysScreen(
    permitIdFilter: String? = null,
    onBack: () -> Unit = {},
    viewModel: KeysViewModel = hiltViewModel()
) {
    LaunchedEffect(permitIdFilter) { viewModel.setPermitFilter(permitIdFilter) }
    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                windowInsets = WindowInsets(0, 0, 0, 0),
                title = { Text(stringResource(R.string.keys_title), style = MaterialTheme.typography.titleLarge) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        bottomBar = {
            if (state.selectionMode) {
                val selectedKeys = state.keys.filter { it.keyIdHex in state.selectedIds }
                val anyActive = selectedKeys.any { it.status == IssuedKeyStatus.ACTIVE }
                val anyArchived = selectedKeys.any { it.status != IssuedKeyStatus.ACTIVE }
                BottomAppBar {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        if (anyActive) {
                            Button(
                                onClick = { viewModel.askServerRevoke(selectedKeys) },
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = MaterialTheme.colorScheme.error,
                                    contentColor = MaterialTheme.colorScheme.onError
                                ),
                                modifier = Modifier.weight(1f)
                            ) { Text(stringResource(R.string.keys_revoke_via_server)) }
                            Button(
                                onClick = { viewModel.askReaderRevoke(selectedKeys) },
                                modifier = Modifier.weight(1f)
                            ) { Text(stringResource(R.string.keys_revoke_via_reader)) }
                        }
                        if (anyArchived && !anyActive) {
                            Button(
                                onClick = { viewModel.deleteSelected() },
                                modifier = Modifier.weight(1f)
                            ) {
                                Icon(Icons.Default.DeleteSweep, null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.size(4.dp))
                                Text(stringResource(R.string.keys_delete_from_archive))
                            }
                        }
                    }
                }
            }
        },
        floatingActionButton = {
            val permit = state.permit
            if (permit != null && !state.selectionMode) {
                // Кнопка «запросить новый ключ» — кружок с «+» в правом нижнем углу.
                // Гейтим по обоим лимитам: слоты n_parallel и квота ещё-не-истёкших
                // ключей max_total_issued (totalIssuedCount = expiresAtMs>now, как на сервере).
                val canIssue = state.slotCount < permit.nParallel &&
                    (permit.maxTotalIssued == null || state.totalIssuedCount < permit.maxTotalIssued)
                FloatingActionButton(
                    onClick = { if (canIssue) viewModel.openRequestDialog() },
                    containerColor = if (canIssue)
                        MaterialTheme.colorScheme.primaryContainer
                    else
                        MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Icon(
                        Icons.Default.Add,
                        contentDescription = stringResource(R.string.keys_request_new_key),
                    )
                }
            }
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            state.permit?.let { permit ->
                Card(
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                        contentColor = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(permit.displayName, style = MaterialTheme.typography.titleLarge)
                        permit.description?.let {
                            Text(it, style = MaterialTheme.typography.bodyMedium)
                        }
                        // Срок жизни пропуска: от — до (точность до часа).
                        Spacer(Modifier.height(8.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.Schedule, null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.size(6.dp))
                            Text(
                                stringResource(
                                    R.string.keys_lifetime_range,
                                    lifetimeFmt(permit.validFromMs),
                                    lifetimeFmt(permit.validUntilMs),
                                ),
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                        // Счётчики в строку: «в работе» (slot/n_parallel — серверный
                        // гейт active+revoked_by_server) и «всего выпущено» (по всем
                        // ключам пропуска / max_total_issued; ∞ если лимита нет).
                        Spacer(Modifier.height(10.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            CountChip(
                                icon = Icons.Default.Key,
                                text = stringResource(R.string.keys_active_count, state.slotCount, permit.nParallel),
                            )
                            CountChip(
                                icon = Icons.Default.History,
                                text = permit.maxTotalIssued?.let {
                                    stringResource(R.string.keys_total_issued_limited, state.totalIssuedCount, it)
                                } ?: stringResource(R.string.keys_total_issued_unlimited, state.totalIssuedCount),
                            )
                        }
                        // Объясняем, ПОЧЕМУ выпуск недоступен, когда слот держит
                        // отозванный-на-сервере ключ (ждёт применения bloom ридером).
                        if (state.slotCount >= permit.nParallel && state.pendingBloomCount > 0) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                stringResource(R.string.keys_slot_pending_bloom),
                                style = MaterialTheme.typography.bodySmall
                            )
                        }
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                FilterChip(
                    selected = state.filterMode == KeyFilterMode.ACTIVE_ONLY,
                    onClick = { viewModel.setFilterMode(KeyFilterMode.ACTIVE_ONLY) },
                    label = { Text(stringResource(R.string.keys_filter_active, state.activeCount)) }
                )
                FilterChip(
                    selected = state.filterMode == KeyFilterMode.WITH_ARCHIVE,
                    onClick = { viewModel.setFilterMode(KeyFilterMode.WITH_ARCHIVE) },
                    label = { Text(stringResource(R.string.keys_filter_all, state.activeCount + state.archivedCount)) }
                )
                Spacer(Modifier.weight(1f))
                if (state.archivedCount > 0) {
                    TextButton(onClick = { viewModel.askPurgeArchive() }) {
                        Icon(Icons.Default.DeleteSweep, null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.size(4.dp))
                        Text(stringResource(R.string.keys_clear_archive))
                    }
                }
            }

            if (state.keys.isEmpty()) {
                EmptyState(
                    icon = Icons.Default.VpnKey,
                    title = stringResource(R.string.keys_empty_title),
                    subtitle = if (state.permit != null)
                        stringResource(R.string.keys_empty_subtitle_with_permit)
                    else
                        stringResource(R.string.keys_empty_subtitle_no_permit),
                )
            } else {
                LazyColumn(modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 4.dp)) {
                    items(state.keys, key = { it.keyIdHex }) { key ->
                        SwipeRow(
                            key = key,
                            primaryKeyId = state.primaryKeyId,
                            selected = key.keyIdHex in state.selectedIds,
                            selectionMode = state.selectionMode,
                            onTap = {
                                if (state.selectionMode) viewModel.toggleSelect(key.keyIdHex)
                            },
                            onLongPress = {
                                if (!state.selectionMode) viewModel.enterSelectionMode(key.keyIdHex)
                            },
                            onSwipeLeft = {
                                if (key.status == IssuedKeyStatus.ACTIVE) viewModel.swipeRevokeServer(key)
                                else viewModel.swipeDeleteArchived(key)
                            },
                            onSwipeRight = {
                                if (key.status == IssuedKeyStatus.ACTIVE) viewModel.swipeRevokeReader(key)
                                else viewModel.swipeDeleteArchived(key)
                            }
                        )
                    }
                }
            }
        }

        if (state.showRequestDialog) {
            RequestKeyDialog(
                permitValidUntilMs = state.permit?.validUntilMs ?: (System.currentTimeMillis() + 86_400_000L),
                maxTtlSec = state.permit?.maxTokenTtlSeconds ?: 86400,
                onConfirm = { startMs, validity -> viewModel.requestKey(startMs, validity) },
                onDismiss = viewModel::closeRequestDialog
            )
        }

        state.showConfirmServerRevoke?.let { keys ->
            ConfirmDialog(
                title = stringResource(R.string.keys_dialog_confirm_revoke_server_title),
                subtitle = stringResource(R.string.keys_dialog_revoke_server_subtitle, keys.size),
                onConfirm = viewModel::confirmServerRevoke,
                onDismiss = viewModel::dismissDialogs
            )
        }
        state.showConfirmReaderRevoke?.let { keys ->
            ConfirmDialog(
                title = stringResource(R.string.keys_dialog_confirm_revoke_reader_title),
                subtitle = stringResource(R.string.keys_dialog_revoke_reader_subtitle, keys.size),
                onConfirm = viewModel::confirmReaderRevoke,
                onDismiss = viewModel::dismissDialogs
            )
        }
        // Второй диалог (после подтверждения отзыва через ридер): выбор типа отзыва.
        state.showRevokeTypeChoice?.let { keys ->
            RevokeTypeDialog(
                count = keys.size,
                onAccessAndRevoke = { viewModel.chooseReaderRevokeType(true) },
                onRevokeOnly = { viewModel.chooseReaderRevokeType(false) },
                onDismiss = viewModel::dismissDialogs
            )
        }
        if (state.showConfirmPurge) {
            ConfirmDialog(
                title = stringResource(R.string.keys_dialog_confirm_purge_title),
                subtitle = stringResource(R.string.keys_dialog_purge_subtitle),
                onConfirm = viewModel::confirmPurgeArchive,
                onDismiss = viewModel::dismissDialogs
            )
        }
        state.error?.let { msg ->
            AlertDialog(
                onDismissRequest = viewModel::dismissError,
                title = { Text(stringResource(R.string.common_error)) },
                text = { Text(msg) },
                confirmButton = { TextButton(onClick = viewModel::dismissError) { Text(stringResource(R.string.common_ok)) } }
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun SwipeRow(
    key: IssuedKeyEntity,
    primaryKeyId: String?,
    selected: Boolean,
    selectionMode: Boolean,
    onTap: () -> Unit,
    onLongPress: () -> Unit,
    onSwipeLeft: () -> Unit,
    onSwipeRight: () -> Unit
) {
    val isActive = key.status == IssuedKeyStatus.ACTIVE
    val dismissState = rememberSwipeToDismissBoxState(
        confirmValueChange = { value ->
            when (value) {
                SwipeToDismissBoxValue.StartToEnd -> { onSwipeRight(); true }
                SwipeToDismissBoxValue.EndToStart -> { onSwipeLeft(); true }
                SwipeToDismissBoxValue.Settled    -> false
            }
        },
        positionalThreshold = { total -> total * 0.4f }
    )

    SwipeToDismissBox(
        state = dismissState,
        enableDismissFromStartToEnd = !selectionMode,
        enableDismissFromEndToStart = !selectionMode,
        backgroundContent = {
            SwipeBackground(
                swipeValue = dismissState.dismissDirection,
                isActive = isActive
            )
        },
        content = {
            KeyRow(
                key = key,
                selected = selected,
                selectionMode = selectionMode,
                isPrimary = primaryKeyId == key.keyIdHex,
                onClick = onTap,
                onLongClick = onLongPress
            )
        }
    )
}

@Composable
private fun SwipeBackground(swipeValue: SwipeToDismissBoxValue, isActive: Boolean) {
    val bg: Color
    val iconRight: ImageVector
    val iconLeft: ImageVector
    val textRight: String
    val textLeft: String
    if (isActive) {
        // Активный: StartToEnd (вправо) → через READER, EndToStart (влево) → через сервер.
        bg = when (swipeValue) {
            SwipeToDismissBoxValue.StartToEnd -> MaterialTheme.colorScheme.secondaryContainer
            SwipeToDismissBoxValue.EndToStart -> MaterialTheme.colorScheme.errorContainer
            else -> Color.Transparent
        }
        iconLeft = Icons.Default.Nfc
        iconRight = Icons.Default.Cloud
        textLeft = stringResource(R.string.keys_swipe_revoke_reader)
        textRight = stringResource(R.string.keys_swipe_revoke_server)
    } else {
        bg = MaterialTheme.colorScheme.errorContainer
        iconLeft = Icons.Default.DeleteSweep
        iconRight = Icons.Default.DeleteSweep
        textLeft = stringResource(R.string.keys_swipe_delete)
        textRight = stringResource(R.string.keys_swipe_delete)
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(bg)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (swipeValue == SwipeToDismissBoxValue.StartToEnd) {
            Icon(iconLeft, null)
            Spacer(Modifier.size(8.dp))
            Text(textLeft, style = MaterialTheme.typography.labelLarge)
        }
        Spacer(Modifier.weight(1f))
        if (swipeValue == SwipeToDismissBoxValue.EndToStart) {
            Text(textRight, style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.size(8.dp))
            Icon(iconRight, null)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun KeyRow(
    key: IssuedKeyEntity,
    selected: Boolean,
    selectionMode: Boolean,
    isPrimary: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit
) {
    val isDark = isSystemInDarkTheme()
    val nowMs = System.currentTimeMillis()
    val stLabel = statusLabel(key, nowMs)
    val stColor = statusColor(key, nowMs, isDark)

    val primaryBorder = if (isPrimary) {
        Modifier.border(
            width = 2.dp,
            color = MaterialTheme.colorScheme.primary,
            shape = RoundedCornerShape(12.dp)
        )
    } else Modifier

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .then(primaryBorder)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick),
        colors = CardDefaults.cardColors(
            containerColor = if (key.belongsToThisDevice) {
                MaterialTheme.colorScheme.secondaryContainer
            } else {
                MaterialTheme.colorScheme.surface
            }
        )
    ) {
        Row(modifier = Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
            // Цветная вертикальная полоса-индикатор статуса (слева).
            Box(
                modifier = Modifier
                    .width(6.dp)
                    .fillMaxHeight()
                    .background(stColor)
            )
            Row(
                modifier = Modifier.fillMaxWidth().padding(12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                if (selectionMode) {
                    Checkbox(checked = selected, onCheckedChange = null)
                    Spacer(Modifier.size(6.dp))
                }
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (isPrimary) {
                            Icon(
                                Icons.Default.Star,
                                null,
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(Modifier.size(4.dp))
                        }
                        Text(
                            key.keyIdHex.take(16) + "…",
                            style = MaterialTheme.typography.bodyLarge.copy(fontFamily = FontFamily.Monospace)
                        )
                    }
                    Text(
                        rangeLabel(key),
                        style = MaterialTheme.typography.bodySmall
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.padding(top = 4.dp)
                    ) {
                        StatusBadge(label = stLabel, color = stColor)
                        if (key.belongsToThisDevice) {
                            StatusBadge(
                                label = stringResource(R.string.keys_badge_this_phone),
                                color = MaterialTheme.colorScheme.tertiary
                            )
                        }
                        if (isPrimary) {
                            StatusBadge(
                                label = stringResource(R.string.keys_badge_primary),
                                color = MaterialTheme.colorScheme.primary
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusBadge(label: String, color: Color) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(color.copy(alpha = 0.18f))
            .padding(horizontal = 8.dp, vertical = 3.dp)
    ) {
        Text(label, color = color, style = MaterialTheme.typography.labelSmall)
    }
}

private fun rangeLabel(key: IssuedKeyEntity): String {
    val fmt = SimpleDateFormat("dd.MM HH:mm", Locale.getDefault())
    val startFmt = fmt.format(Date(key.issuedAtMs))
    val endFmt = fmt.format(Date(key.expiresAtMs))
    return "$startFmt → $endFmt"
}

// Срок жизни пропуска — точность до часа (показываем дату и время HH:mm).
private fun lifetimeFmt(ms: Long): String =
    SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault()).format(Date(ms))

/** Компактный чип-счётчик «иконка + текст» для шапки пропуска (в работе / всего выпущено). */
@Composable
private fun CountChip(icon: ImageVector, text: String) {
    Surface(
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.35f),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(icon, null, modifier = Modifier.size(16.dp))
            Text(text, style = MaterialTheme.typography.labelLarge)
        }
    }
}

@Composable
private fun statusLabel(key: IssuedKeyEntity, nowMs: Long): String {
    if (key.expiresAtMs <= nowMs && key.status == IssuedKeyStatus.ACTIVE) return stringResource(R.string.keys_status_expired)
    return when (key.status) {
        IssuedKeyStatus.ACTIVE ->
            if (key.issuedAtMs > nowMs) stringResource(R.string.keys_status_pending_activation) else stringResource(R.string.keys_status_active)
        IssuedKeyStatus.REVOKED_BY_SERVER -> stringResource(R.string.keys_status_revoked_server)
        IssuedKeyStatus.REVOKED_BY_READER -> stringResource(R.string.keys_status_revoked_reader)
        IssuedKeyStatus.REVOKED_IN_BLOOM  -> stringResource(R.string.keys_status_revoked_bloom)
        IssuedKeyStatus.EXPIRED           -> stringResource(R.string.keys_status_expired)
        else                              -> key.status
    }
}

private fun statusColor(key: IssuedKeyEntity, nowMs: Long, dark: Boolean): Color {
    val expiredColor = if (dark) StatusExpiredDark else StatusExpiredLight
    if (key.expiresAtMs <= nowMs) return expiredColor
    return when (key.status) {
        IssuedKeyStatus.ACTIVE ->
            if (key.issuedAtMs > nowMs)
                if (dark) StatusPendingDark else StatusPendingLight
            else
                if (dark) StatusActiveDark else StatusActiveLight
        IssuedKeyStatus.REVOKED_BY_SERVER -> if (dark) StatusPendingDark else StatusPendingLight
        IssuedKeyStatus.REVOKED_BY_READER,
        IssuedKeyStatus.REVOKED_IN_BLOOM  -> if (dark) StatusRevokedDark else StatusRevokedLight
        IssuedKeyStatus.EXPIRED           -> expiredColor
        else                              -> expiredColor
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RequestKeyDialog(
    permitValidUntilMs: Long,
    maxTtlSec: Int,
    onConfirm: (startMs: Long, validitySeconds: Int) -> Unit,
    onDismiss: () -> Unit
) {
    val nowMs = System.currentTimeMillis()
    var startMs by remember { mutableLongStateOf(nowMs) }
    var endMs by remember {
        mutableLongStateOf(minOf(nowMs + maxTtlSec * 1000L, permitValidUntilMs))
    }
    // Фаза flow — 3-state FSM на выбор даты→времени. target=START|END.
    var pickerPhase by remember { mutableStateOf<PickerPhase>(PickerPhase.Idle) }

    val fmt = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.keys_dialog_new_key_title)) },
        text = {
            Column {
                Text(stringResource(R.string.keys_dialog_start_label), style = MaterialTheme.typography.labelLarge)
                OutlinedButton(
                    onClick = { pickerPhase = PickerPhase.PickDate(PickerTarget.START) },
                    modifier = Modifier.fillMaxWidth()
                ) { Text(fmt.format(Date(startMs))) }
                Spacer(Modifier.height(8.dp))
                Text(stringResource(R.string.keys_dialog_end_label), style = MaterialTheme.typography.labelLarge)
                OutlinedButton(
                    onClick = { pickerPhase = PickerPhase.PickDate(PickerTarget.END) },
                    modifier = Modifier.fillMaxWidth()
                ) { Text(fmt.format(Date(endMs))) }
                Spacer(Modifier.height(8.dp))
                val durationSec = ((endMs - startMs) / 1000L).coerceAtLeast(0)
                Text(
                    stringResource(R.string.keys_dialog_duration, formatDuration(durationSec), formatDuration(maxTtlSec.toLong())),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val validity = ((endMs - startMs) / 1000L).toInt().coerceIn(1, maxTtlSec)
                    onConfirm(startMs, validity)
                },
                enabled = endMs > startMs
            ) { Text(stringResource(R.string.keys_dialog_request_button)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) } }
    )

    // Flow: выбор даты → выбор времени → apply к нужному поставщику.
    when (val phase = pickerPhase) {
        is PickerPhase.Idle -> Unit

        is PickerPhase.PickDate -> {
            val current = if (phase.target == PickerTarget.START) startMs else endMs
            val pickerState = rememberDatePickerState(initialSelectedDateMillis = current)
            DatePickerDialog(
                onDismissRequest = { pickerPhase = PickerPhase.Idle },
                confirmButton = {
                    TextButton(onClick = {
                        val selDate = pickerState.selectedDateMillis ?: current
                        // Переходим на выбор времени. Сохраняем час:мин из текущего значения.
                        pickerPhase = PickerPhase.PickTime(phase.target, combineDateKeepTime(selDate, current))
                    }) { Text(stringResource(R.string.keys_dialog_date_next)) }
                },
                dismissButton = {
                    TextButton(onClick = { pickerPhase = PickerPhase.Idle }) { Text(stringResource(R.string.common_cancel)) }
                }
            ) { DatePicker(state = pickerState) }
        }

        is PickerPhase.PickTime -> {
            val cal = java.util.Calendar.getInstance().apply { timeInMillis = phase.preliminaryMs }
            val initHour = cal.get(java.util.Calendar.HOUR_OF_DAY)
            val initMin = cal.get(java.util.Calendar.MINUTE)
            val timeState = rememberTimePickerState(
                initialHour = initHour,
                initialMinute = initMin,
                is24Hour = true
            )
            AlertDialog(
                onDismissRequest = { pickerPhase = PickerPhase.Idle },
                title = { Text(if (phase.target == PickerTarget.START) stringResource(R.string.keys_dialog_time_start_title) else stringResource(R.string.keys_dialog_time_end_title)) },
                text = {
                    Box(
                        modifier = Modifier.fillMaxWidth(),
                        contentAlignment = Alignment.Center
                    ) {
                        TimePicker(state = timeState)
                    }
                },
                confirmButton = {
                    TextButton(onClick = {
                        val combined = setTimeOfDay(phase.preliminaryMs, timeState.hour, timeState.minute)
                        when (phase.target) {
                            PickerTarget.START -> {
                                startMs = combined.coerceAtLeast(nowMs - 60_000L)
                                // Автоподбор окончания: start + maxTtl, но не позднее permit.valid_until.
                                endMs = minOf(startMs + maxTtlSec * 1000L, permitValidUntilMs)
                            }
                            PickerTarget.END -> {
                                val upper = minOf(startMs + maxTtlSec * 1000L, permitValidUntilMs)
                                endMs = combined.coerceAtMost(upper).coerceAtLeast(startMs + 60_000L)
                            }
                        }
                        pickerPhase = PickerPhase.Idle
                    }) { Text(stringResource(R.string.common_ok)) }
                },
                dismissButton = {
                    TextButton(onClick = { pickerPhase = PickerPhase.Idle }) { Text(stringResource(R.string.common_cancel)) }
                }
            )
        }
    }
}

// FSM-state для выбора даты+времени.
private sealed interface PickerPhase {
    data object Idle : PickerPhase
    data class PickDate(val target: PickerTarget) : PickerPhase
    // preliminaryMs содержит уже выбранную дату + старое время (час:мин),
    // пользователь корректирует только часы+минуты в TimePicker.
    data class PickTime(val target: PickerTarget, val preliminaryMs: Long) : PickerPhase
}

private enum class PickerTarget { START, END }

private fun combineDateKeepTime(dateUtcMs: Long, sourceMs: Long): Long {
    // DatePicker возвращает 00:00 UTC выбранного дня. Подмешиваем старое локальное время.
    val src = java.util.Calendar.getInstance().apply { timeInMillis = sourceMs }
    val hour = src.get(java.util.Calendar.HOUR_OF_DAY)
    val min = src.get(java.util.Calendar.MINUTE)
    val date = java.util.Calendar.getInstance().apply {
        timeInMillis = dateUtcMs
        // DatePicker даёт 00:00 UTC → переводим в локальный с нужным часом/мин.
        timeZone = java.util.TimeZone.getTimeZone("UTC")
    }
    val y = date.get(java.util.Calendar.YEAR)
    val m = date.get(java.util.Calendar.MONTH)
    val d = date.get(java.util.Calendar.DAY_OF_MONTH)
    return java.util.Calendar.getInstance().apply {
        set(y, m, d, hour, min, 0)
        set(java.util.Calendar.MILLISECOND, 0)
    }.timeInMillis
}

private fun setTimeOfDay(ms: Long, hour: Int, minute: Int): Long {
    return java.util.Calendar.getInstance().apply {
        timeInMillis = ms
        set(java.util.Calendar.HOUR_OF_DAY, hour)
        set(java.util.Calendar.MINUTE, minute)
        set(java.util.Calendar.SECOND, 0)
        set(java.util.Calendar.MILLISECOND, 0)
    }.timeInMillis
}

@Composable
private fun formatDuration(sec: Long): String {
    val days = sec / 86400
    val hours = (sec % 86400) / 3600
    val mins = (sec % 3600) / 60
    return when {
        days > 0 -> stringResource(R.string.keys_duration_days_hours, days, hours)
        hours > 0 -> stringResource(R.string.keys_duration_hours_mins, hours, mins)
        else -> stringResource(R.string.keys_duration_mins, mins)
    }
}

@Composable
private fun ConfirmDialog(
    title: String,
    subtitle: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(subtitle) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(stringResource(R.string.common_confirm)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) } }
    )
}

// Второй диалог отзыва через ридер: выбор типа.
//   «Пропуск и отзыв» → grantFinalAccess=true  (ACCESS перед REVOKE на тапе);
//   «Только отзыв»    → grantFinalAccess=false (без ACCESS, дверь молчит).
@Composable
private fun RevokeTypeDialog(
    count: Int,
    onAccessAndRevoke: () -> Unit,
    onRevokeOnly: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.keys_dialog_revoke_type_title)) },
        text = { Text(stringResource(R.string.keys_dialog_revoke_type_message)) },
        confirmButton = {
            TextButton(onClick = onAccessAndRevoke) {
                Text(stringResource(R.string.keys_revoke_type_access_and_revoke))
            }
        },
        dismissButton = {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = onRevokeOnly) {
                    Text(stringResource(R.string.keys_revoke_type_revoke_only))
                }
                TextButton(onClick = onDismiss) {
                    Text(stringResource(R.string.common_cancel))
                }
            }
        }
    )
}
