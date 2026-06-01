package com.vkrauth.app.data.remote

import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject

class AuthInterceptor @Inject constructor(
    private val accountProvider: dagger.Lazy<CurrentAccount>,
    private val refreshProvider: dagger.Lazy<RefreshFlow>
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val account = accountProvider.get().get() ?: return chain.proceed(chain.request())

        var request = chain.request().newBuilder()
            .header("Authorization", "Bearer ${account.sessionToken}")
            .build()

        var response = chain.proceed(request)

        if (response.code == 401 && account.refreshToken.isNotBlank()) {
            response.close()
            val refreshed = runBlocking { refreshProvider.get().attemptRefresh(account.refreshToken) }
            if (refreshed != null) {
                request = chain.request().newBuilder()
                    .header("Authorization", "Bearer ${refreshed.sessionToken}")
                    .build()
                response = chain.proceed(request)
            }
        }

        return response
    }
}
