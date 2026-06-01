package com.vkrauth.app.ui.tap

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.nfc.NfcAdapter
import android.nfc.cardemulation.CardEmulation
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.hce.ScudHceService
import com.vkrauth.app.hce.TapController
import com.vkrauth.app.hce.TapLog
import com.vkrauth.app.hce.TapLogEntry
import com.vkrauth.app.hce.TapUiState
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class TapViewModel @Inject constructor(
    private val tapController: TapController,
    private val tapLog: TapLog,
    @ApplicationContext private val context: Context
) : ViewModel() {

    val state: StateFlow<TapUiState> = tapController.uiState
    val logs: StateFlow<List<TapLogEntry>> = tapLog.entries
    val readerTime: StateFlow<com.vkrauth.app.ui.common.ReaderTimeInfo?> = tapController.readerTime

    fun clearLogs() = tapLog.clear()

    private var timeoutJob: Job? = null

    fun startTapMode(activity: Activity) {
        val adapter = NfcAdapter.getDefaultAdapter(context) ?: return
        val cardEmulation = CardEmulation.getInstance(adapter)
        val component = ComponentName(context, ScudHceService::class.java)
        try {
            cardEmulation.setPreferredService(activity, component)
        } catch (_: Exception) {
        }
        // Внутренний экран Tap сам показывает этапы → подавляем полноэкранный оверлей.
        tapController.inAppTapUiVisible = true
        tapController.reset()
        timeoutJob?.cancel()
        timeoutJob = viewModelScope.launch {
            delay(5 * 60 * 1000L)
            tapController.forceTimeout()
        }
    }

    fun stopTapMode(activity: Activity) {
        timeoutJob?.cancel()
        // Экран Tap уходит с переднего плана → снова разрешаем оверлей по тапу.
        tapController.inAppTapUiVisible = false
        val adapter = NfcAdapter.getDefaultAdapter(context) ?: return
        val cardEmulation = CardEmulation.getInstance(adapter)
        try {
            cardEmulation.unsetPreferredService(activity)
        } catch (_: Exception) {
        }
    }
}
