package com.vkrauth.app.ui.keys

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.vkrauth.app.data.local.entity.IssuedKeyEntity
import com.vkrauth.app.data.local.entity.IssuedKeyStatus
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.data.repository.KeysRepository
import com.vkrauth.app.data.repository.PermitsRepository
import com.vkrauth.app.domain.usecase.QueueRevokeOnReaderUseCase
import com.vkrauth.app.domain.usecase.RequestKeyUseCase
import com.vkrauth.app.domain.usecase.RevokeKeyOnServerUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// Фильтр, который пользователь может переключить на экране ключей:
//   ACTIVE_ONLY — только status='active' с неистёкшим TTL (рабочие для тапа).
//   WITH_ARCHIVE — всё подряд, включая отозванные и истёкшие.
enum class KeyFilterMode { ACTIVE_ONLY, WITH_ARCHIVE }

data class KeysUiState(
    val permitFilter: String? = null,
    val permit: PermitEntity? = null,
    val keys: List<IssuedKeyEntity> = emptyList(),
    val filterMode: KeyFilterMode = KeyFilterMode.ACTIVE_ONLY,
    val primaryKeyId: String? = null,         // keyIdHex, который сейчас выбран бы тапом
    val activeCount: Int = 0,                 // usable-для-тапа (status=active И не истёк)
    // slotCount — сколько ключей занимают слот n_parallel (зеркалит серверный
    // count_active_keys = active + revoked_by_server). Именно это гейтит выпуск нового
    // ключа, а не activeCount (revoked_by_server держит слот до применения bloom ридером).
    val slotCount: Int = 0,
    val pendingBloomCount: Int = 0,           // revoked_by_server, ещё занимающие слот
    val archivedCount: Int = 0,
    // Сколько ключей пропуска ещё НЕ истекли (expiresAtMs > now, любой статус) —
    // зеркалит серверный гейт count_unexpired_keys. Для строки «X / лимит» и для
    // гейта кнопки выпуска; лимит — permit.maxTotalIssued (null = без лимита,
    // защита bloom-фильтра отзыва от раздувания). Истёкшие ключи слот не держат.
    val totalIssuedCount: Int = 0,
    val selectionMode: Boolean = false,
    val selectedIds: Set<String> = emptySet(),
    val showRequestDialog: Boolean = false,
    val showConfirmServerRevoke: List<IssuedKeyEntity>? = null,
    val showConfirmReaderRevoke: List<IssuedKeyEntity>? = null,
    // Второй диалог: после подтверждения отзыва через ридер пользователь выбирает
    // ТИП отзыва — «пропуск и отзыв» (grantFinalAccess=true) или «только отзыв».
    val showRevokeTypeChoice: List<IssuedKeyEntity>? = null,
    val showConfirmPurge: Boolean = false,
    val error: String? = null
)

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
@HiltViewModel
class KeysViewModel @Inject constructor(
    private val keysRepository: KeysRepository,
    private val permitsRepository: PermitsRepository,
    private val requestKeyUseCase: RequestKeyUseCase,
    private val revokeKeyOnServerUseCase: RevokeKeyOnServerUseCase,
    private val queueRevokeOnReaderUseCase: QueueRevokeOnReaderUseCase
) : ViewModel() {

    private val local = MutableStateFlow(KeysUiState())

    val state: StateFlow<KeysUiState> = local
        .flatMapLatest { ui ->
            val keysFlow = if (ui.permitFilter != null) {
                keysRepository.observeForPermit(ui.permitFilter)
            } else {
                keysRepository.observeAll()
            }
            val permitFlow = ui.permitFilter?.let { permitsRepository.observeOne(it) } ?: flowOf(null)
            combine(keysFlow, permitFlow) { allKeys, permit ->
                val nowMs = System.currentTimeMillis()

                // Разделяем на активные (status=active И expires>now) и архив (всё остальное).
                val active = allKeys.filter { it.isUsable(nowMs) }
                val archive = allKeys - active.toSet()

                // Занятые слоты n_parallel = active + revoked_by_server (зеркало backend
                // count_active_keys). pendingBloom — отозванные-на-сервере, ещё держащие слот
                // (для подсказки «слот занят, пока ридер не обновит bloom»).
                val slotUsed = allKeys.count { it.occupiesSlot() }
                val pendingBloom = allKeys.count { it.status == IssuedKeyStatus.REVOKED_BY_SERVER }

                // Ключ, который выберет TapDecisionTree (первый active, принадлежащий этому
                // устройству, с наибольшим expiresAtMs). Подсвечиваем его в UI.
                val primary = active
                    .filter { it.belongsToThisDevice }
                    .maxByOrNull { it.expiresAtMs }
                    ?.keyIdHex

                val visible = when (ui.filterMode) {
                    KeyFilterMode.ACTIVE_ONLY -> active
                    KeyFilterMode.WITH_ARCHIVE -> allKeys  // уже отсортированы DAO (active→revoked→expired)
                }

                ui.copy(
                    keys = visible,
                    permit = permit,
                    primaryKeyId = primary,
                    activeCount = active.size,
                    slotCount = slotUsed,
                    pendingBloomCount = pendingBloom,
                    archivedCount = archive.size,
                    // Ещё-не-истёкшие ключи (любой статус) — зеркало серверного
                    // count_unexpired_keys (гейт maxTotalIssued). Истёкшие выпадают.
                    totalIssuedCount = allKeys.count { it.expiresAtMs > nowMs },
                )
            }
        }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), KeysUiState())

    fun setPermitFilter(permitId: String?) {
        local.update { it.copy(permitFilter = permitId) }
        viewModelScope.launch {
            // Помечаем истёкшие active-ключи как expired (чтобы status-колонка
            // не врала и firstActiveForReader их не выбирал).
            keysRepository.markExpired()
            // Тянем с сервера актуальный список ключей permit'а. Без этого
            // локально видны только ключи, выпущенные с ТЕКУЩЕГО устройства —
            // ключи других устройств того же пользователя не попадают.
            if (permitId != null) {
                runCatching { keysRepository.refreshForPermit(permitId) }
            } else {
                runCatching { keysRepository.refreshAllPermits() }
            }
        }
    }

    fun setFilterMode(mode: KeyFilterMode) = local.update { it.copy(filterMode = mode) }

    fun toggleSelect(keyIdHex: String) = local.update {
        val newSet = if (keyIdHex in it.selectedIds) it.selectedIds - keyIdHex else it.selectedIds + keyIdHex
        it.copy(selectedIds = newSet, selectionMode = newSet.isNotEmpty())
    }

    fun enterSelectionMode(initial: String) = local.update {
        it.copy(selectionMode = true, selectedIds = it.selectedIds + initial)
    }

    fun exitSelectionMode() = local.update {
        it.copy(selectionMode = false, selectedIds = emptySet())
    }

    fun openRequestDialog() = local.update { it.copy(showRequestDialog = true) }

    fun closeRequestDialog() = local.update { it.copy(showRequestDialog = false) }

    // startMs — unix-миллисекунды начала действия ключа (future-start).
    // validitySeconds — end - start в секундах.
    // Grant (TIME_SYNC) запрашивается автоматически в UseCase.
    fun requestKey(startMs: Long, validitySeconds: Int) {
        val permitId = local.value.permitFilter ?: return
        viewModelScope.launch {
            requestKeyUseCase(permitId, validitySeconds, startMs).onFailure { t ->
                local.update { it.copy(error = t.message) }
            }
            local.update { it.copy(showRequestDialog = false) }
        }
    }

    // Swipe-action: отзыв через сервер/ридер ОДНОГО ключа без confirm-диалога.
    // Вызывается из SwipeToDismissBox на соответствующем направлении.
    fun swipeRevokeServer(key: IssuedKeyEntity) {
        if (key.status != IssuedKeyStatus.ACTIVE) return
        viewModelScope.launch { revokeKeyOnServerUseCase(key.keyIdHex) }
    }

    fun swipeRevokeReader(key: IssuedKeyEntity) {
        if (key.status != IssuedKeyStatus.ACTIVE) return
        // Быстрый свайп = «только отзыв» (grantFinalAccess=false): без лишнего
        // диалога не открываем дверь, просто ставим ключ в очередь на отзыв.
        viewModelScope.launch { queueRevokeOnReaderUseCase(key.keyIdHex, grantFinalAccess = false) }
    }

    // Swipe-action для архивных ключей: физически удалить из локальной БД
    // (не-active → просто forget, сервер о них знает сам).
    fun swipeDeleteArchived(key: IssuedKeyEntity) {
        if (key.status == IssuedKeyStatus.ACTIVE) return
        viewModelScope.launch { keysRepository.deleteByIds(listOf(key.keyIdHex)) }
    }

    fun askServerRevoke(keys: List<IssuedKeyEntity>) {
        // Отзыв на сервере имеет смысл только для active-ключей.
        val valid = keys.filter { it.status == IssuedKeyStatus.ACTIVE }
        if (valid.isEmpty()) return
        local.update { it.copy(showConfirmServerRevoke = valid) }
    }

    fun askReaderRevoke(keys: List<IssuedKeyEntity>) {
        val valid = keys.filter { it.status == IssuedKeyStatus.ACTIVE }
        if (valid.isEmpty()) return
        local.update { it.copy(showConfirmReaderRevoke = valid) }
    }

    fun dismissDialogs() = local.update {
        it.copy(
            showConfirmServerRevoke = null,
            showConfirmReaderRevoke = null,
            showRevokeTypeChoice = null,
            showConfirmPurge = false
        )
    }

    fun confirmServerRevoke() {
        val keys = local.value.showConfirmServerRevoke ?: return
        viewModelScope.launch {
            keys.forEach { revokeKeyOnServerUseCase(it.keyIdHex) }
            local.update {
                it.copy(
                    showConfirmServerRevoke = null,
                    selectedIds = emptySet(),
                    selectionMode = false
                )
            }
        }
    }

    fun confirmReaderRevoke() {
        // Не ставим отзыв в очередь сразу: сначала переходим ко второму диалогу
        // выбора ТИПА отзыва (пропуск+отзыв / только отзыв). Сам queue произойдёт
        // в chooseReaderRevokeType с нужным флагом grantFinalAccess.
        val keys = local.value.showConfirmReaderRevoke ?: return
        local.update { it.copy(showConfirmReaderRevoke = null, showRevokeTypeChoice = keys) }
    }

    // Второй диалог: пользователь выбрал тип отзыва. grantFinalAccess управляет
    // порядком операций в тапе (Approach A) — true: ACCESS перед REVOKE_KEY
    // («пропуск и отзыв»), false: без ACCESS («только отзыв»).
    fun chooseReaderRevokeType(grantFinalAccess: Boolean) {
        val keys = local.value.showRevokeTypeChoice ?: return
        viewModelScope.launch {
            keys.forEach { queueRevokeOnReaderUseCase(it.keyIdHex, grantFinalAccess) }
            local.update {
                it.copy(
                    showRevokeTypeChoice = null,
                    selectedIds = emptySet(),
                    selectionMode = false
                )
            }
        }
    }

    fun askPurgeArchive() = local.update { it.copy(showConfirmPurge = true) }

    fun confirmPurgeArchive() {
        val permitId = local.value.permitFilter
        viewModelScope.launch {
            if (permitId != null) keysRepository.deleteInactiveForPermit(permitId)
            else keysRepository.deleteInactive()
            local.update { it.copy(showConfirmPurge = false) }
        }
    }

    fun deleteSelected() {
        // «Удалить из архива» — физически стирает из локальной БД только
        // отобранные НЕактивные ключи. Для active принудительно отказываем
        // (сначала отзыв, потом удаление).
        val toDelete = state.value.keys
            .filter { it.keyIdHex in local.value.selectedIds }
            .filter { it.status != IssuedKeyStatus.ACTIVE }
            .map { it.keyIdHex }
        if (toDelete.isEmpty()) return
        viewModelScope.launch {
            keysRepository.deleteByIds(toDelete)
            local.update {
                it.copy(selectedIds = emptySet(), selectionMode = false)
            }
        }
    }

    fun dismissError() = local.update { it.copy(error = null) }
}
