package com.vkrauth.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.vkrauth.app.ui.auth.AuthScreen
import com.vkrauth.app.ui.ble.BleSessionScreen
import com.vkrauth.app.ui.home.HomeScreen
import com.vkrauth.app.ui.keys.KeysScreen
import com.vkrauth.app.ui.permits.PermitDetailScreen
import com.vkrauth.app.ui.permits.PermitsScreen
import com.vkrauth.app.ui.settings.SettingsScreen
import com.vkrauth.app.ui.tap.TapScreen
import com.vkrauth.app.ui.tasks.TasksScreen
import java.net.URLDecoder
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

sealed class Screen(val route: String) {
    data object Auth : Screen("auth")
    data object Home : Screen("home")
    data object Tap : Screen("tap")
    data object Permits : Screen("permits")
    data object PermitDetail : Screen("permits/{permitId}") {
        fun of(permitId: String) = "permits/$permitId"
    }
    data object Keys : Screen("keys?permitId={permitId}") {
        fun of(permitId: String? = null) = if (permitId != null) "keys?permitId=$permitId" else "keys"
    }
    data object Tasks : Screen("tasks")
    data object Settings : Screen("settings")
    data object BleSession : Screen("ble_session/{address}/{shortId}") {
        fun create(address: String, shortId: String): String =
            "ble_session/" +
                URLEncoder.encode(address, StandardCharsets.UTF_8.name()) + "/" +
                URLEncoder.encode(shortId, StandardCharsets.UTF_8.name())
    }
}

@Composable
fun ScudNavHost(
    navController: NavHostController = rememberNavController(),
    startDestination: String = Screen.Auth.route,
) {
    AppScaffold(navController) { contentModifier ->
        NavHost(
            navController = navController,
            startDestination = startDestination,
            modifier = contentModifier,
        ) {
            composable(Screen.Auth.route) {
                AuthScreen(onLoggedIn = {
                    navController.navigate(Screen.Home.route) {
                        popUpTo(Screen.Auth.route) { inclusive = true }
                        // launchSingleTop: не плодим дубль Home, если ScudRoot уже
                        // навигировал по появлению активного аккаунта (add-account flow).
                        launchSingleTop = true
                    }
                })
            }
            composable(Screen.Home.route) {
                HomeScreen(
                    onTap = { navController.navigate(Screen.Tap.route) },
                    onSettings = { navController.navigate(Screen.Settings.route) },
                    onOpenBleReader = { address, shortId ->
                        navController.navigate(Screen.BleSession.create(address, shortId))
                    },
                )
            }
            composable(Screen.Tap.route) {
                TapScreen(onClose = { navController.popBackStack() })
            }
            composable(Screen.Permits.route) {
                PermitsScreen(onOpenKeys = { permitId ->
                    navController.navigate(Screen.Keys.of(permitId))
                })
            }
            composable(
                route = Screen.PermitDetail.route,
                arguments = listOf(navArgument("permitId") { type = NavType.StringType }),
            ) { backStack ->
                val permitId = backStack.arguments?.getString("permitId") ?: return@composable
                PermitDetailScreen(
                    permitId = permitId,
                    onBack = { navController.popBackStack() },
                    onOpenKeys = { navController.navigate(Screen.Keys.of(permitId)) },
                )
            }
            composable(
                route = Screen.Keys.route,
                arguments = listOf(navArgument("permitId") {
                    type = NavType.StringType
                    nullable = true
                    defaultValue = null
                }),
            ) { backStack ->
                val permitId = backStack.arguments?.getString("permitId")
                KeysScreen(permitIdFilter = permitId, onBack = { navController.popBackStack() })
            }
            composable(Screen.Tasks.route) {
                TasksScreen(onBack = { navController.popBackStack() })
            }
            composable(Screen.Settings.route) {
                SettingsScreen(
                    onLoggedOut = {
                        navController.navigate(Screen.Auth.route) {
                            popUpTo(0) { inclusive = true }
                        }
                    },
                    // «Добавить аккаунт» — открываем экран входа поверх стека. После
                    // успешного логина AuthScreen уведёт на Home (popUpTo Auth inclusive),
                    // новый аккаунт станет активным.
                    onAddAccount = { navController.navigate(Screen.Auth.route) },
                )
            }
            composable(
                route = Screen.BleSession.route,
                arguments = listOf(
                    navArgument("address") { type = NavType.StringType },
                    navArgument("shortId") { type = NavType.StringType },
                ),
            ) { backStack ->
                val encodedAddress = backStack.arguments?.getString("address") ?: return@composable
                val encodedShortId = backStack.arguments?.getString("shortId") ?: return@composable
                val address = URLDecoder.decode(encodedAddress, StandardCharsets.UTF_8.name())
                val shortId = URLDecoder.decode(encodedShortId, StandardCharsets.UTF_8.name())
                BleSessionScreen(
                    deviceAddress = address,
                    shortId = shortId,
                    onClose = { navController.popBackStack() },
                )
            }
        }
    }
}
