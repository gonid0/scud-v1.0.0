package com.vkrauth.app.hce

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton

enum class TapLogLevel { INFO, SEND, RECV, WARN, ERROR }

data class TapLogEntry(
    val timestampMs: Long,
    val level: TapLogLevel,
    val message: String,
    val formattedTime: String = formatTime(timestampMs)
) {
    companion object {
        private val TIME_FMT = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
        private fun formatTime(ms: Long): String =
            synchronized(TIME_FMT) { TIME_FMT.format(Date(ms)) }
    }
}

@Singleton
class TapLog @Inject constructor(
    @ApplicationContext context: Context
) {

    private val _entries = MutableStateFlow<List<TapLogEntry>>(emptyList())
    val entries: StateFlow<List<TapLogEntry>> = _entries.asStateFlow()

    val logFile: File =
        (context.getExternalFilesDir(null) ?: context.filesDir).resolve("tap.log")

    private val writerExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "TapLog-writer").apply { isDaemon = true }
    }

    init {
        writerExecutor.execute {
            try {
                logFile.parentFile?.mkdirs()
                FileWriter(logFile, /*append=*/true).use { w ->
                    w.appendLine("---- session started ${DATE_FMT.format(Date())} ----")
                }
            } catch (t: Throwable) {
                Log.e(TAG, "logFile open failed", t)
            }
        }
    }

    fun clear() {
        _entries.value = emptyList()
    }

    fun clearFile() {
        writerExecutor.execute {
            try {
                logFile.writeText("")
            } catch (t: Throwable) {
                Log.e(TAG, "logFile clear failed", t)
            }
        }
    }

    fun log(level: TapLogLevel, message: String) {
        val entry = TapLogEntry(System.currentTimeMillis(), level, message)
        _entries.update { prev ->
            val combined = prev + entry
            if (combined.size > MAX) combined.drop(combined.size - MAX) else combined
        }
        writerExecutor.execute {
            try {
                FileWriter(logFile, /*append=*/true).use { w ->
                    w.append(entry.formattedTime)
                    w.append(' ')
                    w.append(level.name.padEnd(5))
                    w.append(' ')
                    w.append(message)
                    w.append('\n')
                }
            } catch (t: Throwable) {
                Log.e(TAG, "logFile append failed", t)
            }
        }
    }

    fun info(msg: String) = log(TapLogLevel.INFO, msg)
    fun send(msg: String) = log(TapLogLevel.SEND, msg)
    fun recv(msg: String) = log(TapLogLevel.RECV, msg)
    fun warn(msg: String) = log(TapLogLevel.WARN, msg)
    fun error(msg: String) = log(TapLogLevel.ERROR, msg)

    companion object {
        private const val TAG = "TapLog"
        private const val MAX = 300
        private val DATE_FMT = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
    }
}
