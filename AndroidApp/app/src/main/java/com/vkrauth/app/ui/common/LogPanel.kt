package com.vkrauth.app.ui.common

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.vkrauth.app.R
import com.vkrauth.app.hce.TapLogEntry
import com.vkrauth.app.hce.TapLogLevel

/**
 * Общая панель лога для NFC- и BLE-экранов.
 *
 * Рендерит entries (моноширинный шрифт, цвет на уровень, время+уровень+сообщение)
 * внутри [SelectionContainer] — текст выделяемый. Тулбар: Copy (копирует весь лог
 * в буфер обмена через [LocalClipboardManager]) и Clear.
 */
@Composable
fun LogPanel(
    entries: List<TapLogEntry>,
    onClear: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current
    val copiedMsg = stringResource(R.string.log_copied)
    val listState = rememberLazyListState()
    LaunchedEffect(entries.size) {
        if (entries.isNotEmpty()) listState.animateScrollToItem(entries.size - 1)
    }
    Column(modifier = modifier) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                stringResource(R.string.tap_log_title, entries.size),
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier.weight(1f)
            )
            TextButton(
                onClick = {
                    clipboard.setText(AnnotatedString(entries.joinToString("\n") { it.asLine() }))
                    Toast.makeText(context, copiedMsg, Toast.LENGTH_SHORT).show()
                },
                enabled = entries.isNotEmpty()
            ) { Text(stringResource(R.string.log_copy)) }
            TextButton(onClick = onClear) { Text(stringResource(R.string.log_clear)) }
        }
        Box(
            modifier = Modifier
                .fillMaxSize()
                .clip(RoundedCornerShape(8.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .padding(8.dp)
        ) {
            if (entries.isEmpty()) {
                Text(
                    stringResource(R.string.tap_log_empty),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                SelectionContainer(modifier = Modifier.fillMaxSize()) {
                    LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
                        items(entries) { entry -> LogEntryRow(entry) }
                    }
                }
            }
        }
    }
}

private fun TapLogEntry.asLine(): String =
    "$formattedTime ${level.name.take(1)} $message"

@Composable
private fun LogEntryRow(entry: TapLogEntry) {
    val color = when (entry.level) {
        TapLogLevel.ERROR -> MaterialTheme.colorScheme.error
        TapLogLevel.WARN -> Color(0xFFB26A00)
        TapLogLevel.SEND -> Color(0xFF1565C0)
        TapLogLevel.RECV -> Color(0xFF2E7D32)
        TapLogLevel.INFO -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        text = entry.asLine(),
        style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
        color = color,
        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp)
    )
}
