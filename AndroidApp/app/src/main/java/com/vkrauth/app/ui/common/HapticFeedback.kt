package com.vkrauth.app.ui.common

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Тактильная + звуковая обратная связь по результату tap-сессии.
 *
 * UX-правило: в момент verdict от ридера пользователь часто НЕ смотрит на
 * экран (телефон у дверного ридера). Поэтому вибрация и короткий beep —
 * обязательны для accessibility и просто хорошего опыта.
 *
 * Паттерны подобраны эмпирически:
 *   - GRANTED: один короткий «бойкий» tick → ассоциируется с «click — открыто».
 *   - DENIED: два коротких «жёстких» удара → tactile «no-no».
 *   - INFO (нейтральное завершение без вердикта): один очень короткий tick.
 *
 * Звук — через ToneGenerator (не нужны ассеты в APK; звук системный, всегда
 * есть). Если пользователь молча тап'нул и не дождался — звуков нет.
 *
 * Если устройство не имеет вибромотора (планшеты) — методы no-op без падений.
 */
@Singleton
class HapticFeedback @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val vibrator: Vibrator? by lazy {
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
                vm?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }
        }.getOrNull()
    }

    private fun newTone(): ToneGenerator? = runCatching {
        // Volume 60/100 — слышимо в обычной обстановке, не оглушает в тишине.
        ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60)
    }.getOrNull()

    fun granted() {
        vibrate(longArrayOf(0, 80))
        playTone(ToneGenerator.TONE_PROP_PROMPT, 120)
    }

    fun denied() {
        vibrate(longArrayOf(0, 60, 80, 120))
        playTone(ToneGenerator.TONE_CDMA_NETWORK_BUSY, 200)
    }

    fun neutral() {
        vibrate(longArrayOf(0, 30))
    }

    private fun vibrate(pattern: LongArray) {
        val v = vibrator ?: return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // -1 = no repeat. Amplitudes default → DEFAULT_AMPLITUDE.
                v.vibrate(VibrationEffect.createWaveform(pattern, -1))
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(pattern, -1)
            }
        } catch (e: Exception) {
            Log.w(TAG, "vibrate failed: ${e.message}")
        }
    }

    private fun playTone(toneType: Int, durationMs: Int) {
        val tone = newTone() ?: return
        try {
            tone.startTone(toneType, durationMs)
            // ToneGenerator нужно release'ить вручную — иначе SoundPool-handle
            // утекает. Делаем это с задержкой через runnable, чтобы tone успел проиграться.
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(
                { runCatching { tone.release() } },
                (durationMs + 50).toLong()
            )
        } catch (e: Exception) {
            Log.w(TAG, "tone failed: ${e.message}")
            runCatching { tone.release() }
        }
    }

    companion object { private const val TAG = "HapticFeedback" }
}
