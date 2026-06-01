package com.vkrauth.app.data.local

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * ANDROID-01: реальные миграции вместо destructive fallback.
 *
 * Раньше БД пересоздавалась при каждом bump-е версии
 * (`fallbackToDestructiveMigration`), что стирало offline-ключи и очереди
 * отчётов на устройстве — недопустимо для SCUD.
 *
 * v1 -> v2: в таблицу issued_keys добавлено поле status
 * (см. [com.vkrauth.app.data.local.entity.IssuedKeyEntity]). Существующие
 * записи бэкфиллятся в "active" через DEFAULT — он обязан совпадать с
 * @ColumnInfo(defaultValue = ...) у сущности, иначе Room.validateMigration упадёт.
 */
val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "ALTER TABLE issued_keys ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    }
}

/**
 * v2 -> v3: в таблицу pending_revoke_intents добавлено поле grantFinalAccess
 * (см. [com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity]).
 *
 * Approach A: тип отзыва («пропуск и отзыв» / «только отзыв») задаётся клиентом
 * через ПОРЯДОК операций в тапе — прошивка не меняется. Флаг хранится в intent'е
 * и читается TapDecisionTree при построении очереди.
 *
 * Existing pending-intent'ы бэкфиллятся в 0 (false = «только отзыв») через DEFAULT
 * — он обязан совпадать с @ColumnInfo(defaultValue = "0") у сущности, иначе
 * Room.validateMigration упадёт.
 */
val MIGRATION_2_3 = object : Migration(2, 3) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            "ALTER TABLE pending_revoke_intents ADD COLUMN grantFinalAccess INTEGER NOT NULL DEFAULT 0"
        )
    }
}

/**
 * v3 -> v4: в таблицу permits добавлено nullable-поле maxTotalIssued
 * (см. [com.vkrauth.app.data.local.entity.PermitEntity]) — пожизненный потолок
 * выпуска ключей по пропуску, приходящий с сервера. NULL = без лимита.
 *
 * Колонка nullable без DEFAULT — существующие записи получают NULL (без лимита),
 * что совпадает с сущностью (Int? = null), поэтому Room.validateMigration проходит.
 */
val MIGRATION_3_4 = object : Migration(3, 4) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE permits ADD COLUMN maxTotalIssued INTEGER")
    }
}

/**
 * v4 -> v5: мультиаккаунт. Таблица account переходит с фиксированного id=1 на
 * accountId (TEXT PK = "$domain|$login") + новые поля login и isActive.
 *
 * Смена первичного ключа в SQLite требует пересоздания таблицы (create-copy-
 * drop-rename). Существующая единственная строка переносится как активный
 * аккаунт (isActive=1); login неизвестен (не хранился) — берём displayName как
 * наилучшее приближение. Схема результирующей таблицы обязана точно совпадать с
 * сущностью [com.vkrauth.app.data.local.entity.AccountEntity], иначе
 * Room.validateMigration упадёт (isActive: NOT NULL DEFAULT 0 ↔ @ColumnInfo "0").
 */
val MIGRATION_4_5 = object : Migration(4, 5) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE account_new (
                accountId TEXT NOT NULL PRIMARY KEY,
                domain TEXT NOT NULL,
                login TEXT NOT NULL,
                userId INTEGER NOT NULL,
                userGroupId TEXT NOT NULL,
                displayName TEXT NOT NULL,
                sessionToken TEXT NOT NULL,
                refreshToken TEXT NOT NULL,
                deviceId TEXT,
                phonePubkeyBase64 TEXT NOT NULL,
                isActive INTEGER NOT NULL DEFAULT 0
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            INSERT INTO account_new (
                accountId, domain, login, userId, userGroupId, displayName,
                sessionToken, refreshToken, deviceId, phonePubkeyBase64, isActive
            )
            SELECT domain || '|' || displayName, domain, displayName, userId,
                   userGroupId, displayName, sessionToken, refreshToken, deviceId,
                   phonePubkeyBase64, 1
            FROM account
            """.trimIndent()
        )
        db.execSQL("DROP TABLE account")
        db.execSQL("ALTER TABLE account_new RENAME TO account")
    }
}
