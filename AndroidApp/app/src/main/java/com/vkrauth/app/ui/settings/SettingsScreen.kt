package com.vkrauth.app.ui.settings

import android.app.Activity
import android.widget.Toast
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.PersonAdd
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.vkrauth.app.R
import com.vkrauth.app.data.local.entity.AccountEntity
import com.vkrauth.app.locale.AppLanguage
import com.vkrauth.app.ui.common.SectionCard

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun SettingsScreen(
    onLoggedOut: () -> Unit,
    onAddAccount: () -> Unit,
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val acc = state.account
    val dash = stringResource(R.string.common_empty)
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val copiedMsg = stringResource(R.string.settings_copied)
    var showLanguageDialog by remember { mutableStateOf(false) }

    // Копирование значения в буфер обмена с короткой подсказкой (по зажатию строки).
    val copy: (String) -> Unit = { value ->
        clipboard.setText(AnnotatedString(value))
        Toast.makeText(context, copiedMsg, Toast.LENGTH_SHORT).show()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                windowInsets = WindowInsets(0, 0, 0, 0),
                title = { Text(stringResource(R.string.settings_title), style = MaterialTheme.typography.titleLarge) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // Profile header (активный аккаунт).
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(72.dp)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.primary),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        (acc?.displayName?.firstOrNull()?.uppercase()) ?: dash,
                        color = MaterialTheme.colorScheme.onPrimary,
                        style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Column {
                    Text(
                        acc?.displayName ?: dash,
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        acc?.domain ?: dash,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Переключатель аккаунтов (мультиаккаунт). Активный отмечен, по тапу —
            // переключение; внизу — «Добавить аккаунт» (открывает экран входа).
            SectionCard(title = stringResource(R.string.settings_section_accounts)) {
                state.accounts.forEach { account ->
                    AccountRow(
                        account = account,
                        selected = account.accountId == acc?.accountId,
                        enabled = !state.switching,
                        onClick = { viewModel.switchAccount(account.accountId) },
                    )
                }
                AddAccountRow(enabled = !state.switching, onClick = onAddAccount)
            }

            // Реквизиты активного аккаунта — копируются по долгому нажатию.
            SectionCard(title = stringResource(R.string.settings_section_account)) {
                CopyableRow(
                    label = stringResource(R.string.settings_login_label),
                    display = acc?.login ?: dash,
                    copyValue = acc?.login,
                    onCopy = copy,
                )
                CopyableRow(
                    label = stringResource(R.string.settings_server_label),
                    display = acc?.domain ?: dash,
                    copyValue = acc?.domain,
                    mono = true,
                    onCopy = copy,
                )
                CopyableRow(
                    label = stringResource(R.string.settings_device_id_label),
                    display = acc?.deviceId?.let { it.take(20) + "…" } ?: dash,
                    copyValue = acc?.deviceId,
                    mono = true,
                    onCopy = copy,
                )
            }

            LanguageSettingCard(
                currentLanguage = stringResource(state.language.labelRes),
                onClick = { showLanguageDialog = true },
            )

            SectionCard(title = stringResource(R.string.settings_section_about)) {
                CopyableRow(
                    label = stringResource(R.string.settings_version_label),
                    display = "0.1.0",
                    copyValue = null,
                    onCopy = copy,
                )
                CopyableRow(
                    label = stringResource(R.string.settings_protocol_label),
                    display = "v1 (shared §0)",
                    copyValue = null,
                    onCopy = copy,
                )
                Text(
                    stringResource(R.string.settings_about_description),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            Surface(
                color = MaterialTheme.colorScheme.errorContainer,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Button(
                    onClick = { viewModel.askLogout() },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                        contentColor = MaterialTheme.colorScheme.onError,
                    ),
                    enabled = !state.loggingOut,
                    modifier = Modifier.fillMaxWidth().padding(2.dp),
                ) {
                    Icon(Icons.Default.Logout, contentDescription = null)
                    Spacer(Modifier.size(8.dp))
                    Text(stringResource(R.string.settings_logout_button), fontWeight = FontWeight.SemiBold)
                }
            }
        }

        if (showLanguageDialog) {
            LanguageDialog(
                current = state.language,
                onSelect = { language ->
                    showLanguageDialog = false
                    viewModel.setLanguage(language)
                    (context as? Activity)?.recreate()
                },
                onDismiss = { showLanguageDialog = false },
            )
        }

        if (state.showConfirm) {
            AlertDialog(
                onDismissRequest = viewModel::cancelLogout,
                icon = { Icon(Icons.Default.Logout, contentDescription = null) },
                title = { Text(stringResource(R.string.settings_logout_confirm_title)) },
                text = { Text(stringResource(R.string.settings_logout_confirm_message)) },
                confirmButton = {
                    TextButton(onClick = { viewModel.confirmLogout(onLoggedOut) }) {
                        Text(stringResource(R.string.common_logout), color = MaterialTheme.colorScheme.error)
                    }
                },
                dismissButton = { TextButton(onClick = viewModel::cancelLogout) { Text(stringResource(R.string.common_cancel)) } },
            )
        }
    }
}

/** Строка одного аккаунта в переключателе: радио + имя + login@домен. */
@Composable
private fun AccountRow(
    account: AccountEntity,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = selected, enabled = enabled, onClick = onClick)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = onClick, enabled = enabled)
        Spacer(Modifier.size(8.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                account.displayName.ifBlank { account.login },
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
            Text(
                "${account.login} · ${account.domain}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (selected) {
            Icon(
                Icons.Default.Check,
                contentDescription = stringResource(R.string.settings_account_active),
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(20.dp),
            )
        }
    }
}

/** «Добавить аккаунт» — открывает экран входа для новой сессии. */
@Composable
private fun AddAccountRow(enabled: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = false, enabled = enabled, onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.PersonAdd,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.size(12.dp))
        Text(
            stringResource(R.string.settings_add_account),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}

/** Inline «ключ-значение» с копированием по долгому нажатию (если copyValue != null). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun CopyableRow(
    label: String,
    display: String,
    copyValue: String?,
    mono: Boolean = false,
    onCopy: (String) -> Unit,
) {
    val rowModifier = if (copyValue != null) {
        Modifier.combinedClickable(onClick = {}, onLongClick = { onCopy(copyValue) })
    } else {
        Modifier
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(rowModifier)
            .padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            display,
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = if (mono) FontFamily.Monospace else FontFamily.Default,
            fontWeight = FontWeight.Medium,
        )
    }
}

/** Карточка-строка «Язык» с текущим выбором справа; по клику открывает диалог. */
@Composable
private fun LanguageSettingCard(
    currentLanguage: String,
    onClick: () -> Unit,
) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.elevatedCardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        elevation = CardDefaults.elevatedCardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Default.Language,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(12.dp))
            Text(
                stringResource(R.string.settings_language),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            Text(
                currentLanguage,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** Отдельное окошко выбора языка. */
@Composable
private fun LanguageDialog(
    current: AppLanguage,
    onSelect: (AppLanguage) -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(Icons.Default.Language, contentDescription = null) },
        title = { Text(stringResource(R.string.settings_language)) },
        text = {
            Column {
                AppLanguage.entries.forEach { language ->
                    LanguageRow(
                        label = stringResource(language.labelRes),
                        selected = language == current,
                        onSelect = { onSelect(language) },
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) }
        },
    )
}

@Composable
private fun LanguageRow(
    label: String,
    selected: Boolean,
    onSelect: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = selected, onClick = onSelect)
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = onSelect)
        Spacer(Modifier.size(8.dp))
        Text(
            label,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f),
        )
    }
}
