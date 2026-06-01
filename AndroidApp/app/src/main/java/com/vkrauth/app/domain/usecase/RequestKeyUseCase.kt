package com.vkrauth.app.domain.usecase

import android.util.Base64
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import com.vkrauth.app.data.local.entity.TimeGrantEntity
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import com.vkrauth.app.data.remote.dto.RequestKeyRequest
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.data.repository.KeysRepository
import com.vkrauth.app.data.repository.PermitsRepository
import com.vkrauth.app.data.repository.ReadersRepository
import java.time.Instant
import javax.inject.Inject

class RequestKeyUseCase @Inject constructor(
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val keysRepository: KeysRepository,
    private val readersRepository: ReadersRepository,
    private val permitsRepository: PermitsRepository,
    private val authRepository: AuthRepository
) {
    // validStartMs — null = сразу от now. Для «future-dated» ключей передаём явное
    // начало (unix-миллисекунды), бэкенд сохранит его как issued_at.
    suspend operator fun invoke(
        permitId: String,
        validitySeconds: Int,
        validStartMs: Long? = null
    ): Result<IssuedKeyEntity> = runCatching {
        val snapshot = currentAccount.get() ?: error("no session")
        val api = apiFactory.create(snapshot.baseUrl)

        // TIME_SYNC grant: запрашиваем автоматически, если на permit'е ещё нет
        // активного grant'а. Пользователь это никак не контролирует — ридер
        // по нему сам синхронизирует часы при тапе, если дрейф > порога.
        val needGrant = keysRepository.activeGrantForPermit(permitId) == null

        val resp = api.requestKey(
            RequestKeyRequest(
                permitId = permitId,
                validitySeconds = validitySeconds,
                requestGrant = needGrant,
                issuedAtTs = validStartMs?.let { it / 1000L }
            )
        )
        val keyBytes = Base64.decode(resp.issuedKey.fullKeyBase64, Base64.NO_WRAP)
        val readerIdHex = Serialization.extractReaderIdFromKey(keyBytes).toHex()

        val entity = IssuedKeyEntity(
            keyIdHex = resp.issuedKey.keyIdHex,
            permitId = permitId,
            readerId = readerIdHex,
            issuedAtMs = Instant.parse(resp.issuedKey.issuedAt).toEpochMilli(),
            expiresAtMs = Instant.parse(resp.issuedKey.expiresAt).toEpochMilli(),
            fullKeyBytes = keyBytes,
            belongsToThisDevice = true
        )
        keysRepository.insert(entity)

        resp.timeGrant?.let { gr ->
            val grantBytes = Base64.decode(gr.fullGrantBase64, Base64.NO_WRAP)
            keysRepository.insertGrant(
                TimeGrantEntity(
                    grantId = gr.grantId,
                    permitId = permitId,
                    readerId = readerIdHex,
                    kind = gr.kind,
                    issuedAtMs = System.currentTimeMillis(),
                    expiresAtMs = Instant.parse(gr.expiresAt).toEpochMilli(),
                    fullGrantBytes = grantBytes
                )
            )
        }

        readersRepository.ensureReaderKnown(readerIdHex)
        // Обновим permits: на сервере active_keys_count увеличился, хотим видеть
        // актуальное значение в UI без ожидания следующего периодического refresh.
        authRepository.currentAccount()?.let { acc ->
            runCatching { permitsRepository.refresh(acc.userId) }
        }
        entity
    }
}
