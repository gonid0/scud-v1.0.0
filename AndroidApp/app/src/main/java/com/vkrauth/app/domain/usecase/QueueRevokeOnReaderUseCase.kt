package com.vkrauth.app.domain.usecase

import com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity
import com.vkrauth.app.data.repository.KeysRepository
import com.vkrauth.app.data.repository.ReportsRepository
import javax.inject.Inject

class QueueRevokeOnReaderUseCase @Inject constructor(
    private val reportsRepository: ReportsRepository,
    private val keysRepository: KeysRepository
) {
    // grantFinalAccess — тип отзыва (Approach A, выбор клиентом через порядок op):
    //   true  → «Пропуск и отзыв»: на тапе ACCESS пойдёт ПЕРЕД REVOKE_KEY.
    //   false → «Только отзыв»: ACCESS этого ключа не строится (дверь молчит).
    suspend operator fun invoke(keyIdHex: String, grantFinalAccess: Boolean = false): Result<Long> = runCatching {
        val key = keysRepository.findByKeyId(keyIdHex) ?: error("key not found")
        val id = reportsRepository.insertRevokeIntent(
            PendingRevokeIntentEntity(
                targetReaderId = key.readerId,
                targetKeyIdHex = keyIdHex,
                targetFullKeyBytes = key.fullKeyBytes,
                createdAtMs = System.currentTimeMillis(),
                status = "pending",
                grantFinalAccess = grantFinalAccess
            )
        )
        // НЕ помечаем ключ revoked_by_reader сейчас: ключ обязан остаться
        // active/валидным до самого тапа —
        //  - он является requester'ом для REVOKE_KEY (firstActiveForReader),
        //  - в сценарии «пропуск и отзыв» он же используется для ACCESS,
        //  - при self-revoke (отзыв собственного ключа) преждевременная пометка
        //    лишила бы REVOKE_KEY его requester'а.
        // Локальная пометка REVOKED_BY_READER отложена в TapDecisionTree:
        // onResult REVOKE_KEY (успех) → commitPendingChanges, т.е. только по
        // фактически подтверждённому отзыву на ридере.
        id
    }
}
