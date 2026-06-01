package com.vkrauth.app.data.repository

import android.util.Base64
import com.vkrauth.app.data.local.dao.ReaderDao
import com.vkrauth.app.data.local.entity.ReaderKnownEntity
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ReadersRepository @Inject constructor(
    private val readerDao: ReaderDao,
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount
) {
    fun observeAll(): Flow<List<ReaderKnownEntity>> = readerDao.observeAll()

    suspend fun find(readerIdHex: String): ReaderKnownEntity? = readerDao.find(readerIdHex)

    suspend fun ensureReaderKnown(readerIdHex: String): ReaderKnownEntity? {
        val existing = readerDao.find(readerIdHex)
        if (existing != null) return existing
        val snapshot = currentAccount.get() ?: return null
        val api = apiFactory.create(snapshot.baseUrl)
        return try {
            val resp = api.reader(readerIdHex)
            val entity = ReaderKnownEntity(
                readerId = resp.readerIdHex,
                displayName = resp.displayName,
                description = resp.description,
                readerPubkey = Base64.decode(resp.readerPubkey, Base64.NO_WRAP),
                readerGroupId = resp.groupId
            )
            readerDao.insert(entity)
            entity
        } catch (_: Exception) {
            null
        }
    }
}
