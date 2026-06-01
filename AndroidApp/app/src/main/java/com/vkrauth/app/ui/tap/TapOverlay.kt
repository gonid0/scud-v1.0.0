package com.vkrauth.app.ui.tap

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Nfc
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import com.vkrauth.app.hce.TapUiState
import com.vkrauth.app.ui.common.ReaderTimeCard
import kotlinx.coroutines.delay

private const val AUTO_DISMISS_MS = 2_800L

@Composable
fun TapOverlay(
    onClose: () -> Unit,
    viewModel: TapOverlayViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val readerTime by viewModel.readerTime.collectAsStateWithLifecycle()

    // Как в Google Pay: после финала (успех/отказ/ошибка) карточка сама исчезает.
    LaunchedEffect(state::class) {
        if (state is TapUiState.Completed || state is TapUiState.Failed) {
            delay(AUTO_DISMISS_MS)
            onClose()
        }
    }

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            when (val s = state) {
                is TapUiState.Waiting -> {
                    Icon(Icons.Default.Nfc, null, modifier = Modifier.size(112.dp),
                        tint = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(24.dp))
                    Text(
                        stringResource(R.string.tap_overlay_hold),
                        style = MaterialTheme.typography.titleMedium,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(24.dp))
                    CircularProgressIndicator()
                }

                is TapUiState.InProgress -> {
                    Icon(Icons.Default.Nfc, null, modifier = Modifier.size(96.dp),
                        tint = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(20.dp))
                    Text(
                        s.readerName ?: stringResource(R.string.tap_unknown_reader),
                        style = MaterialTheme.typography.titleLarge,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        stringResource(s.currentActivityRes),
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(24.dp))
                    CircularProgressIndicator()
                    readerTime?.let {
                        Spacer(Modifier.height(24.dp))
                        ReaderTimeCard(it)
                    }
                }

                is TapUiState.Completed -> {
                    val ok = s.success
                    Icon(
                        if (ok) Icons.Default.CheckCircle else Icons.Default.Error,
                        contentDescription = null,
                        modifier = Modifier.size(128.dp),
                        tint = if (ok) Color(0xFF2E7D32) else MaterialTheme.colorScheme.error,
                    )
                    Spacer(Modifier.height(24.dp))
                    s.readerName?.let {
                        Text(it, style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(4.dp))
                    }
                    Text(
                        if (s.finalMessageArg != null) stringResource(s.finalMessageRes, s.finalMessageArg)
                        else stringResource(s.finalMessageRes),
                        style = MaterialTheme.typography.headlineSmall,
                        textAlign = TextAlign.Center,
                    )
                    readerTime?.let {
                        Spacer(Modifier.height(20.dp))
                        ReaderTimeCard(it)
                    }
                }

                is TapUiState.Failed -> {
                    Icon(Icons.Default.Error, null, modifier = Modifier.size(128.dp),
                        tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.height(24.dp))
                    Text(
                        stringResource(s.messageRes),
                        style = MaterialTheme.typography.headlineSmall,
                        color = MaterialTheme.colorScheme.error,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            Spacer(Modifier.height(32.dp))
            TextButton(onClick = onClose) {
                Text(stringResource(R.string.common_close))
            }
        }
    }
}
