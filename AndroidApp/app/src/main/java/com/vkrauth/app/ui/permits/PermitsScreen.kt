package com.vkrauth.app.ui.permits

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sort
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.BottomAppBar
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.ui.common.EmptyState
import com.vkrauth.app.ui.common.StatusChip
import com.vkrauth.app.ui.theme.StatusActiveDark
import com.vkrauth.app.ui.theme.StatusActiveLight
import com.vkrauth.app.ui.theme.StatusExpiredDark
import com.vkrauth.app.ui.theme.StatusExpiredLight
import com.vkrauth.app.ui.theme.StatusPendingDark
import com.vkrauth.app.ui.theme.StatusPendingLight
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun PermitsScreen(
    onOpenKeys: (permitId: String) -> Unit,
    viewModel: PermitsViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var search by remember { mutableStateOf("") }

    val visible = remember(state.visiblePermits, search) {
        if (search.isBlank()) state.visiblePermits
        else state.visiblePermits.filter { it.displayName.contains(search, ignoreCase = true) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                windowInsets = WindowInsets(0, 0, 0, 0),
                title = { Text(stringResource(R.string.permits_title), style = MaterialTheme.typography.titleLarge) },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }, enabled = !state.refreshing) {
                        if (state.refreshing) {
                            CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.size(20.dp))
                        } else {
                            Icon(Icons.Default.Refresh, contentDescription = stringResource(R.string.permits_refresh_cd))
                        }
                    }
                    SortMenu(state.sortMode, viewModel::onSortChange)
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        bottomBar = {
            if (state.selectionMode) {
                BottomAppBar {
                    Button(
                        onClick = { viewModel.showBulkRevokeDialog() },
                        enabled = state.canBulkRevoke,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                    ) {
                        Icon(Icons.Default.Block, contentDescription = null)
                        Spacer(Modifier.size(8.dp))
                        Text(stringResource(R.string.permits_revoke_selected, state.selectedIds.size))
                    }
                }
            }
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            // Search + filter chip row.
            Column(
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = search,
                    onValueChange = { search = it },
                    placeholder = { Text(stringResource(R.string.permits_search_placeholder)) },
                    leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = state.onlyEmpty,
                        onClick = { viewModel.onOnlyEmptyChange(!state.onlyEmpty) },
                        label = { Text(stringResource(R.string.permits_filter_only_empty)) },
                    )
                }
            }

            state.refreshError?.let { err ->
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            stringResource(R.string.permits_error, err),
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        TextButton(onClick = viewModel::dismissError) { Text(stringResource(R.string.common_ok)) }
                    }
                }
            }

            if (visible.isEmpty() && !state.refreshing) {
                EmptyState(
                    icon = Icons.Default.Badge,
                    title = if (search.isNotBlank())
                        stringResource(R.string.permits_empty_search_title) else stringResource(R.string.permits_empty_title),
                    subtitle = if (search.isNotBlank())
                        stringResource(R.string.permits_empty_search_subtitle)
                    else
                        stringResource(R.string.permits_empty_subtitle),
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        horizontal = 16.dp, vertical = 8.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (state.selectionMode) {
                        item {
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Checkbox(
                                    checked = state.allSelected,
                                    onCheckedChange = { viewModel.toggleSelectAll() },
                                )
                                Text(stringResource(R.string.permits_select_all), style = MaterialTheme.typography.bodyMedium)
                                Spacer(Modifier.weight(1f))
                                TextButton(onClick = viewModel::exitSelectionMode) { Text(stringResource(R.string.common_cancel)) }
                            }
                        }
                    }
                    items(visible, key = { it.permitId }) { permit ->
                        PermitRow(
                            permit = permit,
                            selectionMode = state.selectionMode,
                            selected = permit.permitId in state.selectedIds,
                            onClick = {
                                if (state.selectionMode) viewModel.toggleSelect(permit.permitId)
                                else onOpenKeys(permit.permitId)
                            },
                            onLongClick = {
                                if (!state.selectionMode) viewModel.enterSelectionMode(permit.permitId)
                            },
                        )
                    }
                }
            }
        }

        state.showConfirmDialog?.let { list ->
            ConfirmRevokeDialog(
                permits = list,
                onConfirm = viewModel::confirmRevoke,
                onDismiss = viewModel::dismissDialog,
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun PermitRow(
    permit: PermitEntity,
    selectionMode: Boolean,
    selected: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val expiresAt = permit.validUntilMs
    val nowMs = System.currentTimeMillis()
    val expiringSoon = (expiresAt - nowMs) < 7L * 24 * 3600 * 1000 && expiresAt > nowMs
    val expired = expiresAt <= nowMs

    val (statusText, statusBg, statusFg, statusIcon) = when {
        expired -> Quad(stringResource(R.string.permits_status_expired), StatusExpiredLight.copy(alpha = 0.15f), StatusExpiredLight, Icons.Default.Schedule)
        expiringSoon -> Quad(stringResource(R.string.permits_status_expiring), StatusPendingLight.copy(alpha = 0.15f), StatusPendingLight, Icons.Default.Schedule)
        permit.activeKeysCount == 0 -> Quad(stringResource(R.string.permits_status_no_key), StatusPendingLight.copy(alpha = 0.15f), StatusPendingLight, null)
        else -> Quad(stringResource(R.string.permits_status_active), StatusActiveLight.copy(alpha = 0.15f), StatusActiveLight, Icons.Default.CheckCircle)
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = onClick, onLongClick = onLongClick),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (selected)
                MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surface,
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (selectionMode) {
                Checkbox(checked = selected, onCheckedChange = null)
                Spacer(Modifier.size(8.dp))
            } else {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.primaryContainer),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Default.Badge, contentDescription = null,
                         tint = MaterialTheme.colorScheme.onPrimaryContainer)
                }
                Spacer(Modifier.size(12.dp))
            }
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    permit.displayName,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    StatusChip(
                        text = statusText,
                        containerColor = statusBg,
                        contentColor = statusFg,
                        icon = statusIcon,
                    )
                    Text(
                        stringResource(R.string.permits_keys_count, permit.activeKeysCount, permit.nParallel),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    stringResource(
                        R.string.permits_valid_until,
                        SimpleDateFormat("dd.MM.yyyy", Locale.getDefault()).format(Date(expiresAt))
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun SortMenu(current: PermitSortMode, onSelect: (PermitSortMode) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { expanded = true }) {
            Icon(Icons.Default.Sort, contentDescription = stringResource(R.string.permits_sort_cd))
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text(stringResource(R.string.permits_sort_expires_asc)) },
                onClick = { onSelect(PermitSortMode.ExpiresAsc); expanded = false },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.permits_sort_expires_desc)) },
                onClick = { onSelect(PermitSortMode.ExpiresDesc); expanded = false },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.permits_sort_keys_asc)) },
                onClick = { onSelect(PermitSortMode.KeysAsc); expanded = false },
            )
            DropdownMenuItem(
                text = { Text(stringResource(R.string.permits_sort_keys_desc)) },
                onClick = { onSelect(PermitSortMode.KeysDesc); expanded = false },
            )
        }
    }
}

@Composable
private fun ConfirmRevokeDialog(
    permits: List<PermitEntity>,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Default.Block, contentDescription = null) },
        title = { Text(stringResource(R.string.permits_revoke_dialog_title)) },
        text = {
            Column {
                Text(
                    stringResource(R.string.permits_revoke_dialog_message),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.size(8.dp))
                permits.forEach {
                    Text(stringResource(R.string.permits_revoke_dialog_item, it.displayName), style = MaterialTheme.typography.bodyMedium)
                }
            }
        },
        confirmButton = { TextButton(onClick = onConfirm) { Text(stringResource(R.string.permits_revoke_confirm)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) } },
    )
}

private data class Quad<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)
