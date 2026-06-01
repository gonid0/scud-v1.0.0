package com.vkrauth.app.ui.navigation

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Assignment
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.currentBackStackEntryAsState
import com.vkrauth.app.R

/**
 * Главный навигационный таб. Каждая запись = одна нижняя вкладка.
 *
 * Tap, Auth, BLE-сессии и пр. — НЕ в табах: они либо триггерятся из контента
 * (большая Hero-кнопка на Home), либо вынесены в pre-auth flow.
 */
enum class TopLevelTab(
    val route: String,
    val icon: ImageVector,
    @StringRes val labelRes: Int,
) {
    Home("home", Icons.Default.Home, R.string.nav_home),
    Permits("permits", Icons.Default.Badge, R.string.nav_permits),
    Keys("keys", Icons.Default.VpnKey, R.string.nav_keys),
    Tasks("tasks", Icons.Default.Assignment, R.string.nav_tasks),
    Settings("settings", Icons.Default.Settings, R.string.nav_settings),
}

/**
 * Bottom navigation bar. Скрывается на экранах вне табов (Auth, Tap, BLE).
 */
@Composable
fun BottomNavBar(navController: NavHostController) {
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
        TopLevelTab.values().forEach { tab ->
            val isSelected = backStackEntry?.destination?.hierarchy?.any {
                it.route?.startsWith(tab.route) == true
            } ?: false
            val label = stringResource(tab.labelRes)
            NavigationBarItem(
                selected = isSelected,
                onClick = {
                    navController.navigate(tab.route) {
                        popUpTo(navController.graph.findStartDestination().id) {
                            saveState = true
                        }
                        launchSingleTop = true
                        restoreState = true
                    }
                },
                icon = { Icon(tab.icon, contentDescription = label) },
                label = { Text(label, style = MaterialTheme.typography.labelSmall) },
                alwaysShowLabel = true,
            )
        }
    }
}

/**
 * Решение «показать ли bottom-nav для текущего route». Простая whitelist-логика:
 * только top-level routes показывают bar.
 */
fun shouldShowBottomBar(route: String?): Boolean {
    if (route == null) return false
    val topLevel = TopLevelTab.values().map { it.route }.toSet() +
        // Sub-routes тоже считаем top-level (например keys?permitId=...).
        setOf("keys?permitId={permitId}")
    return route in topLevel
}

/**
 * Универсальный wrapper: Scaffold с bottom-nav на нужных экранах.
 * Используется в ScudNavHost — оборачивает NavHost.
 */
@Composable
fun AppScaffold(
    navController: NavHostController,
    content: @Composable (Modifier) -> Unit,
) {
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val showBar = shouldShowBottomBar(currentRoute)

    Scaffold(
        bottomBar = { if (showBar) BottomNavBar(navController) },
    ) { padding ->
        content(Modifier.padding(padding))
    }
}
