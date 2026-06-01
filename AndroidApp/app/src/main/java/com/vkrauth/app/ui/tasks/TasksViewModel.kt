package com.vkrauth.app.ui.tasks

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.R
import com.vkrauth.app.data.local.entity.OutgoingReportEntity
import com.vkrauth.app.data.local.entity.PendingFilterDeliveryEntity
import com.vkrauth.app.data.local.entity.PendingRevokeIntentEntity
import com.vkrauth.app.data.remote.dto.CourierAvailableItem
import com.vkrauth.app.data.repository.CourierRepository
import com.vkrauth.app.data.repository.ReportsRepository
import com.vkrauth.app.domain.usecase.DownloadDeliveryUseCase
import com.vkrauth.app.domain.usecase.SubmitReportsUseCase
import com.vkrauth.app.domain.usecase.SubmitResult
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// Один элемент списка на вкладке «На READER». Это либо локально сохранённая
// (скачанная) посылка, либо ещё-не-скачанная доступная на сервере. Оба варианта
// рендерятся одной карточкой с разным состоянием.
sealed class ReaderItem {
    abstract val deliveryId: String
    abstract val readerId: String
    abstract val filterVersion: Long

    data class Downloaded(val entity: PendingFilterDeliveryEntity) : ReaderItem() {
        override val deliveryId get() = entity.deliveryId
        override val readerId get() = entity.targetReaderId
        override val filterVersion get() = entity.filterVersion
    }

    data class Downloadable(val item: CourierAvailableItem) : ReaderItem() {
        override val deliveryId get() = item.deliveryId
        override val readerId get() = item.readerIdHex
        override val filterVersion get() = item.filterVersion
        val readerName: String get() = item.readerName
    }
}

data class TasksUiState(
    val outgoing: List<OutgoingReportEntity> = emptyList(),
    val pendingRevokes: List<PendingRevokeIntentEntity> = emptyList(),
    // Единый список карточек для ReaderTab. Сортируется: downloaded вверху
    // (готовы к отправке), downloadable ниже.
    val readerItems: List<ReaderItem> = emptyList(),
    @StringRes val refreshErrorRes: Int? = null,
    val refreshErrorArg: String? = null,
    val lastSubmitResult: SubmitResult? = null,
    val loading: Boolean = false
)

@HiltViewModel
class TasksViewModel @Inject constructor(
    private val courierRepository: CourierRepository,
    private val reportsRepository: ReportsRepository,
    private val submitReportsUseCase: SubmitReportsUseCase,
    private val downloadDeliveryUseCase: DownloadDeliveryUseCase
) : ViewModel() {

    // local.available хранится отдельно — он не в БД, это снапшот после
    // последнего нажатия «Обновить».
    private val local = MutableStateFlow(LocalUiState())

    private data class LocalUiState(
        val available: List<CourierAvailableItem> = emptyList(),
        @StringRes val refreshErrorRes: Int? = null,
        val refreshErrorArg: String? = null,
        val lastSubmitResult: SubmitResult? = null,
        val loading: Boolean = false,
    )

    val state: StateFlow<TasksUiState> = combine(
        reportsRepository.observeOutgoing(),
        courierRepository.observePending(),
        reportsRepository.observeRevokeIntents(),
        local
    ) { outgoing, downloaded, revokes, ui ->
        val downloadedIds = downloaded.map { it.deliveryId }.toSet()
        val downloadedItems = downloaded.map { ReaderItem.Downloaded(it) }
        val downloadableItems = ui.available
            .filter { it.deliveryId !in downloadedIds }  // дубликаты исключаем
            .map { ReaderItem.Downloadable(it) }
        TasksUiState(
            outgoing = outgoing,
            pendingRevokes = revokes,
            readerItems = downloadedItems + downloadableItems,
            refreshErrorRes = ui.refreshErrorRes,
            refreshErrorArg = ui.refreshErrorArg,
            lastSubmitResult = ui.lastSubmitResult,
            loading = ui.loading
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), TasksUiState())

    fun refreshAvailable() {
        viewModelScope.launch {
            local.update { it.copy(loading = true, refreshErrorRes = null, refreshErrorArg = null) }
            runCatching { courierRepository.fetchAvailable() }
                .onSuccess { items -> local.update { it.copy(available = items, loading = false) } }
                .onFailure { t ->
                    local.update { it.copy(refreshErrorRes = R.string.tasks_refresh_error, refreshErrorArg = t.message, loading = false) }
                }
        }
    }

    fun submitAll() {
        viewModelScope.launch {
            val result = submitReportsUseCase()
            local.update { it.copy(lastSubmitResult = result) }
        }
    }

    fun downloadDelivery(deliveryId: String) {
        viewModelScope.launch {
            downloadDeliveryUseCase(deliveryId)
                .onSuccess {
                    // Убираем из локального "available"-snapshot только при
                    // успехе — иначе пользователь видит что пункт пропал,
                    // а в «скачанных» его тоже нет.
                    local.update {
                        it.copy(available = it.available.filterNot { v -> v.deliveryId == deliveryId })
                    }
                }
                .onFailure { t ->
                    local.update { it.copy(refreshErrorRes = R.string.tasks_download_error, refreshErrorArg = t.message) }
                }
        }
    }

    fun dismissError() = local.update { it.copy(refreshErrorRes = null, refreshErrorArg = null) }

    // Удалить скачанную посылку из локальной БД.
    fun deleteDownloaded(deliveryId: String) {
        viewModelScope.launch { courierRepository.forget(deliveryId) }
    }

    fun cancelRevokeIntent(intentId: Long) {
        viewModelScope.launch { reportsRepository.deleteRevokeIntent(intentId) }
    }
}
