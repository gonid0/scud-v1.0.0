package com.vkrauth.app.di

import android.content.Context
import androidx.room.Room
import com.vkrauth.app.data.local.MIGRATION_1_2
import com.vkrauth.app.data.local.MIGRATION_2_3
import com.vkrauth.app.data.local.MIGRATION_3_4
import com.vkrauth.app.data.local.MIGRATION_4_5
import com.vkrauth.app.data.local.ScudDatabase
import com.vkrauth.app.data.local.dao.AccountDao
import com.vkrauth.app.data.local.dao.ContactHistoryDao
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.dao.PermitDao
import com.vkrauth.app.data.local.dao.ReaderDao
import com.vkrauth.app.data.local.dao.TimeGrantDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context): ScudDatabase =
        Room.databaseBuilder(context, ScudDatabase::class.java, "scud.db")
            .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5)
            .build()

    @Provides
    fun provideAccountDao(db: ScudDatabase): AccountDao = db.accountDao()

    @Provides
    fun providePermitDao(db: ScudDatabase): PermitDao = db.permitDao()

    @Provides
    fun provideIssuedKeyDao(db: ScudDatabase): IssuedKeyDao = db.issuedKeyDao()

    @Provides
    fun provideTimeGrantDao(db: ScudDatabase): TimeGrantDao = db.timeGrantDao()

    @Provides
    fun provideReaderDao(db: ScudDatabase): ReaderDao = db.readerDao()

    @Provides
    fun providePendingFilterDeliveryDao(db: ScudDatabase): PendingFilterDeliveryDao =
        db.pendingFilterDeliveryDao()

    @Provides
    fun providePendingRevokeIntentDao(db: ScudDatabase): PendingRevokeIntentDao =
        db.pendingRevokeIntentDao()

    @Provides
    fun provideOutgoingReportDao(db: ScudDatabase): OutgoingReportDao = db.outgoingReportDao()

    @Provides
    fun provideContactHistoryDao(db: ScudDatabase): ContactHistoryDao = db.contactHistoryDao()
}
