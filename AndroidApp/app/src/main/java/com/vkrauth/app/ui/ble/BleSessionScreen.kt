package com.vkrauth.app.ui.ble

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Icon
import androidx.hilt.navigation.compose.hiltViewModel
import com.vkrauth.app.R
import com.vkrauth.app.hce.CompletedOp
import com.vkrauth.app.ui.common.LogPanel
import com.vkrauth.app.ui.common.ReaderTimeCard
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * Экран «сессии» с одним BLE-ридером после выбора из списка.
 * Получает device address + short_reader_id (из adv) как аргументы навигации.
 *
 * Confirm-FIRST: prepare() резолвит ключ из ЛОКАЛЬНЫХ данных и показывает
 * confirm-экран ДО подключения; только confirmAndRun() трогает радио.
 */
@Composable
fun BleSessionScreen(
    deviceAddress: String,
    shortId: String,
    onClose: () -> Unit,
    viewModel: BleSessionViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val logs by viewModel.logEntries.collectAsStateWithLifecycle()
    val ops by viewModel.ops.collectAsStateWithLifecycle()
    val readerTime by viewModel.readerTime.collectAsStateWithLifecycle()

    LaunchedEffect(deviceAddress, shortId) {
        if (state is BleSessionViewModel.UiState.Idle) {
            viewModel.prepare(deviceAddress, shortId)
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
      Column(
          modifier = Modifier.weight(1f).fillMaxWidth(),
          verticalArrangement = Arrangement.spacedBy(16.dp),
          horizontalAlignment = Alignment.CenterHorizontally
      ) {
        Text(stringResource(R.string.ble_session_title), style = MaterialTheme.typography.titleLarge)
        Text(deviceAddress, style = MaterialTheme.typography.bodySmall, color = Color.Gray)

        when (val s = state) {
            BleSessionViewModel.UiState.Idle -> {
                CircularProgressIndicator()
                Text(stringResource(R.string.ble_session_idle_message))
            }
            is BleSessionViewModel.UiState.Connecting -> {
                CircularProgressIndicator()
                Text(stringResource(R.string.ble_session_connecting_message))
            }
            is BleSessionViewModel.UiState.Confirm -> {
                // §16.8: confirm BEFORE connecting. reader_id/name come from LOCAL
                // trusted data (resolved from the advertised short id). The post-connect
                // INFO reader_id match + signature verify is the safety guard
                // (confirmAndRun aborts on mismatch). The summary lists what the session
                // will do — a session is initiable on key OR delivery OR revoke.
                Text(
                    stringResource(R.string.ble_session_confirm_prompt),
                    style = MaterialTheme.typography.bodyMedium
                )
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        if (s.readerName != null) {
                            Text(stringResource(R.string.ble_session_reader_name, s.readerName),
                                style = MaterialTheme.typography.titleMedium)
                            Text(stringResource(R.string.ble_session_reader_id, s.readerIdHex.take(16)),
                                style = MaterialTheme.typography.bodySmall, color = Color.Gray)
                        } else {
                            Text(stringResource(R.string.ble_session_reader_info, s.readerIdHex.take(16)),
                                style = MaterialTheme.typography.titleMedium)
                        }
                        Spacer(Modifier.height(8.dp))
                        // Short summary of what will happen (multiple may apply).
                        val summary = buildList {
                            if (s.hasKey) add(stringResource(R.string.ble_session_action_access))
                            if (s.hasDelivery) add(stringResource(R.string.ble_session_action_delivery))
                            if (s.hasRevoke) add(stringResource(R.string.ble_session_action_revoke))
                        }.joinToString(", ")
                        Text(
                            stringResource(R.string.ble_session_actions_summary, summary),
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
                Button(
                    onClick = { viewModel.confirmAndRun() },
                    modifier = Modifier.fillMaxWidth()
                ) { Text(stringResource(R.string.ble_session_start_button)) }
                OutlinedButton(
                    onClick = { viewModel.cancel(); onClose() },
                    modifier = Modifier.fillMaxWidth()
                ) { Text(stringResource(R.string.common_cancel)) }
            }
            is BleSessionViewModel.UiState.NothingToDo -> {
                Box(modifier = Modifier.padding(8.dp)) {
                    Text(
                        stringResource(R.string.ble_session_nothing_to_do, s.shortIdHex.take(16)),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center
                    )
                }
                Button(onClick = { viewModel.cancel(); onClose() }) { Text(stringResource(R.string.common_close)) }
            }
            is BleSessionViewModel.UiState.Sending -> {
                CircularProgressIndicator()
                Text(stringResource(s.statusRes))
                readerTime?.let { ReaderTimeCard(it) }
                // Операции по мере выполнения — как на NFC-экране.
                if (ops.isNotEmpty()) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        ops.forEach { BleOpRow(it) }
                    }
                }
            }
            is BleSessionViewModel.UiState.Completed -> {
                val verdict = s.verdict
                when {
                    // ACCESS ran and the door opened.
                    verdict == "OK" -> {
                        Text(
                            stringResource(R.string.ble_session_success),
                            style = MaterialTheme.typography.headlineSmall,
                            color = Color(0xFF1E7E34)
                        )
                        Text(
                            if (s.passageReceiptCaptured)
                                stringResource(R.string.ble_session_receipt_captured)
                            else
                                stringResource(R.string.ble_session_receipt_not_captured),
                            style = MaterialTheme.typography.bodySmall,
                            color = Color.Gray
                        )
                    }
                    // ACCESS ran but the reader denied.
                    verdict != null -> {
                        Text(
                            stringResource(R.string.ble_session_denied, verdict),
                            style = MaterialTheme.typography.headlineSmall,
                            color = Color(0xFFB71C1C)
                        )
                    }
                    // No ACCESS — a delivery/revoke-only session. Show op count.
                    else -> {
                        Text(
                            stringResource(R.string.ble_session_done, s.opsCompleted),
                            style = MaterialTheme.typography.headlineSmall,
                            color = Color(0xFF1E7E34)
                        )
                    }
                }
                readerTime?.let { ReaderTimeCard(it) }
                // Какие операции выполнились и с каким результатом (как на NFC-экране).
                if (ops.isNotEmpty()) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        ops.forEach { BleOpRow(it) }
                    }
                }
                Button(onClick = { onClose() }, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.common_done))
                }
            }
            is BleSessionViewModel.UiState.Failed -> {
                Text(
                    if (s.errorArg != null) stringResource(s.errorRes, s.errorArg)
                    else stringResource(s.errorRes),
                    color = Color.Red
                )
                Button(onClick = { onClose() }, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.common_close))
                }
            }
        }
      }

        HorizontalDivider()
        LogPanel(
            entries = logs,
            onClear = viewModel::clearLogs,
            modifier = Modifier.fillMaxWidth().height(220.dp)
        )
    }
}

/**
 * Одна выполненная операция в списке прогресса BLE-сессии — тот же вид, что
 * CompletedOpRow на NFC-экране (галочка/крест + «имя: результат»).
 */
@Composable
private fun BleOpRow(op: CompletedOp) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            if (op.success) Icons.Default.CheckCircle else Icons.Default.Cancel,
            contentDescription = null,
            tint = if (op.success) Color(0xFF2E7D32) else MaterialTheme.colorScheme.error
        )
        Spacer(Modifier.size(8.dp))
        Text("${op.name}: ${op.result}")
    }
}
