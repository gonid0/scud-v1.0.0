package com.vkrauth.app.ui.common

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.vkrauth.app.R
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.abs

/**
 * Снимок времени ридера из INFO (§5.2) и его отклонение от часов телефона.
 *
 * @param readerEpochSec время ридера (Unix-секунды), поле reader_time из INFO.
 * @param driftSec       readerEpochSec − время телефона на момент контакта:
 *                       «+» = ридер впереди телефона, «−» = отстаёт.
 */
data class ReaderTimeInfo(
    val readerEpochSec: Long,
    val driftSec: Long,
) {
    companion object {
        /** Считает drift относительно текущих часов телефона в момент получения INFO. */
        fun of(readerEpochSec: Long): ReaderTimeInfo {
            val deviceSec = System.currentTimeMillis() / 1000
            return ReaderTimeInfo(readerEpochSec, readerEpochSec - deviceSec)
        }
    }
}

// Порог рассинхрона = порог, на котором клиент инициирует TIME_SYNC
// (TapDecisionTree.TIME_DRIFT_THRESHOLD_SEC). За ним drift подсвечивается красным.
private const val DRIFT_WARN_SEC = 15L

private val READER_TIME_FORMAT: DateTimeFormatter =
    DateTimeFormatter.ofPattern("HH:mm:ss · dd.MM.yyyy")

/**
 * Блок «время на ридере + отклонение». Один и тот же на NFC-экране (TapScreen),
 * в полноэкранном оверлее и в BLE-сессии — единый источник представления.
 */
@Composable
fun ReaderTimeCard(
    info: ReaderTimeInfo,
    modifier: Modifier = Modifier,
) {
    val inSync = abs(info.driftSec) <= DRIFT_WARN_SEC
    val driftColor = if (inSync) Color(0xFF2E7D32) else MaterialTheme.colorScheme.error

    ElevatedCard(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Default.Schedule,
                contentDescription = null,
                modifier = Modifier.size(32.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text(
                    stringResource(R.string.reader_time_title),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    formatReaderTime(info.readerEpochSec),
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.size(2.dp))
                Text(
                    stringResource(R.string.reader_time_drift, formatDrift(info.driftSec)),
                    style = MaterialTheme.typography.bodyMedium,
                    color = driftColor,
                )
                Text(
                    stringResource(R.string.reader_time_vs_phone),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

private fun formatReaderTime(epochSec: Long): String = runCatching {
    Instant.ofEpochSecond(epochSec).atZone(ZoneId.systemDefault()).format(READER_TIME_FORMAT)
}.getOrDefault(epochSec.toString())

@Composable
private fun formatDrift(driftSec: Long): String {
    val secUnit = stringResource(R.string.reader_time_unit_sec)
    val minUnit = stringResource(R.string.reader_time_unit_min)
    val magnitude = abs(driftSec)
    val sign = when {
        driftSec > 0 -> "+"
        driftSec < 0 -> "−"
        else -> ""
    }
    return when {
        magnitude < 60 -> "$sign$magnitude $secUnit"
        else -> {
            val m = magnitude / 60
            val s = magnitude % 60
            if (s == 0L) "$sign$m $minUnit" else "$sign$m $minUnit $s $secUnit"
        }
    }
}
