package com.vkrauth.app.data.local

import androidx.room.Database
import androidx.room.RoomDatabase
import com.vkrauth.app.data.local.dao.AccountDao
import com.vkrauth.app.data.local.dao.ContactHistoryDao
import com.vkrauth.app.data.local.dao.IssuedKeyDao
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.local.dao.PendingFilterDeliveryDao
import com.vkrauth.app.data.local.dao.PendingRevokeIntentDao
import com.vkrauth.app.data.local.dao.PermitDao
import com.vkrauth.app.data.local.dao.ReaderDao
import com.vkrauth.app.data.local.dao.TimeGrantDao
import com.vkrauth.app.data.local.entity.AccountEntity
import com.vkrauth.app.data.local.entity.ContactHistoryEntity
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import com.vkrauth.app.data.local.entity.OutgoingReportEntity
import com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity
import com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.data.local.entity.ReaderKnownEntity
import com.vkrauth.app.data.local.entity.TimeGrantEntity

@Database(
    entities = [
        AccountEntity::class,
        PermitEntity::class,
        IssuedKeyEntity::class,
        TimeGrantEntity::class,
        ReaderKnownEntity::class,
        PendingFilterDeliveryEntity::class,
        PendingRevokeIntentEntity::class,
        OutgoingReportEntity::class,
        ContactHistoryEntity::class
    ],
    version = 5,
    exportSchema = true
)
abstract class ScudDatabase : RoomDatabase() {
    abstract fun accountDao(): AccountDao
    abstract fun permitDao(): PermitDao
    abstract fun issuedKeyDao(): IssuedKeyDao
    abstract fun timeGrantDao(): TimeGrantDao
    abstract fun readerDao(): ReaderDao
    abstract fun pendingFilterDeliveryDao(): PendingFilterDeliveryDao
    abstract fun pendingRevokeIntentDao(): PendingRevokeIntentDao
    abstract fun outgoingReportDao(): OutgoingReportDao
    abstract fun contactHistoryDao(): ContactHistoryDao
}
