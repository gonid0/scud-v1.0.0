package com.vkrauth.app.data.remote

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit

class ScudApiFactory(
    private val okHttp: OkHttpClient,
    private val json: Json
) {
    private val cache = mutableMapOf<String, ScudApi>()

    @Synchronized
    fun create(baseUrl: String): ScudApi {
        val normalized = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        return cache.getOrPut(normalized) {
            Retrofit.Builder()
                .baseUrl(normalized)
                .client(okHttp)
                .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                .build()
                .create(ScudApi::class.java)
        }
    }
}
