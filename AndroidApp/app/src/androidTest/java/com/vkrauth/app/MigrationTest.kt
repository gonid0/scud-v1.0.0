package com.vkrauth.app

import androidx.room.testing.MigrationTestHelper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.vkrauth.app.data.local.MIGRATION_3_4
import com.vkrauth.app.data.local.MIGRATION_4_5
import com.vkrauth.app.data.local.ScudDatabase
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Прогон и валидация Room-миграций против экспортированных schema JSON.
 *
 * NB: эти тесты требуют, чтобы build уже сгенерировал актуальную schema (5.json) в
 * app/schemas — это происходит автоматически при сборке (exportSchema=true,
 * room.schemaLocation). Стартуем с v3 (3.json присутствует) и прогоняем обе новые
 * миграции до v5: так не требуется промежуточный 4.json (он не экспортировался,
 * т.к. версия 4 ни разу не собиралась отдельно).
 */
@RunWith(AndroidJUnit4::class)
class MigrationTest {

    private val dbName = "scud-migration-test.db"

    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        ScudDatabase::class.java,
    )

    /**
     * v3 -> v5: MIGRATION_3_4 (permits.maxTotalIssued) + MIGRATION_4_5 (перестройка
     * таблицы account: id -> accountId, +login, +isActive). runMigrationsAndValidate
     * сверяет ИТОГОВУЮ схему с сущностями v5 — упадёт при любом расхождении
     * (тип/NOT NULL/DEFAULT/первичный ключ). Дополнительно проверяем, что
     * единственный аккаунт переносится активным, accountId = "$domain|$login".
     */
    @Test
    fun migrate3To5_appliesPermitCapAndRebuildsAccountTable() {
        helper.createDatabase(dbName, 3).use { db ->
            db.execSQL(
                """
                INSERT INTO account
                  (id, domain, userId, userGroupId, displayName, sessionToken,
                   refreshToken, deviceId, phonePubkeyBase64)
                VALUES
                  (1, 'example.com', 7, 'grp', 'Alice', 'sess', 'refr', 'dev-1', 'pk==')
                """.trimIndent()
            )
        }

        helper.runMigrationsAndValidate(dbName, 5, true, MIGRATION_3_4, MIGRATION_4_5)
            .use { db ->
                db.query("SELECT accountId, login, isActive FROM account").use { c ->
                    check(c.moveToFirst()) { "migrated account row missing" }
                    check(c.getString(0) == "example.com|Alice") {
                        "accountId must be derived from domain|displayName"
                    }
                    check(c.getString(1) == "Alice") { "login backfilled from displayName" }
                    check(c.getInt(2) == 1) { "migrated account must be active" }
                }
            }
    }
}
