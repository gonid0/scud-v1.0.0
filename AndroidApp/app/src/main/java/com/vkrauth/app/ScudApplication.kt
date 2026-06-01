package com.vkrauth.app

import android.app.Application
import android.content.Context
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.di.ApplicationScope
import com.vkrauth.app.hce.TapNotifier
import com.vkrauth.app.locale.LocaleManager
import dagger.hilt.android.HiltAndroidApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltAndroidApp
class ScudApplication : Application() {

    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    @ApplicationScope
    lateinit var appScope: CoroutineScope

    // Полноэкранный оверлей tap-сессии: наблюдает TapController.uiState и всплывает,
    // когда телефон поднесли к ридеру вне открытого экрана Tap (фон/локскрин).
    @Inject
    lateinit var tapNotifier: TapNotifier

    // Выбранная локаль действует и для не-Activity контекстов (applicationContext.getString).
    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(LocaleManager.wrap(base))
    }

    override fun onCreate() {
        super.onCreate()
        tapNotifier.start()
        appScope.launch {
            authRepository.restoreFromStorage()
        }
    }
}
