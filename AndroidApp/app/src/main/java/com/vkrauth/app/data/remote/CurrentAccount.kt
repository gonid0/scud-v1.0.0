package com.vkrauth.app.data.remote

import java.util.concurrent.atomic.AtomicReference
import javax.inject.Inject
import javax.inject.Singleton

data class AuthSnapshot(
    val sessionToken: String,
    val refreshToken: String,
    val baseUrl: String
)

@Singleton
class CurrentAccount @Inject constructor() {
    private val ref = AtomicReference<AuthSnapshot?>(null)

    fun get(): AuthSnapshot? = ref.get()

    fun set(snapshot: AuthSnapshot?) {
        ref.set(snapshot)
    }

    fun updateTokens(sessionToken: String, refreshToken: String) {
        val cur = ref.get() ?: return
        ref.set(cur.copy(sessionToken = sessionToken, refreshToken = refreshToken))
    }
}
