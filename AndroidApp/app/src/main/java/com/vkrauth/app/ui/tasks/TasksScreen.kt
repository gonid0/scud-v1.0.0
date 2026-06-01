package com.vkrauth.app.ui.tasks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FilterAlt
import androidx.compose.material.icons.filled.MeetingRoom
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.SensorDoor
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import com.vkrauth.app.ui.common.EmptyState
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(
    onBack: () -> Unit = {},
    viewModel: TasksViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var selectedTab by remember { mutableStateOf(0) }

    Scaffold(
        topBar = {
            TopAppBar(
                windowInsets = WindowInsets(0, 0, 0, 0),
                title = { Text(stringResource(R.string.tasks_title), style = MaterialTheme.typography.titleLarge) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
    Column(modifier = Modifier.fillMaxSize().padding(padding)) {
        TabRow(selectedTabIndex = selectedTab) {
            Tab(
                selected = selectedTab == 0,
                onClick = { selectedTab = 0 },
                text = { TabLabel(stringResource(R.string.tasks_tab_server), Icons.Default.Upload) }
            )
            Tab(
                selected = selectedTab == 1,
                onClick = { selectedTab = 1 },
                text = { TabLabel(stringResource(R.string.tasks_tab_reader), Icons.Default.SensorDoor) }
            )
        }

        when (selectedTab) {
            0 -> ServerTab(state, viewModel)
            1 -> ReaderTab(state, viewModel)
        }

        state.refreshErrorRes?.let { res ->
            AlertDialog(
                onDismissRequest = viewModel::dismissError,
                title = { Text(stringResource(R.string.common_error)) },
                text = {
                    Text(
                        if (state.refreshErrorArg != null) stringResource(res, state.refreshErrorArg!!)
                        else stringResource(res)
                    )
                },
                confirmButton = { TextButton(onClick = viewModel::dismissError) { Text(stringResource(R.string.common_ok)) } }
            )
        }
    }
    } // Scaffold
}

@Composable
private fun ServerTab(state: TasksUiState, vm: TasksViewModel) {
    Column(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        Button(
            onClick = vm::submitAll,
            modifier = Modifier.fillMaxWidth()
        ) {
            Icon(Icons.Default.Upload, null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.size(6.dp))
            Text(stringResource(R.string.tasks_submit_all_button, state.outgoing.size))
        }
        state.lastSubmitResult?.let {
            val resultText = stringResource(R.string.tasks_submit_result, it.accepted, it.rejected)
            Text(
                resultText + (it.error?.let { e -> " — $e" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Spacer(Modifier.height(8.dp))
        if (state.outgoing.isEmpty()) {
            EmptyState(
                icon = Icons.Default.Inbox,
                title = stringResource(R.string.tasks_empty_server_title),
                subtitle = stringResource(R.string.tasks_empty_server_subtitle),
            )
        } else {
            // Группируем очередь по типу пакета: для каждого типа — заголовок с
            // человекочитаемым названием и иконкой, затем карточки этого типа.
            val grouped = state.outgoing.groupBy { it.type }
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                grouped.forEach { (type, reports) ->
                    item(key = "hdr_$type") {
                        TaskGroupHeader(
                            name = reportTypeLabel(type),
                            icon = reportTypeIcon(type),
                            count = reports.size,
                        )
                    }
                    items(reports, key = { it.reportId }) { r ->
                        ServerReportCard(r)
                    }
                }
            }
        }
    }
}

@Composable
private fun ServerReportCard(r: com.vkrauth.app.data.local.entity.OutgoingReportEntity) {
    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                stringResource(R.string.tasks_target_reader, r.targetReaderId.take(16)),
                style = MaterialTheme.typography.bodySmall,
            )
            val retryColor = if (r.retryCount > 3) MaterialTheme.colorScheme.error else Color.Unspecified
            Text(
                stringResource(R.string.tasks_retries, r.retryCount),
                style = MaterialTheme.typography.bodySmall,
                color = retryColor,
            )
        }
    }
}

@Composable
private fun ReaderTab(state: TasksUiState, vm: TasksViewModel) {
    Column(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        // Кнопка «Обновить» — единственный способ подтянуть available-элементы
        // с сервера. В локальной БД всегда только скачанные посылки.
        OutlinedButton(
            onClick = vm::refreshAvailable,
            enabled = !state.loading,
            modifier = Modifier.fillMaxWidth()
        ) {
            if (state.loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(16.dp),
                    strokeWidth = 2.dp
                )
            } else {
                Icon(Icons.Default.Refresh, null, modifier = Modifier.size(18.dp))
            }
            Spacer(Modifier.size(6.dp))
            Text(if (state.loading) stringResource(R.string.tasks_refresh_loading) else stringResource(R.string.tasks_refresh_button))
        }
        Spacer(Modifier.height(8.dp))

        val items = state.readerItems
        val revokes = state.pendingRevokes

        if (items.isEmpty() && revokes.isEmpty()) {
            EmptyState(
                icon = Icons.Default.CloudDownload,
                title = stringResource(R.string.tasks_empty_reader_title),
                subtitle = stringResource(R.string.tasks_empty_reader_subtitle),
            )
            return@Column
        }

        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            // Группа «Доставка фильтра» — скачанные + доступные посылки.
            if (items.isNotEmpty()) {
                item(key = "hdr_filter_delivery") {
                    TaskGroupHeader(
                        name = stringResource(R.string.tasks_group_filter_delivery),
                        icon = Icons.Default.FilterAlt,
                        count = items.size,
                    )
                }
                items(items, key = { it.deliveryId }) { item ->
                    when (item) {
                        is ReaderItem.Downloaded -> DownloadedRow(
                            entity = item.entity,
                            onDelete = { vm.deleteDownloaded(item.deliveryId) }
                        )
                        is ReaderItem.Downloadable -> DownloadableRow(
                            item = item,
                            onDownload = { vm.downloadDelivery(item.deliveryId) }
                        )
                    }
                }
            }

            if (revokes.isNotEmpty()) {
                item(key = "hdr_revokes") {
                    TaskGroupHeader(
                        name = stringResource(R.string.tasks_revokes_section_title),
                        icon = Icons.Default.Block,
                        count = revokes.size,
                    )
                }
                items(revokes, key = { it.intentId }) { r ->
                    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Default.Upload, null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.size(8.dp))
                            Column(modifier = Modifier.weight(1f)) {
                                Text(stringResource(R.string.tasks_revoke_card_title), style = MaterialTheme.typography.titleSmall)
                                Text(
                                    "${r.targetKeyIdHex.take(12)}… · reader ${r.targetReaderId.take(12)}…",
                                    style = MaterialTheme.typography.bodySmall
                                )
                            }
                            IconButton(onClick = { vm.cancelRevokeIntent(r.intentId) }) {
                                Icon(Icons.Default.Delete, stringResource(R.string.common_cancel))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DownloadedRow(
    entity: com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity,
    onDelete: () -> Unit
) {
    // Скачанная посылка: карточка с цветом «готов к отправке» +
    // иконка CheckCircle + кнопка «Удалить» справа.
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer
        )
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                Icons.Default.CheckCircle,
                contentDescription = stringResource(R.string.tasks_downloaded_icon_description),
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(28.dp)
            )
            Spacer(Modifier.size(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    stringResource(R.string.tasks_downloaded_for_reader, entity.targetReaderId.take(16)),
                    style = MaterialTheme.typography.titleSmall
                )
                Text(
                    stringResource(R.string.tasks_downloaded_subtitle, entity.filterVersion),
                    style = MaterialTheme.typography.bodySmall
                )
            }
            IconButton(onClick = onDelete) {
                Icon(Icons.Default.Delete, contentDescription = stringResource(R.string.common_delete))
            }
        }
    }
}

@Composable
private fun DownloadableRow(
    item: ReaderItem.Downloadable,
    onDownload: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                Icons.Default.CloudDownload,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(28.dp)
            )
            Spacer(Modifier.size(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(item.readerName, style = MaterialTheme.typography.titleSmall)
                Text(
                    stringResource(R.string.tasks_downloadable_subtitle, item.filterVersion),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            FilledTonalButton(onClick = onDownload) {
                Text(stringResource(R.string.tasks_download_button))
            }
        }
    }
}

/** Подпись вкладки: текст, затем иконка справа (по требованию — значок после названия). */
@Composable
private fun TabLabel(text: String, icon: ImageVector) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text)
        Spacer(Modifier.size(6.dp))
        Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
    }
}

