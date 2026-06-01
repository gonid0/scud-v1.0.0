package com.vkrauth.app.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Один сохранённый аккаунт (сессия без пароля). Мультиаккаунт: в таблице может
 * быть несколько строк, ровно одна с isActive = true — она используется для API.
 *
 * accountId = "$domain|$login" — стабильный ключ: повторный вход тем же
 * логином на тот же домен перезаписывает строку (обновляет токены), а не плодит
 * дубликаты. Пароль НЕ хранится (хранятся только токены сессии).
 */
@Entity(tableName = "account")
data class AccountEntity(
    @PrimaryKey val accountId: String,
    val domain: String,
    val login: String,
    val userId: Int,
    val userGroupId: String,
    val displayName: String,
    val sessionToken: String,
    val refreshToken: String,
    val deviceId: String?,
    val phonePubkeyBase64: String,
    // Ровно один аккаунт активен. defaultValue "0" обязан совпадать с DEFAULT 0
    // в MIGRATION_4_5, иначе Room.validateMigration упадёт.
    @ColumnInfo(defaultValue = "0") val isActive: Boolean = false,
) {
    companion object {
        fun idFor(domain: String, login: String): String = "$domain|$login"
    }
}
