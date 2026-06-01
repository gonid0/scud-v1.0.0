package com.vkrauth.app.ui.tap

import android.app.Activity
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Nfc
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import com.vkrauth.app.hce.CompletedOp
import com.vkrauth.app.hce.TapUiState
import com.vkrauth.app.ui.common.LogPanel
import com.vkrauth.app.ui.common.ReaderTimeCard

@Composable
fun TapScreen(
    onClose: () -> Unit,
    viewModel: TapViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val logs by viewModel.logs.collectAsStateWithLifecycle()
    val readerTime by viewModel.readerTime.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val activity = context as? Activity

    DisposableEffect(Unit) {
        activity?.let { viewModel.startTapMode(it) }
        onDispose { activity?.let { viewModel.stopTapMode(it) } }
    }

    // Окно остаётся открытым пока пользователь явно не нажмёт «Закрыть».
    // Автоматическое сворачивание после Completed раньше было — убрано по просьбе UX.

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Column(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            when (val s = state) {
                is TapUiState.Waiting -> {
                    Spacer(Modifier.height(32.dp))
                    Icon(Icons.Default.Nfc, null, modifier = Modifier.size(96.dp))
                    Text(stringResource(R.string.tap_waiting_prompt), style = MaterialTheme.typography.titleMedium)
                    CircularProgressIndicator()
                }
                is TapUiState.InProgress -> {
                    Text(
                        s.readerName ?: stringResource(R.string.tap_unknown_reader),
                        style = MaterialTheme.typography.titleLarge
                    )
                    Text(stringResource(s.currentActivityRes))
                    readerTime?.let { ReaderTimeCard(it) }
                    LazyColumn(modifier = Modifier.fillMaxWidth()) {
                        items(s.completedOps) { op -> CompletedOpRow(op) }
                    }
                }
                is TapUiState.Completed -> {
                    Icon(
                        if (s.success) Icons.Default.CheckCircle else Icons.Default.Error,
                        contentDescription = null,
                        modifier = Modifier.size(96.dp),
                        tint = if (s.success) Color(0xFF2E7D32) else MaterialTheme.colorScheme.primary
                    )
                    Text(
                        if (s.finalMessageArg != null) stringResource(s.finalMessageRes, s.finalMessageArg)
                        else stringResource(s.finalMessageRes),
                        style = MaterialTheme.typography.titleLarge
                    )
                    readerTime?.let { ReaderTimeCard(it) }
                    LazyColumn(modifier = Modifier.fillMaxWidth()) {
                        items(s.completedOps) { op -> CompletedOpRow(op) }
                    }
                    Button(onClick = onClose) { Text(stringResource(R.string.common_close)) }
                }
                is TapUiState.Failed -> {
                    Icon(Icons.Default.Error, null, modifier = Modifier.size(96.dp))
                    Text(stringResource(s.messageRes), color = MaterialTheme.colorScheme.error)
                    Button(onClick = onClose) { Text(stringResource(R.string.common_close)) }
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

@Composable
private fun CompletedOpRow(op: CompletedOp) {
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

