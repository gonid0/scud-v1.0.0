package com.vkrauth.app.domain.model

data class Account(
    val domain: String,
    val userId: Int,
    val userGroupId: String,
    val displayName: String,
    val deviceId: String?
)

data class Permit(
    val permitId: String,
    val readerIdHex: String,
    val displayName: String,
    val description: String?,
    val validUntilMs: Long,
    val nParallel: Int,
    val activeKeysCount: Int,
    val maxTokenTtlSeconds: Int,
    val hasOwnKey: Boolean = false,
    val hasActiveGrant: Boolean = false
)

data class IssuedKey(
    val keyIdHex: String,
    val permitId: String,
    val readerIdHex: String,
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    val belongsToThisDevice: Boolean
)

data class Reader(
    val readerIdHex: String,
    val displayName: String,
    val description: String?
)
