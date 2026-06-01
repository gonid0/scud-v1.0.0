package com.vkrauth.app.hce

import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class TapSessionHolder @Inject constructor() {
    @Volatile
    private var session: TapSession? = null

    val current: TapSession?
        get() = session?.let {
            if (isExpired(it)) {
                session = null
                null
            } else it
        }

    fun startNew(): TapSession {
        val s = TapSession()
        session = s
        return s
    }

    fun clear() {
        session = null
    }

    private fun isExpired(s: TapSession): Boolean {
        return System.currentTimeMillis() - s.lastActivityAt > TTL_MS
    }

    companion object {
        const val TTL_MS = 30_000L
    }
}
