package com.vkrauth.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.locale.LocaleManager
import com.vkrauth.app.ui.navigation.Screen
import com.vkrauth.app.ui.navigation.ScudNavHost
import com.vkrauth.app.ui.theme.AppTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var authRepository: AuthRepository

    // Применяем выбранную локаль до создания UI. recreate() после смены языка
    // снова проходит через attachBaseContext и пересоздаёт Configuration.
    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleManager.wrap(newBase))
    }

    // POST_NOTIFICATIONS нужен (API 33+), чтобы TapNotifier мог показать полноэкранный
    // оверлей tap-сессии. Тихо запрашиваем один раз при старте; отказ не критичен —
    // оверлей просто не всплывёт, in-app экран Tap продолжит работать.
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        maybeRequestNotificationPermission()
        enableEdgeToEdge()
        setContent {
            AppTheme {
                // UI-2: внешний Scaffold НЕ консумит system-bar insets — иначе они
                // применяются дважды (здесь + во вложенном AppScaffold), давая лишний
                // верхний «козырёк» и двойной нижний nav-bar отступ. Единственный
                // владелец insets — AppScaffold (top через content, bottom через
                // NavigationBar). Этот Scaffold оставляем только ради surface-фона.
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    contentWindowInsets = WindowInsets(0, 0, 0, 0)
                ) { padding ->
                    ScudRoot(
                        authRepository = authRepository,
                        modifier = Modifier.padding(padding)
                    )
                }
            }
        }
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}

@Composable
private fun ScudRoot(authRepository: AuthRepository, modifier: Modifier = Modifier) {
    val navController = rememberNavController()
    val accountState = authRepository.observeAccount().collectAsState(initial = null)
    val account = accountState.value
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    // Авто-переход на Home ТОЛЬКО на переходе «нет аккаунта → есть аккаунт» на экране
    // Auth: это холодный старт с восстановленной сессией или вход из logged-out.
    //
    // Раньше условие было «account != null && route == Auth», и оно ломало кнопку
    // «Добавить аккаунт»: при добавлении активный аккаунт УЖЕ есть, поэтому заход на
    // Auth мгновенно выкидывало на Home (account != null). Теперь добавление аккаунта —
    // это «есть → есть», редирект не срабатывает, и пользователь видит форму входа; на
    // Home после нового логина уводит явный onLoggedIn (AuthViewModel.submit → onSuccess).
    //
    // prevHasAccount хранится через rememberSaveable, поэтому переживает поворот/смерть
    // процесса: после восстановления на Home (account уже есть) ложного редиректа нет.
    val prevHasAccount = androidx.compose.runtime.saveable.rememberSaveable {
        androidx.compose.runtime.mutableStateOf(false)
    }
    androidx.compose.runtime.LaunchedEffect(account?.accountId, currentRoute) {
        val hasAccount = account != null
        if (!prevHasAccount.value && hasAccount && currentRoute == Screen.Auth.route) {
            navController.navigate(Screen.Home.route) {
                popUpTo(Screen.Auth.route) { inclusive = true }
                launchSingleTop = true
            }
        }
        prevHasAccount.value = hasAccount
    }
    androidx.compose.foundation.layout.Box(modifier = modifier) {
        ScudNavHost(navController = navController, startDestination = Screen.Auth.route)
    }
}
