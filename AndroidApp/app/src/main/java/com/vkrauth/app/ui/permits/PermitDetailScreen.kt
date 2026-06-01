package com.vkrauth.app.ui.permits

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.vkrauth.app.R
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.ViewModel
import com.vkrauth.app.data.local.entity.PermitEntity
import com.vkrauth.app.data.repository.PermitsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.Flow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

@HiltViewModel
class PermitDetailViewModel @Inject constructor(
    private val permitsRepository: PermitsRepository
) : ViewModel() {
    fun observe(permitId: String): Flow<PermitEntity?> = permitsRepository.observeOne(permitId)
}

@Composable
fun PermitDetailScreen(
    permitId: String,
    onBack: () -> Unit,
    onOpenKeys: () -> Unit,
    viewModel: PermitDetailViewModel = hiltViewModel()
) {
    val permit by viewModel.observe(permitId).collectAsState(initial = null)

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        val p = permit
        if (p == null) {
            Text(stringResource(R.string.common_loading))
        } else {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(p.displayName, style = MaterialTheme.typography.titleLarge)
                    p.description?.let { Text(it) }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        stringResource(
                            R.string.permit_detail_valid_until,
                            SimpleDateFormat("dd.MM.yyyy", Locale.getDefault()).format(Date(p.validUntilMs))
                        )
                    )
                    Text(stringResource(R.string.permit_detail_parallel, p.activeKeysCount, p.nParallel))
                    Text(stringResource(R.string.permit_detail_max_ttl, p.maxTokenTtlSeconds))
                }
            }
            Spacer(Modifier.height(12.dp))
            Button(onClick = onOpenKeys, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.permit_detail_open_keys))
            }
            Spacer(Modifier.height(8.dp))
            Button(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text(stringResource(R.string.common_back)) }
        }
    }
}
