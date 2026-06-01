package com.vkrauth.app.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "pending_revoke_intents")
data class PendingRevokeIntentEntity(
    @PrimaryKey(autoGenerate = true) val intentId: Long = 0,
    val targetReaderId: String,
    val targetKeyIdHex: String,
    val targetFullKeyBytes: ByteArray,
    val createdAtMs: Long,
    val status: String,
    // Approach A: тип отзыва выбирается КЛИЕНТОМ через ПОРЯДОК операций в тапе.
    //   true  → «Пропуск и отзыв»: ACCESS ставится ПЕРЕД REVOKE_KEY этого ключа
    //           (ключ ещё валиден, не в blacklist) → дверь откроется, потом отзыв.
    //   false → «Только отзыв»: ACCESS этого ключа не ставится вовсе → дверь молчит.
    // defaultValue "0" обязан совпадать с DEFAULT 0 в MIGRATION_2_3, иначе
    // Room.validateMigration упадёт.
    @ColumnInfo(defaultValue = "0") val grantFinalAccess: Boolean = false
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is PendingRevokeIntentEntity) return false
        return intentId == other.intentId
    }

    override fun hashCode(): Int = intentId.hashCode()
}