/** Заголовок группы внутри списка: человекочитаемое название, иконка после него, счётчик справа. */
@Composable
private fun TaskGroupHeader(name: String, icon: ImageVector, count: Int) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(name, style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.size(6.dp))
        Icon(
            icon,
            contentDescription = null,
            modifier = Modifier.size(18.dp),
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.weight(1f))
        Text(
            count.toString(),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Человекочитаемое название типа отчёта вместо кодоподобного (filter_delivery_info и т.п.). */
@Composable
private fun reportTypeLabel(type: String): String = when (type) {
    "filter_delivery_info" -> stringResource(R.string.tasks_type_filter_delivery_info)
    "delivery_receipt" -> stringResource(R.string.tasks_type_delivery_receipt)
    "passage_receipt" -> stringResource(R.string.tasks_type_passage_receipt)
    "blacklist_report" -> stringResource(R.string.tasks_type_blacklist_report)
    else -> type
}

private fun reportTypeIcon(type: String): ImageVector = when (type) {
    "filter_delivery_info" -> Icons.Default.FilterAlt
    "delivery_receipt" -> Icons.Default.ReceiptLong
    "passage_receipt" -> Icons.Default.MeetingRoom
    "blacklist_report" -> Icons.Default.Block
    else -> Icons.Default.Upload
}
