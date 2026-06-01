package com.vkrauth.app.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

// Возможные значения поля status. Держим в одном месте, чтобы не было magic-strings.
object IssuedKeyStatus {
    const val ACTIVE            = "active"
    const val REVOKED_BY_SERVER = "revoked_by_server"
    const val REVOKED_BY_READER = "revoked_by_reader"
    const val REVOKED_IN_BLOOM  = "revoked_in_bloom"
    const val EXPIRED           = "expired"
}

@Entity(tableName = "issued_keys")
data class IssuedKeyEntity(
    @PrimaryKey val keyIdHex: String,
    val permitId: String,
    val readerId: String,
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    val fullKeyBytes: ByteArray,
    val belongsToThisDevice: Boolean,
    // Новое поле в v2. Дефолт "active" для бэкфилла существующих записей.
    @ColumnInfo(defaultValue = IssuedKeyStatus.ACTIVE)
    val status: String = IssuedKeyStatus.ACTIVE
) {
    fun isRevoked(): Boolean = status != IssuedKeyStatus.ACTIVE && status != IssuedKeyStatus.EXPIRED
    fun isExpired(nowMs: Long): Boolean = expiresAtMs <= nowMs || status == IssuedKeyStatus.EXPIRED
    fun isUsable(nowMs: Long): Boolean = status == IssuedKeyStatus.ACTIVE && expiresAtMs > nowMs

    // Занимает ли ключ слот n_parallel на сервере. Зеркалит backend-семантику
    // count_active_keys / is_active (repositories/keys.py:67): active И revoked_by_server
    // считаются занимающими слот — revoked_by_server намеренно держит слот, пока ридер
    // не применит новый bloom и worker не флипнет ключ в revoked_in_bloom. UI-счётчик
    // ДОЛЖЕН использовать это (а не isUsable), иначе карточка покажет «0/1» и предложит
    // выпуск, который сервер отклонит 409 n_parallel_exceeded.
    fun occupiesSlot(): Boolean =
        status == IssuedKeyStatus.ACTIVE || status == IssuedKeyStatus.REVOKED_BY_SERVER

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is IssuedKeyEntity) return false
        return keyIdHex == other.keyIdHex
    }

    override fun hashCode(): Int = keyIdHex.hashCode()
}
