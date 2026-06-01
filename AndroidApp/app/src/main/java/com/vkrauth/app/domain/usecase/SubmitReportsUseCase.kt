package com.vkrauth.app.domain.usecase

import android.util.Base64
import com.vkrauth.app.data.local.dao.OutgoingReportDao
import com.vkrauth.app.data.remote.CurrentAccount
import com.vkrauth.app.data.remote.ScudApiFactory
import com.vkrauth.app.data.remote.dto.ReportDto
import com.vkrauth.app.data.remote.dto.SubmitReportsRequest
import com.vkrauth.app.data.repository.AuthRepository
import com.vkrauth.app.data.repository.PermitsRepository
import com.vkrauth.app.di.IoDispatcher
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import javax.inject.Inject

data class SubmitResult(
    val accepted: Int,
    val rejected: Int,
    val error: String? = null
)

class SubmitReportsUseCase @Inject constructor(
    private val dao: OutgoingReportDao,
    private val apiFactory: ScudApiFactory,
    private val currentAccount: CurrentAccount,
    private val permitsRepository: PermitsRepository,
    private val authRepository: AuthRepository,
    @IoDispatcher private val io: CoroutineDispatcher
) {
    suspend operator fun invoke(): SubmitResult = withContext(io) {
        val all = dao.getAll()
        if (all.isEmpty()) return@withContext SubmitResult(0, 0)

        // Наличие BLK-отчёта означает, что на сервере вот-вот сменится статус
        // revoked_by_reader и упадёт active_keys_count в соответствующем permit.
        val hasBlacklistReport = all.any { it.type == "blacklist_report" }

        val snapshot = currentAccount.get()
            ?: return@withContext SubmitResult(0, all.size, "no session")
        val api = apiFactory.create(snapshot.baseUrl)

        val req = SubmitReportsRequest(
            reports = all.map { r ->
                ReportDto(
                    type = r.type,
                    targetReaderId = r.targetReaderId,
                    bytes = Base64.encodeToString(r.bytes, Base64.NO_WRAP)
                )
            }
        )

        val resp = try {
            api.submitReports(req)
        } catch (e: Exception) {
            dao.incrementRetryCountAll()
            return@withContext SubmitResult(0, all.size, e.message)
        }

        val rejectedIndices = resp.rejected.map { it.index }.toSet()
        val toDelete = all.withIndex()
            .filter { it.index !in rejectedIndices }
            .map { it.value.reportId }
        dao.deleteByIds(toDelete)

        val toIncrement = all.withIndex()
            .filter { it.index in rejectedIndices }
            .map { it.value.reportId }
        if (toIncrement.isNotEmpty()) {
            dao.incrementRetryCount(toIncrement)
        }

        // После успешного приёма BLK воркер обычно обрабатывает process_blk_report
        // за <100 мс — даём ему фору и перетягиваем permits, чтобы UI сразу
        // увидел свежий active_keys_count.
        if (hasBlacklistReport) {
            delay(800)
            authRepository.currentAccount()?.let { acc ->
                runCatching { permitsRepository.refresh(acc.userId) }
            }
        }

        SubmitResult(accepted = toDelete.size, rejected = toIncrement.size)
    }
}
