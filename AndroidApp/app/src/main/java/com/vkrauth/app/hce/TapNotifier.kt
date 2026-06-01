package com.vkrauth.app.hce

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.vkrauth.app.R
import com.vkrauth.app.di.ApplicationScope
import com.vkrauth.app.ui.tap.TapActivity
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Поднимает полноэкранный оверлей tap-сессии (как «карточка» Google Pay) тогда,
 * когда телефон поднесли к ридеру, а внутренний экран Tap НЕ открыт (фон / другой
 * экран приложения / экран только что разблокирован после requireDeviceUnlock).
 *
 * Механика — единственный санкционированный способ показать Activity из фона на
 * современном Android: high-importance уведомление с setFullScreenIntent(). Если
 * full-screen-intent разрешён (API < 34 — по умолчанию; API 34+ — нужно явное
 * разрешение USE_FULL_SCREEN_INTENT в настройках), система сразу разворачивает
 * [TapActivity]; иначе показывает heads-up, по тапу на который открывается тот же
 * экран. Сам RF-обмен оверлей НЕ тормозит — он только наблюдает [TapController.uiState].
 *
 * Запускается один раз из [com.vkrauth.app.ScudApplication.onCreate] через [start].
 */
@Singleton
class TapNotifier @Inject constructor(
    @ApplicationContext private val context: Context,
    private val tapController: TapController,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private val manager = NotificationManagerCompat.from(context)

    // Оверлей поднимаем РОВНО один раз на сессию (на переходе Waiting→живое состояние),
    // чтобы каждое обновление InProgress не перезапускало Activity.
    @Volatile
    private var shownForSession = false

    fun start() {
        ensureChannel()
        scope.launch {
            tapController.uiState.collect { state -> onState(state) }
        }
    }

    private fun onState(state: TapUiState) {
        // Внутренний экран Tap сам рисует этапы — оверлей/уведомление не нужны.
        if (tapController.inAppTapUiVisible) {
            if (shownForSession) cancel()
            shownForSession = false
            return
        }
        when (state) {
            is TapUiState.Waiting -> {
                shownForSession = false
                cancel()
            }
            is TapUiState.InProgress -> {
                val text = context.getString(R.string.tap_exchanging_with_reader)
                val title = state.readerName ?: context.getString(R.string.tap_overlay_session_title)
                if (!shownForSession) {
                    shownForSession = true
                    post(title, text, ongoing = true, withFullScreen = true)
                } else {
                    post(title, text, ongoing = true, withFullScreen = false)
                }
            }
            is TapUiState.Completed -> {
                if (!shownForSession) return
                val title = state.readerName ?: context.getString(R.string.tap_overlay_session_title)
                val text = if (state.finalMessageArg != null) {
                    context.getString(state.finalMessageRes, state.finalMessageArg)
                } else {
                    context.getString(state.finalMessageRes)
                }
                // Результат — авто-исчезает; Activity покажет «галочку» и сама закроется.
                post(title, text, ongoing = false, withFullScreen = false, timeoutMs = RESULT_TIMEOUT_MS)
            }
            is TapUiState.Failed -> {
                if (!shownForSession) return
                val title = context.getString(R.string.tap_overlay_session_title)
                post(title, context.getString(state.messageRes), ongoing = false, withFullScreen = false, timeoutMs = RESULT_TIMEOUT_MS)
            }
        }
    }

    private fun post(
        title: String,
        text: String,
        ongoing: Boolean,
        withFullScreen: Boolean,
        timeoutMs: Long = 0L,
    ) {
        if (!hasPostPermission()) return

        val intent = Intent(context, TapActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pi = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_tap_nfc)
            .setContentTitle(title)
            .setContentText(text)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOngoing(ongoing)
            .setAutoCancel(!ongoing)
            .setOnlyAlertOnce(true)
            .setContentIntent(pi)
        if (withFullScreen) builder.setFullScreenIntent(pi, /* highPriority = */ true)
        if (timeoutMs > 0L) builder.setTimeoutAfter(timeoutMs)

        try {
            manager.notify(NOTIFICATION_ID, builder.build())
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS отозвано между проверкой и notify — молча пропускаем.
        }
    }

    private fun cancel() {
        manager.cancel(NOTIFICATION_ID)
    }

    private fun hasPostPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.tap_overlay_channel_name),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = context.getString(R.string.tap_overlay_channel_desc)
            setShowBadge(false)
            enableVibration(false)
        }
        val sys = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        sys.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "tap_session"
        private const val NOTIFICATION_ID = 0x5C0D // "SCUD"-ish
        private const val RESULT_TIMEOUT_MS = 8_000L
    }
}
