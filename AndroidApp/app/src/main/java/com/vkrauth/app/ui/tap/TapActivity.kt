package com.vkrauth.app.ui.tap

import android.content.Context
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.ViewModel
import com.vkrauth.app.hce.TapController
import com.vkrauth.app.hce.TapUiState
import com.vkrauth.app.locale.LocaleManager
import com.vkrauth.app.ui.common.ReaderTimeInfo
import com.vkrauth.app.ui.theme.AppTheme
import dagger.hilt.android.AndroidEntryPoint
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject

/**
 * Полноэкранный оверлей tap-сессии (как «карточка» Google Pay). Запускается
 * системой по full-screen-intent из [com.vkrauth.app.hce.TapNotifier], когда
 * телефон поднесли к ридеру, а внутренний экран Tap не открыт. Только наблюдает
 * [TapController.uiState] — на сам RF-обмен не влияет.
 *
 * Отдельная Activity (а не маршрут в MainActivity), чтобы:
 *  - показываться поверх локскрина сразу после разблокировки (showWhenLocked);
 *  - не трогать основной back-stack приложения (excludeFromRecents).
 */
@AndroidEntryPoint
class TapActivity : ComponentActivity() {

    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(LocaleManager.wrap(base))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Появляемся поверх локскрина и будим экран (кейс «разблокировал → сессия идёт»).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        setContent {
            AppTheme {
                TapOverlay(onClose = { finish() })
            }
        }
    }
}

@HiltViewModel
class TapOverlayViewModel @Inject constructor(
    tapController: TapController,
) : ViewModel() {
    val state: StateFlow<TapUiState> = tapController.uiState
    val readerTime: StateFlow<ReaderTimeInfo?> = tapController.readerTime
}
