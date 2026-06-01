package com.vkrauth.app.di

import android.content.Context
import com.vkrauth.app.data.crypto.KeyManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object CryptoModule {

    @Provides
    @Singleton
    fun provideKeyManager(@ApplicationContext context: Context): KeyManager =
        KeyManager(context)
}
