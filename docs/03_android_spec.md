# 03. Android — ТЗ для Android-приложения (Kotlin + Jetpack Compose)

**Этот документ читается вместе с `00_shared_protocol.md`.** Там — форматы всех объектов, криптопримитивы, domain tags, алгоритмы подписи.

## 0. Задача

Android-приложение self-hosted СКУД. Оффлайн-first: основное время работает без интернета. При касании NFC автоматически выполняет всё возможное — обновляет фильтры, синхронизирует время, открывает дверь, забирает отчёты. Пользователь-курьер вручную загружает посылки для чужих ридеров своей группы.

## 1. Стек

| Компонент | Версия / выбор |
|---|---|
| Язык | Kotlin 1.9+ |
| Target SDK | 34 (Android 14) |
| Min SDK | 26 (Android 8) — требуется для HCE и современного NFC |
| UI | Jetpack Compose (Material 3) |
| Navigation | Navigation-Compose |
| Async | Coroutines + Flow |
| DI | Hilt (Dagger-Hilt) |
| Storage | Room 2.6+ |
| HTTP | Retrofit 2.11+ с OkHttp 4.12 |
| JSON | kotlinx.serialization |
| Crypto (Ed25519) | BouncyCastle 1.77+ |
| Keystore wrapping | Android Keystore (AES-GCM) |
| Secure prefs | AndroidX Security (EncryptedSharedPreferences) |
| NFC HCE | `android.nfc.cardemulation.HostApduService` |
| Testing | JUnit 4, Turbine, mockk, Espresso (где нужно) |

## 2. Структура проекта

Single-module Gradle project, но внутри — слойное разделение по пакетам.

```
app/
├── build.gradle.kts
├── proguard-rules.pro
└── src/main/
    ├── AndroidManifest.xml
    ├── java/com/vkrauth/app/
    │   ├── ScudApplication.kt                  # @HiltAndroidApp
    │   ├── MainActivity.kt                     # @AndroidEntryPoint, NavHost
    │   ├── di/
    │   │   ├── DatabaseModule.kt
    │   │   ├── NetworkModule.kt
    │   │   ├── CryptoModule.kt
    │   │   └── DispatchersModule.kt
    │   ├── data/
    │   │   ├── local/
    │   │   │   ├── ScudDatabase.kt             # Room @Database
    │   │   │   ├── dao/
    │   │   │   │   ├── AccountDao.kt
    │   │   │   │   ├── PermitDao.kt
    │   │   │   │   ├── IssuedKeyDao.kt
    │   │   │   │   ├── TimeGrantDao.kt
    │   │   │   │   ├── ReaderDao.kt
    │   │   │   │   ├── PendingFilterDeliveryDao.kt
    │   │   │   │   ├── PendingRevokeIntentDao.kt
    │   │   │   │   ├── OutgoingReportDao.kt
    │   │   │   │   └── ContactHistoryDao.kt
    │   │   │   └── entity/
    │   │   │       ├── AccountEntity.kt
    │   │   │       ├── PermitEntity.kt
    │   │   │       ├── IssuedKeyEntity.kt
    │   │   │       ├── TimeGrantEntity.kt
    │   │   │       ├── ReaderKnownEntity.kt
    │   │   │       ├── PendingFilterDeliveryEntity.kt
    │   │   │       ├── PendingRevokeIntentEntity.kt
    │   │   │       ├── OutgoingReportEntity.kt
    │   │   │       └── ContactHistoryEntity.kt
    │   │   ├── remote/
    │   │   │   ├── ScudApi.kt                  # Retrofit interface
    │   │   │   ├── dto/
    │   │   │   │   └── ... (все request/response DTOs)
    │   │   │   └── AuthInterceptor.kt
    │   │   ├── crypto/
    │   │   │   ├── KeyManager.kt               # Ed25519 keypair + Keystore wrapping
    │   │   │   ├── Domains.kt                  # domain tags
    │   │   │   ├── Ed25519.kt                  # BouncyCastle wrapper
    │   │   │   ├── Blake2s.kt                  # BouncyCastle / Bouncy-built-in
    │   │   │   └── Serialization.kt            # pack/unpack all objects
    │   │   └── repository/
    │   │       ├── AuthRepository.kt
    │   │       ├── PermitsRepository.kt
    │   │       ├── KeysRepository.kt
    │   │       ├── CourierRepository.kt
    │   │       ├── ReportsRepository.kt
    │   │       └── ReadersRepository.kt
    │   ├── domain/
    │   │   ├── model/                           # чистые data-классы без Room/Retrofit
    │   │   └── usecase/
    │   │       ├── LoginUseCase.kt
    │   │       ├── RegisterDeviceUseCase.kt
    │   │       ├── RefreshPermitsUseCase.kt
    │   │       ├── RequestKeyUseCase.kt
    │   │       ├── RevokeKeyOnServerUseCase.kt
    │   │       ├── QueueRevokeOnReaderUseCase.kt
    │   │       ├── DownloadDeliveryUseCase.kt
    │   │       ├── SubmitReportsUseCase.kt
    │   │       └── LogoutUseCase.kt
    │   ├── ble/                                # BLE-канал (shared §16) — зеркало HCE
    │   │   ├── BleConstants.kt                  # UUID сервиса/характеристик, framing-флаги, MTU
    │   │   ├── BleFraming.kt                    # чистая нарезка PDU (юнит-тестится)
    │   │   ├── BleScanner.kt                    # скан ридеров по SERVICE_UUID
    │   │   └── BleSession.kt                    # GATT-сессия + ReassemblyBuf
    │   ├── hce/
    │   │   ├── ScudHceService.kt                # HostApduService
    │   │   ├── TapController.kt                 # координатор сессии (корутинный)
    │   │   ├── TapSession.kt                    # state одного касания + msg_id buffers
    │   │   ├── TapSessionHolder.kt              # хранит текущую TapSession (Singleton)
    │   │   ├── TapDecisionTree.kt               # operations_queue builder
    │   │   ├── TapUiState.kt                    # sealed-состояния для UI (Waiting/InProgress/…)
    │   │   ├── TapLog.kt                        # кольцевой журнал tap-сессии для экрана
    │   │   └── operations/
    │   │       ├── OperationBuilders.kt         # build<X>Operation — сериализация + подпись
    │   └── ui/
    │       ├── theme/
    │       ├── common/
    │       │   ├── Components.kt                # переиспользуемые Compose-компоненты
    │       │   └── HapticFeedback.kt            # вибро/звук на verdict (granted/denied)
    │       ├── navigation/
    │       │   ├── ScudNavHost.kt
    │       │   └── AppScaffold.kt               # общий каркас с нижней навигацией
    │       ├── ble/
    │       │   ├── BleReadersScreen.kt          # список найденных BLE-ридеров
    │       │   ├── BleReadersViewModel.kt
    │       │   ├── BleSessionScreen.kt          # прогресс BLE-сеанса
    │       │   └── BleSessionViewModel.kt
    │       ├── auth/
    │       │   ├── AuthScreen.kt
    │       │   └── AuthViewModel.kt
    │       ├── home/
    │       │   ├── HomeScreen.kt
    │       │   └── HomeViewModel.kt
    │       ├── tap/
    │       │   ├── TapScreen.kt
    │       │   └── TapViewModel.kt
    │       ├── permits/
    │       │   ├── PermitsScreen.kt
    │       │   ├── PermitsViewModel.kt
    │       │   └── PermitDetailScreen.kt        # режим с фильтром ключей этого permit
    │       ├── keys/
    │       │   ├── KeysScreen.kt
    │       │   └── KeysViewModel.kt
    │       ├── tasks/
    │       │   ├── TasksScreen.kt
    │       │   └── TasksViewModel.kt
    │       └── settings/
    │           ├── SettingsScreen.kt
    │           └── SettingsViewModel.kt
    └── res/
        ├── xml/
        │   └── apduservice.xml                  # HCE конфиг с AID
        ├── values/
        │   ├── strings.xml
        │   └── themes.xml
        └── drawable/
```

## 3. build.gradle.kts (app)

```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization") version "1.9.22"
    id("com.google.dagger.hilt.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.vkrauth.app"
    compileSdk = 34
    
    defaultConfig {
        applicationId = "com.vkrauth.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }
    
    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.8"
    }
    
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.02.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.navigation:navigation-compose:2.7.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.7.0")
    
    // Hilt
    implementation("com.google.dagger:hilt-android:2.50")
    ksp("com.google.dagger:hilt-compiler:2.50")
    implementation("androidx.hilt:hilt-navigation-compose:1.1.0")
    
    // Room
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    
    // Retrofit + OkHttp
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.2")
    
    // Crypto
    implementation("org.bouncycastle:bcprov-jdk18on:1.77")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    
    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    
    // Testing
    testImplementation("junit:junit:4.13.2")
    testImplementation("io.mockk:mockk:1.13.9")
    testImplementation("app.cash.turbine:turbine:1.0.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
```

## 4. AndroidManifest.xml

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.NFC" />
    <!-- Тактильная обратная связь по результату tap-сессии (GRANTED/DENIED). -->
    <uses-permission android:name="android.permission.VIBRATE" />

    <!-- BLE: shared §16 channel. Опциональная фича — устройства без BLE
         работают через NFC как раньше. -->
    <uses-permission android:name="android.permission.BLUETOOTH_SCAN"
        android:usesPermissionFlags="neverForLocation"
        tools:targetApi="s" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT"
        tools:targetApi="s" />
    <!-- Legacy (API < 31): scan требовал location. -->
    <uses-permission android:name="android.permission.BLUETOOTH"
        android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.BLUETOOTH_ADMIN"
        android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"
        android:maxSdkVersion="30" />

    <uses-feature android:name="android.hardware.nfc" android:required="true" />
    <uses-feature android:name="android.hardware.nfc.hce" android:required="true" />
    <uses-feature android:name="android.hardware.bluetooth_le" android:required="false" />
    
    <application
        android:name=".ScudApplication"
        android:label="@string/app_name"
        android:theme="@style/Theme.App">
        
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:launchMode="singleTask">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        
        <service
            android:name=".hce.ScudHceService"
            android:exported="true"
            android:permission="android.permission.BIND_NFC_SERVICE">
            <intent-filter>
                <action android:name="android.nfc.cardemulation.action.HOST_APDU_SERVICE" />
            </intent-filter>
            <meta-data
                android:name="android.nfc.cardemulation.host_apdu_service"
                android:resource="@xml/apduservice" />
        </service>
    </application>
</manifest>
```

`res/xml/apduservice.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<host-apdu-service xmlns:android="http://schemas.android.com/apk/res/android"
    android:description="@string/hce_service_description"
    android:requireDeviceUnlock="true">
    <aid-group android:description="@string/hce_aid_group" android:category="other">
        <aid-filter android:name="F0535343554401" />
    </aid-group>
</host-apdu-service>
```

AID `F0 53 43 55 44 01` (hex без пробелов).

## 5. Room — схема

### 5.1 Entities

```kotlin
@Entity(tableName = "account")
data class AccountEntity(
    @PrimaryKey val id: Int = 1,   // singleton
    val domain: String,
    val userId: Int,
    val userGroupId: String,        // UUID
    val displayName: String,
    val sessionToken: String,
    val refreshToken: String,
    val deviceId: String?,          // UUID
    val phonePubkeyBase64: String   // 32 B в base64
)

@Entity(tableName = "permits")
data class PermitEntity(
    @PrimaryKey val permitId: String,    // UUID
    val userId: Int,
    val readerId: String,                 // hex (16 B = 32 hex chars)
    val displayName: String,
    val description: String?,
    val validFromMs: Long,                // unix ms
    val validUntilMs: Long,
    val nParallel: Int,
    val maxTokenTtlSeconds: Int,
    val activeKeysCount: Int = 0,
    val syncedAtMs: Long
)

@Entity(tableName = "issued_keys")
data class IssuedKeyEntity(
    @PrimaryKey val keyIdHex: String,     // hex
    val permitId: String,
    val readerId: String,
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    val fullKeyBytes: ByteArray,          // 151 B
    val belongsToThisDevice: Boolean,     // ключ выпущен на текущий phone_pubkey
    // Жизненный цикл ключа (добавлено в схеме v2). defaultValue "active"
    // нужен для бэкфилла записей, мигрированных с v1.
    @ColumnInfo(defaultValue = IssuedKeyStatus.ACTIVE)
    val status: String = IssuedKeyStatus.ACTIVE
)

// Возможные значения IssuedKeyEntity.status (без magic-strings):
//   active             — выпущен и пригоден
//   revoked_by_server  — отозван на сервере
//   revoked_by_reader  — отозван через REVOKE_KEY на ридере
//   revoked_in_bloom   — погашен фильтром (bloom)
//   expired            — истёк TTL
object IssuedKeyStatus {
    const val ACTIVE            = "active"
    const val REVOKED_BY_SERVER = "revoked_by_server"
    const val REVOKED_BY_READER = "revoked_by_reader"
    const val REVOKED_IN_BLOOM  = "revoked_in_bloom"
    const val EXPIRED           = "expired"
}

@Entity(tableName = "time_grants")
data class TimeGrantEntity(
    @PrimaryKey val grantId: String,      // UUID
    val permitId: String,
    val readerId: String,
    val kind: String,                      // "soft" / "hard"
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    val fullGrantBytes: ByteArray          // 148 B
)

@Entity(tableName = "readers_known")
data class ReaderKnownEntity(
    @PrimaryKey val readerId: String,     // hex
    val displayName: String,
    val description: String?,
    val readerPubkey: ByteArray,          // 32 B
    val readerGroupId: String              // UUID
)

@Entity(tableName = "pending_filter_deliveries")
data class PendingFilterDeliveryEntity(
    @PrimaryKey val deliveryId: String,   // UUID = task_id с сервера
    val targetReaderId: String,
    val filterVersion: Long,
    val filterPackageBytes: ByteArray,    // до ~127 KB
    val courierIdHex: String,             // 16 B hex
    val downloadedAtMs: Long,
    val status: String                      // "downloaded" / "delivered"
)

@Entity(tableName = "pending_revoke_intents")
data class PendingRevokeIntentEntity(
    @PrimaryKey(autoGenerate = true) val intentId: Long = 0,
    val targetReaderId: String,
    val targetKeyIdHex: String,
    val targetFullKeyBytes: ByteArray,    // 151 B ключа, который отзываем
    val createdAtMs: Long,
    val status: String                      // "pending" / "delivered"
)

@Entity(tableName = "outgoing_reports",
    indices = [Index(value = ["type", "targetReaderId"])])
data class OutgoingReportEntity(
    @PrimaryKey val reportId: String,      // UUID, generated locally
    val type: String,                       // "delivery_receipt" / "filter_delivery_info" / "blacklist_report"
    val targetReaderId: String,
    val bytes: ByteArray,
    val producedAtMs: Long,
    val retryCount: Int = 0
)

@Entity(tableName = "contact_history")
data class ContactHistoryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val readerId: String,
    val occurredAtMs: Long,
    val summary: String,                    // JSON summary: [{op, result}, ...]
    val verdict: String?                    // OK / EXPIRED / etc. если access был
)
```

### 5.2 Database

```kotlin
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
    version = 2,
    exportSchema = false
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
```

Схема в версии **v2**: единственное отличие от v1 — добавленная колонка
`issued_keys.status` (`@ColumnInfo(defaultValue = "active")`). Поскольку дев-схема
ещё не зафиксирована, миграция не пишется вручную: `DatabaseModule` собирает
базу с `.fallbackToDestructiveMigration()`, и при несовпадении версии Room
пересоздаёт `scud.db` (все данные — кэш с сервера и восстанавливаются повторной
синхронизацией). `exportSchema = false` — JSON-схемы не пишем.

### 5.3 DAO — ключевые запросы

```kotlin
@Dao
interface PermitDao {
    @Query("SELECT * FROM permits WHERE validUntilMs > :nowMs ORDER BY validUntilMs")
    fun observeActive(nowMs: Long): Flow<List<PermitEntity>>
    
    @Query("""
        UPDATE permits SET activeKeysCount = (
            SELECT COUNT(*) FROM issued_keys
            WHERE issued_keys.permitId = permits.permitId
              AND issued_keys.expiresAtMs > :nowMs
        )
    """)
    suspend fun recomputeActiveCounts(nowMs: Long)
    
    @Upsert
    suspend fun upsertAll(items: List<PermitEntity>)
    
    @Query("DELETE FROM permits WHERE permitId NOT IN (:keepIds)")
    suspend fun deleteMissing(keepIds: List<String>)
    
    @Transaction
    suspend fun replaceAll(items: List<PermitEntity>) {
        upsertAll(items)
        deleteMissing(items.map { it.permitId })
    }
}

@Dao
interface IssuedKeyDao {
    // Основной выбор ключа для тапа: status='active' AND не истёк
    // (опц. только ключи этого устройства). Берём тот, что живёт дольше.
    @Query("""
        SELECT * FROM issued_keys
        WHERE readerId = :readerIdHex
          AND status = 'active'
          AND expiresAtMs > :nowMs
          AND (:thisDevice = 0 OR belongsToThisDevice = 1)
        ORDER BY expiresAtMs DESC
        LIMIT 1
    """)
    suspend fun firstActiveForReader(
        readerIdHex: String,
        nowMs: Long,
        thisDevice: Boolean
    ): IssuedKeyEntity?

    // Мягкий перевод истёкших active-ключей в 'expired' (периодически из UI).
    @Query("UPDATE issued_keys SET status = 'expired' WHERE status = 'active' AND expiresAtMs <= :nowMs")
    suspend fun markExpired(nowMs: Long): Int

    // Смена статуса по результату tap-сессии / серверного отзыва.
    @Query("UPDATE issued_keys SET status = :status WHERE keyIdHex = :keyIdHex")
    suspend fun setStatus(keyIdHex: String, status: String)

    // Список ключей permit'а отсортирован по приоритету статуса
    // (active → revoked_by_server → revoked_by_reader → revoked_in_bloom →
    // expired), внутри группы — по убыванию expiresAtMs. Отозванные/истёкшие
    // остаются видимы как «архив» на экране ключей.
    @Query("""
        SELECT * FROM issued_keys WHERE permitId = :permitId
        ORDER BY
          CASE status
            WHEN 'active'            THEN 0
            WHEN 'revoked_by_server' THEN 1
            WHEN 'revoked_by_reader' THEN 2
            WHEN 'revoked_in_bloom'  THEN 3
            WHEN 'expired'           THEN 4
            ELSE 5
          END,
          expiresAtMs DESC
    """)
    fun findByPermit(permitId: String): Flow<List<IssuedKeyEntity>>
}

@Dao
interface OutgoingReportDao {
    /**
     * Upsert с дедупликацией по (type, targetReaderId) для FDI и BLK.
     * Для receipt — просто INSERT.
     */
    @Transaction
    suspend fun saveDedup(report: OutgoingReportEntity) {
        when (report.type) {
            "filter_delivery_info", "blacklist_report" -> {
                deleteByTypeAndReader(report.type, report.targetReaderId)
            }
        }
        insert(report)
    }
    
    @Insert
    suspend fun insert(report: OutgoingReportEntity)
    
    @Query("DELETE FROM outgoing_reports WHERE type=:type AND targetReaderId=:readerId")
    suspend fun deleteByTypeAndReader(type: String, readerId: String)
    
    @Query("SELECT * FROM outgoing_reports ORDER BY producedAtMs")
    fun observeAll(): Flow<List<OutgoingReportEntity>>
    
    @Query("DELETE FROM outgoing_reports WHERE reportId IN (:ids)")
    suspend fun deleteByIds(ids: List<String>)
}
```

## 6. Криптография

### 6.1 Ed25519 через BouncyCastle

`data/crypto/Ed25519.kt`:

```kotlin
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.security.SecureRandom

object Ed25519 {
    fun generateKeyPair(): Pair<ByteArray, ByteArray> {
        val rnd = SecureRandom()
        val priv = Ed25519PrivateKeyParameters(rnd)
        val pub = priv.generatePublicKey()
        return priv.encoded to pub.encoded  // priv 32 B, pub 32 B
    }
    
    fun publicKeyFromPrivate(privKeyBytes: ByteArray): ByteArray {
        val priv = Ed25519PrivateKeyParameters(privKeyBytes, 0)
        return priv.generatePublicKey().encoded
    }
    
    fun sign(privKeyBytes: ByteArray, message: ByteArray): ByteArray {
        val priv = Ed25519PrivateKeyParameters(privKeyBytes, 0)
        val signer = Ed25519Signer()
        signer.init(true, priv)
        signer.update(message, 0, message.size)
        return signer.generateSignature()  // 64 B
    }
    
    fun verify(pubKeyBytes: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        val pub = Ed25519PublicKeyParameters(pubKeyBytes, 0)
        val verifier = Ed25519Signer()
        verifier.init(false, pub)
        verifier.update(message, 0, message.size)
        return verifier.verifySignature(signature)
    }
}
```

### 6.2 KeyManager — Keystore-wrapped Ed25519

`data/crypto/KeyManager.kt`:

```kotlin
@Singleton
class KeyManager @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val keystoreAlias = "scud_wrapping_key"
    
    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context, "scud_secure_prefs", masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }
    
    /** Создаёт новую пару Ed25519, обёртывает privkey через Keystore AES-GCM. */
    fun generateAndStore(): ByteArray {
        val (priv, pub) = Ed25519.generateKeyPair()
        val aesKey = getOrCreateWrappingKey()
        val (iv, wrapped) = aesGcmEncrypt(aesKey, priv)
        priv.fill(0)  // затереть priv сразу после оборачивания
        
        prefs.edit()
            .putString("ed_priv_wrapped_b64", Base64.encodeToString(wrapped, Base64.NO_WRAP))
            .putString("ed_priv_iv_b64", Base64.encodeToString(iv, Base64.NO_WRAP))
            .putString("ed_pub_b64", Base64.encodeToString(pub, Base64.NO_WRAP))
            .apply()
        
        return pub
    }
    
    fun hasKeyPair(): Boolean {
        return prefs.contains("ed_priv_wrapped_b64")
    }
    
    fun getPublicKey(): ByteArray {
        val s = prefs.getString("ed_pub_b64", null) ?: error("no keypair")
        return Base64.decode(s, Base64.NO_WRAP)
    }
    
    /** Подписывает сообщение; приватник в RAM только на время подписи. */
    fun sign(message: ByteArray): ByteArray {
        val wrapped = Base64.decode(prefs.getString("ed_priv_wrapped_b64", null), Base64.NO_WRAP)
        val iv = Base64.decode(prefs.getString("ed_priv_iv_b64", null), Base64.NO_WRAP)
        val aesKey = getWrappingKey() ?: error("keystore key missing")
        
        val priv = aesGcmDecrypt(aesKey, iv, wrapped)
        try {
            return Ed25519.sign(priv, message)
        } finally {
            priv.fill(0)
        }
    }
    
    fun clear() {
        prefs.edit().clear().apply()
        try {
            val ks = KeyStore.getInstance("AndroidKeyStore")
            ks.load(null)
            ks.deleteEntry(keystoreAlias)
        } catch (_: Exception) {}
    }
    
    private fun getOrCreateWrappingKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore")
        ks.load(null)
        if (ks.containsAlias(keystoreAlias)) return (ks.getEntry(keystoreAlias, null) as KeyStore.SecretKeyEntry).secretKey
        
        val keyGen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        val specBuilder = KeyGenParameterSpec.Builder(
            keystoreAlias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setUserAuthenticationRequired(false)
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            specBuilder.setUnlockedDeviceRequired(true)
            // StrongBox — если доступен (try/catch на setIsStrongBoxBacked)
            try { specBuilder.setIsStrongBoxBacked(true) } catch (_: Exception) {}
        }
        
        keyGen.init(specBuilder.build())
        return keyGen.generateKey()
    }
    
    private fun getWrappingKey(): SecretKey? {
        val ks = KeyStore.getInstance("AndroidKeyStore")
        ks.load(null)
        return (ks.getEntry(keystoreAlias, null) as? KeyStore.SecretKeyEntry)?.secretKey
    }
    
    private fun aesGcmEncrypt(key: SecretKey, plaintext: ByteArray): Pair<ByteArray, ByteArray> {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        val ct = cipher.doFinal(plaintext)
        return iv to ct
    }
    
    private fun aesGcmDecrypt(key: SecretKey, iv: ByteArray, ct: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
        return cipher.doFinal(ct)
    }
}
```

### 6.3 BLAKE2s

BouncyCastle предоставляет `org.bouncycastle.crypto.digests.Blake2sDigest`:

```kotlin
import org.bouncycastle.crypto.digests.Blake2sDigest

object Blake2s {
    fun compute(data: ByteArray, digestBits: Int = 128): ByteArray {
        val digest = Blake2sDigest(digestBits)
        digest.update(data, 0, data.size)
        val out = ByteArray(digestBits / 8)
        digest.doFinal(out, 0)
        return out
    }
}

fun computeKeyId(readerId: ByteArray, phonePubkey: ByteArray, issuedAtSec: Long, serial: Int): ByteArray {
    val buf = ByteBuffer.allocate(16 + 32 + 8 + 4).order(ByteOrder.LITTLE_ENDIAN)
    buf.put(readerId)
    buf.put(phonePubkey)
    buf.putLong(issuedAtSec)
    buf.putInt(serial)
    return Blake2s.compute(buf.array(), 128)  // 16 B
}
```

### 6.4 Domains

```kotlin
object Domains {
    val KEY = "RDR-KEY-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val INF = "RDR-INF-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val RSP = "RDR-RSP-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val FLT = "RDR-FLT-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val RCP = "RDR-RCP-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val BLK = "RDR-BLK-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val FDI = "RDR-FDI-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val TGR = "RDR-TGR-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val TIM = "RDR-TIM-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val REV = "RDR-REV-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    
    // shared §15 — passage_receipt, подписанный ридером
    val PSG = "RDR-PSG-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    // shared §17 — BLE session_token, подписанный ридером (опционально, v1.1)
    val BLE = "RDR-BLE-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)

    init {
        listOf(KEY, INF, RSP, FLT, RCP, BLK, FDI, TGR, TIM, REV, PSG, BLE).forEach {
            check(it.size == 16) { "domain tag must be 16 B" }
        }
    }
}
```

Всего 12 domain tags. `PSG` и `BLE` — расширения протокола (passage_receipt и
BLE session_token); остальные 10 совпадают с базовым набором shared §3.

### 6.5 Сериализация

Все структуры паковать в little-endian:

```kotlin
object Serialization {
    /**
     * Parse INFO struct (shared §5.2). 146 B.
     * Передаётся reader'ом через PUSH_INFO APDU — НЕ имеет wire opcode префикса.
     */
    fun parseInfo(bytes: ByteArray): InfoStruct {
        require(bytes.size == 146) { "INFO must be 146 B, got ${bytes.size}" }
        val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val formatVersion = buf.get().toInt() and 0xFF
        require(formatVersion == 1) { "unsupported INFO format_version $formatVersion" }
        val readerId = ByteArray(16).also { buf.get(it) }
        val readerTime = buf.long
        val protocolVersion = buf.get().toInt() and 0xFF
        val maxApduSize = buf.short.toInt() and 0xFFFF
        val filterVersion = buf.long
        val filterDeliveredAt = buf.long
        val blacklistCount = buf.short.toInt() and 0xFFFF
        val freshNonce = ByteArray(32).also { buf.get(it) }
        val sessionSeq = buf.int
        val signature = ByteArray(64).also { buf.get(it) }
        return InfoStruct(
            formatVersion, readerId, readerTime, protocolVersion, maxApduSize,
            filterVersion, filterDeliveredAt, blacklistCount,
            freshNonce, sessionSeq, signature,
            signedRange = bytes.sliceArray(0 until 82)
        )
    }
    
    data class InfoStruct(
        val formatVersion: Int,
        val readerId: ByteArray,
        val readerTime: Long,
        val protocolVersion: Int,
        val maxApduSize: Int,
        val filterVersion: Long,
        val filterDeliveredAt: Long,
        val blacklistCount: Int,
        val freshNonce: ByteArray,
        val sessionSeq: Int,
        val reader_signature: ByteArray,
        val signedRange: ByteArray      // первые 82 B — то, что верифицируется
    )
    
    /**
     * Parse ACCESS_VERDICT result (shared §5.4). 42 B.
     */
    data class AccessVerdict(
        val result: Byte,
        val readerTime: Long,
        val nextNonce: ByteArray
    ) {
        val resultName: String get() = when (result) {
            0x00.toByte() -> "OK"
            0x20.toByte() -> "EXPIRED"
            0x21.toByte() -> "REVOKED_BLACKLIST"
            0x22.toByte() -> "REVOKED_FILTER"
            else -> "ERR_%02X".format(result.toInt() and 0xFF)
        }
    }
    
    fun parseAccessVerdict(bytes: ByteArray): AccessVerdict {
        require(bytes.size == 42) { "verdict must be 42 B" }
        require(bytes[0] == 0x81.toByte()) { "verdict marker mismatch" }
        val buf = ByteBuffer.wrap(bytes, 1, 41).order(ByteOrder.LITTLE_ENDIAN)
        val result = buf.get()
        val readerTime = buf.long
        val nextNonce = ByteArray(32).also { buf.get(it) }
        return AccessVerdict(result, readerTime, nextNonce)
    }
    
    // buildAccessOperation / buildTimeSyncOperation / buildRevokeKeyOperation / 
    // buildFilterUpdateOperation / extractNextNonce / extractReceiptFromFilterResult /
    // opResultSucceeded / ... — см. §8.6
}
```

## 7. Retrofit API

```kotlin
interface ScudApi {
    
    @POST("api/v1/app/auth/login")
    suspend fun login(@Body req: LoginRequest): LoginResponse
    
    @POST("api/v1/app/auth/refresh")
    suspend fun refresh(@Body req: RefreshRequest): LoginResponse
    
    @POST("api/v1/app/auth/register-device")
    suspend fun registerDevice(@Body req: RegisterDeviceRequest): RegisterDeviceResponse
    
    @POST("api/v1/app/auth/logout")
    suspend fun logout(): OkResponse
    
    @GET("api/v1/app/my-data")
    suspend fun myData(): MyDataResponse
    
    @GET("api/v1/app/permits")
    suspend fun permits(): PermitListResponse
    
    @GET("api/v1/app/permits/{permitId}/keys")
    suspend fun permitKeys(@Path("permitId") permitId: String): KeyListResponse
    
    @POST("api/v1/app/permits/{permitId}/revoke")
    suspend fun revokePermit(@Path("permitId") permitId: String): OkResponse
    
    @POST("api/v1/app/keys/request")
    suspend fun requestKey(@Body req: RequestKeyRequest): RequestKeyResponse
    
    @POST("api/v1/app/keys/{keyId}/revoke-on-server")
    suspend fun revokeKeyOnServer(@Path("keyId") keyIdHex: String): OkResponse
    
    @GET("api/v1/app/readers/{readerId}")
    suspend fun reader(@Path("readerId") readerIdHex: String): ReaderResponse
    
    @GET("api/v1/app/readers")
    suspend fun readersByGroup(@Query("group_id") groupId: String): ReaderListResponse
    
    @GET("api/v1/app/courier/available")
    suspend fun courierAvailable(): CourierAvailableResponse
    
    @POST("api/v1/app/courier/download")
    suspend fun courierDownload(@Body req: DownloadRequest): DownloadResponse
    
    @POST("api/v1/app/reports/submit")
    suspend fun submitReports(@Body req: SubmitReportsRequest): SubmitReportsResponse
}
```

DTO (kotlinx.serialization):

```kotlin
@Serializable
data class LoginRequest(
    val login: String,
    val password: String,
    @SerialName("device_info") val deviceInfo: DeviceInfoDto
)

@Serializable
data class LoginResponse(
    @SerialName("session_token") val sessionToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("user_id") val userId: Int,
    @SerialName("user_group_id") val userGroupId: String,
    @SerialName("display_name") val displayName: String
)

@Serializable
data class RequestKeyRequest(
    @SerialName("permit_id") val permitId: String,
    @SerialName("validity_seconds") val validitySeconds: Int,
    @SerialName("request_grant") val requestGrant: Boolean
)

@Serializable
data class RequestKeyResponse(
    @SerialName("issued_key") val issuedKey: IssuedKeyDto,
    @SerialName("time_grant") val timeGrant: TimeGrantDto? = null
)

@Serializable
data class IssuedKeyDto(
    @SerialName("key_id") val keyIdHex: String,
    @SerialName("full_key_bytes") val fullKeyBase64: String,
    @SerialName("issued_at") val issuedAt: String,  // ISO-8601
    @SerialName("expires_at") val expiresAt: String
)

// ... аналогично для всех
```

AuthInterceptor:

```kotlin
class AuthInterceptor @Inject constructor(
    private val accountProvider: dagger.Lazy<CurrentAccount>,
    private val refreshProvider: dagger.Lazy<RefreshFlow>
) : Interceptor {
    
    override fun intercept(chain: Interceptor.Chain): Response {
        val account = accountProvider.get().get() ?: return chain.proceed(chain.request())
        
        var request = chain.request().newBuilder()
            .header("Authorization", "Bearer ${account.sessionToken}")
            .build()
        
        var response = chain.proceed(request)
        
        if (response.code == 401 && account.refreshToken.isNotBlank()) {
            response.close()
            val refreshed = runBlocking { refreshProvider.get().attemptRefresh(account.refreshToken) }
            if (refreshed != null) {
                request = chain.request().newBuilder()
                    .header("Authorization", "Bearer ${refreshed.sessionToken}")
                    .build()
                response = chain.proceed(request)
            }
        }
        
        return response
    }
}
```

## 8. HCE Service и tap-flow

**Модель wire-протокола** (см. shared §4): Reader (PN532 initiator) посылает APDU-команды, Android HCE target отвечает на них. Phone здесь — **пассивный отвечающий**, а не активный отправитель, как было бы в симметричной модели.

Весь флоу one-tap-сессии:

1. `SELECT AID` → `0x9000`.
2. `PUSH_INFO` (reader даёт свой INFO 146 B) → phone верифицирует, строит operations queue → `0x9000`.
3. Цикл `FETCH` (reader запрашивает следующую операцию, прислал prev_result от предыдущей) → phone отдаёт одну операцию из очереди (OP_SINGLE или OP_CHUNKED) или NO_OP.
4. Если операция не помещается в один APDU, reader делает `READ_CHUNK` APDU — phone отдаёт следующий чанк из буфера.
5. Если reader хочет отдать большой result — серия `PUSH_CHUNK` APDU, phone складывает в буфер по msg_id. Затем reader делает FETCH с prev_result = REFERENCE(msg_id).
6. `END` → phone очищает сессию.

### 8.1 ScudHceService

**Отложенный ответ (N4).** `processCommandApdu` возвращает `null` и НЕ блокирует
NFC binder-поток: вся работа (DAO + AndroidKeyStore) выполняется в корутине
`TapController`, а готовый ответ доставляется системе позже через
`sendResponseApdu(...)` — штатный механизм `HostApduService` для асинхронного
ответа. Callback `respond` пробрасывается в `handleApdu`.

```kotlin
@AndroidEntryPoint
class ScudHceService : HostApduService() {
    
    @Inject lateinit var tapController: TapController
    
    override fun processCommandApdu(apdu: ByteArray?, extras: Bundle?): ByteArray? {
        if (apdu == null) return SW_WRONG_LENGTH
        // N4: работа уходит с NFC binder-потока в корутину TapController.
        // Возвращаем null — ответ придёт через sendResponseApdu.
        tapController.handleApdu(apdu) { resp ->
            try {
                sendResponseApdu(resp)
            } catch (e: Exception) {
                Log.e("HCE", "sendResponseApdu failed", e)
            }
        }
        return null
    }
    
    override fun onDeactivated(reason: Int) {
        tapController.onDeactivated(reason)
    }
    
    companion object {
        // ISO/IEC 7816-4 status words
        val SW_OK             = byteArrayOf(0x90.toByte(), 0x00)
        val SW_WRONG_LENGTH   = byteArrayOf(0x67.toByte(), 0x00)
        val SW_FILE_NOT_FOUND = byteArrayOf(0x6A.toByte(), 0x82.toByte())
        val SW_REF_NOT_FOUND  = byteArrayOf(0x6A.toByte(), 0x88.toByte())
        val SW_UNKNOWN        = byteArrayOf(0x6F.toByte(), 0x00)
    }
}
```

### 8.2 TapController (Singleton)

Координирует всё состояние tap-сессии. **Модель — корутинная (N4).** `handleApdu`
вызывается из `processCommandApdu` на NFC binder-потоке, но саму обработку
запускает в корутине на `Dispatchers.IO` и сразу возвращает `Unit`; ответ
доставляется через callback `respond` (→ `sendResponseApdu`). Это снимает
ограничение HCE-таймаута: тяжёлые suspend-вызовы (Room DAO, AndroidKeyStore sign)
идут вне binder-потока.

Доступ к сессии сериализуется через `kotlinx.coroutines.sync.Mutex` (а не
JVM-монитор `synchronized` и не `Object`): этот замок можно удерживать через
suspend-вызовы DAO/Keystore. Ридер шлёт APDU строго по одному (ждёт ответ),
поэтому contention минимальна; Mutex сериализует обработку APDU с
`reset()`/`onDeactivated()`/watchdog'ом. **`runBlocking` в коде отсутствует** —
все обработчики `handle*` суспендят.

```kotlin
@Singleton
class TapController @Inject constructor(
    private val sessionHolder: TapSessionHolder,
    private val decisionTree: TapDecisionTree,
    private val keyManager: KeyManager,
    private val readerDao: ReaderDao,
    private val issuedKeyDao: IssuedKeyDao,
    private val contactHistoryDao: ContactHistoryDao,
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val pendingRevokeDao: PendingRevokeIntentDao,
    private val outgoingReportDao: OutgoingReportDao,
    private val tapLog: TapLog,
    private val haptic: HapticFeedback,
    @ApplicationScope private val scope: CoroutineScope
) {
    // N4: suspend-safe lock вместо synchronized(Object) — держится через
    // suspend-вызовы DAO/Keystore.
    private val amutex = Mutex()
    
    // N4: вызывается из ScudHceService.processCommandApdu. Не блокирует NFC
    // binder-поток — работа уходит в корутину, ответ доставляется через respond.
    fun handleApdu(apdu: ByteArray, respond: (ByteArray) -> Unit) {
        scope.launch(Dispatchers.IO) {
            val resp = try {
                amutex.withLock { handleApduInner(apdu) }
            } catch (e: Exception) {
                Log.e(TAG, "handleApdu failed", e)
                SW_UNKNOWN
            }
            respond(resp)
        }
    }
    
    private suspend fun handleApduInner(apdu: ByteArray): ByteArray {
        if (apdu.size < 4) return SW_WRONG_LENGTH
        
        val cla = apdu[0]
        val ins = apdu[1]
        
        // SELECT AID
        if (cla == 0x00.toByte() && ins == 0xA4.toByte() && apdu[2] == 0x04.toByte()) {
            sessionHolder.startNew().touch()
            return SW_OK
        }
        
        val session = sessionHolder.current
        if (session == null) {
            // Нет сессии: на FETCH (0xC2) возвращаем явный ERROR(SESSION_LOST)
            // в валидном FETCH-конверте, чтобы ридер начал заново (shared §4.5).
            return if (ins == 0xC2.toByte()) byteArrayOf(0x03, 0x02) + SW_OK
                   else SW_REF_NOT_FOUND
        }
        session.touch()
        
        // Command dispatch — все обработчики suspend.
        return when (ins) {
            0xC1.toByte() -> handlePushInfo(session, apdu)
            0xC2.toByte() -> handleFetch(session, apdu)
            0xC3.toByte() -> handleReadChunk(session, apdu)
            0xC4.toByte() -> handlePushChunk(session, apdu)
            0xC5.toByte() -> handleEnd(session)
            else -> SW_UNKNOWN
        }
    }
    
    fun onDeactivated(reason: Int) {
        scope.launch {
            val snapshot: TapSession? = amutex.withLock {
                inactivityWatchdog?.cancel(); inactivityWatchdog = null
                val s = sessionHolder.current
                sessionHolder.clear()
                s
            }
            // Если handleEnd уже сделал финализацию — session.committed=true,
            // выходим сразу. Иначе (аномальный обрыв без END) коммитим здесь —
            // onDeactivated работает как fallback.
            if (snapshot != null && !snapshot.committed) {
                publishCompleted(snapshot, finalMessageOverride = null)
                finishSessionAsync(snapshot, origin = "onDeactivated")
            }
        }
    }
    
    // … private handle_* methods (см. §8.5)
}
```

Приватное состояние контроллера, на которое ссылается код выше и §8.5:
`inactivityWatchdog: Job?` (корутинный watchdog бездействия, §8.7),
`sigFailStreak: Int` + `SIG_FAIL_RETRY_LIMIT = 2` (retry проверки подписи, §8.7),
а также `_uiState: MutableStateFlow<TapUiState>` (публикуется наружу как
`uiState`). Хелперы: `armInactivityWatchdog(session)`, `publishCompleted(...)`
(обновляет `uiState` + триггерит `haptic.granted()/denied()/neutral()`),
`finishSessionAsync(session, origin)` (единая идемпотентная точка коммита, guard
`session.committed`). Константы: `INACTIVITY_TIMEOUT_MS = 5000`,
`MAX_INCOMING_BUFFERS = 2`, `SIGNED_OPCODES = listOf<Byte>(0x01, 0x12, 0x15)`.

### 8.3 TapSession

Держит всё состояние одного касания. Не переживает `onDeactivated`.

```kotlin
class TapSession {
    val createdAt: Long = System.currentTimeMillis()
    var lastActivityAt: Long = System.currentTimeMillis()

    // true после того, как finishSessionAsync один раз сохранил всю
    // накопленную телеметрию и pending-задачи. Guard от двойного коммита
    // (handleEnd + onDeactivated).
    @Volatile var committed: Boolean = false

    // Поднимается в handlePushInfo, если подпись не прошла и решено запросить
    // повтор PUSH_INFO через FETCH_ERROR=SESSION_LOST (см. §8.7).
    @Volatile var pushInfoSigRetryPending: Boolean = false

    var readerId: ByteArray? = null
    var readerPubkey: ByteArray? = null      // известен из readers_known либо null
    var readerTimeSec: Long = 0
    var currentFreshNonce: ByteArray? = null // обновляется от signed result-ответов
    var maxApduSize: Int = 256
    var sessionSeq: Int = 0
    var filterVersion: Long = 0
    var blacklistCount: Int = 0

    fun touch() { lastActivityAt = System.currentTimeMillis() }
    
    // Operations queue, построенная после PUSH_INFO
    val operationsQueue: ArrayDeque<PreparedOperation> = ArrayDeque()
    var lastSentOperation: PreparedOperation? = null
    
    // Активные op_chunked буферы для READ_CHUNK (msg_id → bytes)
    val outgoingChunks: MutableMap<Int, ByteArray> = mutableMapOf()
    
    // Активные PUSH_CHUNK буферы от reader (msg_id → growing bytes)
    val incomingChunks: MutableMap<Int, IncomingChunkBuffer> = mutableMapOf()
    
    var lastAccessVerdict: String? = null
    
    // Для сессионного журнала
    val operationLog: MutableList<String> = mutableListOf()
    
    fun historyJson(): String = buildString {
        append("{\"ops\":[")
        append(operationLog.joinToString(","))
        append("]}")
    }
    
    // Информация, что делать после onDeactivated / END
    // (удалить delivered pending_filter_delivery, добавить receipts в outgoing и т.п.)
    val pendingDeliveredIds: MutableList<String> = mutableListOf()
    val pendingDeliveredRevokes: MutableList<Long> = mutableListOf()
    val pendingOutgoingReports: MutableList<OutgoingReportEntity> = mutableListOf()
    
    suspend fun commitPendingChanges(
        pendingFilterDao: PendingFilterDeliveryDao,
        pendingRevokeDao: PendingRevokeIntentDao,
        outgoingReportDao: OutgoingReportDao
    ) {
        pendingDeliveredIds.forEach { pendingFilterDao.markDelivered(it) }
        // REVOKE_KEY доставлен на ридер и принят (result ok) — задача полностью
        // отработала, запись больше не нужна → удаляем из локальной БД.
        pendingDeliveredRevokes.forEach { pendingRevokeDao.deleteById(it) }
        pendingOutgoingReports.forEach { outgoingReportDao.saveDedup(it) }
    }
}

class IncomingChunkBuffer(val total: Int) {
    val bytes = ByteArray(total)
    var filled = 0
    var lastSeen = System.currentTimeMillis()
}

/**
 * Одна операция в очереди.
 *
 * Если `builder != null`, байты операции строятся ЛЕНИВО в момент handleFetch —
 * это выносит дорогой AndroidKeyStore.sign из handlePushInfo (где блокировка
 * чревата HCE-дропом) и позволяет ответить на PUSH_INFO быстро. Иначе отдаются
 * уже готовые `bytes`. Лениво строятся signed-операции: ACCESS, TIME_SYNC,
 * REVOKE_KEY.
 */
data class PreparedOperation(
    val innerOpcode: Byte,
    val bytes: ByteArray = ByteArray(0),                 // готовый payload (если не lazy)
    val builder: ((TapSession) -> ByteArray)? = null,    // ленивая сборка с актуальным nonce
    val onResult: (ByteArray) -> Unit = {},              // callback с prev_result
    val debugName: String
)
```

### 8.4 Построение operations queue

Вызывается из `handlePushInfo`. Логика соответствует дереву решений из stage3 (stage3_android_app.md §3.8).

```kotlin
class TapDecisionTree @Inject constructor(
    private val timeGrantDao: TimeGrantDao,
    private val pendingFilterDao: PendingFilterDeliveryDao,
    private val issuedKeyDao: IssuedKeyDao,
    private val pendingRevokeDao: PendingRevokeIntentDao,
    private val keyManager: KeyManager,
    private val account: CurrentAccount
) {
    
    suspend fun buildOperations(session: TapSession): List<PreparedOperation> {
        val ops = mutableListOf<PreparedOperation>()
        val nowDevice = System.currentTimeMillis() / 1000   // секунды
        val readerIdBytes = session.readerId ?: return ops
        val readerIdHex = readerIdBytes.toHex()
        val readerTimeSec = session.readerTimeSec
        val driftSec = kotlin.math.abs(nowDevice - readerTimeSec)
        
        // (1) TIME_SYNC, если grant есть и drift > TIME_DRIFT_THRESHOLD_SEC (15 c).
        // Сам подписанный statement строится ЛЕНИВО в builder (KeyManager.sign идёт
        // через AndroidKeyStore и заблокировал бы NFC-тред в handlePushInfo) — здесь
        // только замыкаем grant-данные.
        val grant = timeGrantDao.firstActiveForReader(readerIdHex, nowDevice * 1000)
        if (grant != null && driftSec > TIME_DRIFT_THRESHOLD_SEC) {
            val authorityId = Serialization.extractAuthorityIdFromGrant(grant.fullGrantBytes)
            val grantBytes = grant.fullGrantBytes
            val grantKind = grant.kindByte()
            ops.add(PreparedOperation(
                innerOpcode = 0x12,
                builder = { s ->
                    val nonce = s.currentFreshNonce ?: return@PreparedOperation ByteArray(0)
                    val statement = Serialization.buildTimeSyncStatement(
                        readerId = readerIdBytes,
                        authorityId = authorityId,
                        newTime = System.currentTimeMillis() / 1000,
                        usedNonce = nonce,
                        kind = grantKind,
                        keyManager = keyManager
                    )
                    Serialization.buildTimeSyncOperation(
                        grantBytes = grantBytes,
                        statementBytes = statement
                    )
                },
                debugName = "TIME_SYNC"
            ))
        }
        
        // (2) FILTER_UPDATE, если есть pending_delivery для этого reader_id
        val pending = pendingFilterDao.firstFor(readerIdHex, status = "downloaded")
        if (pending != null && pending.filterVersion > session.filterVersion) {
            val opBytes = Serialization.buildFilterUpdateOperation(
                courierIdHex = pending.courierIdHex,
                filterPackageBytes = pending.filterPackageBytes
            )
            ops.add(PreparedOperation(
                innerOpcode = 0x13,
                bytes = opBytes,
                onResult = { result -> 
                    // result — OP_RESULT (0x93) с delivery_receipt + next_nonce
                    // Парсить, добавить receipt в outgoing_reports, отметить delivery как delivered
                    session.pendingDeliveredIds.add(pending.deliveryId)
                    val receipt = Serialization.extractReceiptFromFilterResult(result)
                    session.pendingOutgoingReports.add(OutgoingReportEntity(
                        reportId = UUID.randomUUID().toString(),
                        type = "delivery_receipt",
                        targetReaderId = readerIdHex,
                        bytes = receipt,
                        producedAtMs = System.currentTimeMillis()
                    ))
                },
                debugName = "FILTER_UPDATE"
            ))
        }
        
        // (3) FDI — всегда
        ops.add(PreparedOperation(
            innerOpcode = 0x11,
            bytes = byteArrayOf(0x11),   // payload — только opcode
            onResult = { result ->
                // result = FDI response (241 B), marker 0x91
                // Добавить в outgoing_reports (deduped по reader_id)
                session.pendingOutgoingReports.add(OutgoingReportEntity(
                    reportId = UUID.randomUUID().toString(),
                    type = "filter_delivery_info",
                    targetReaderId = readerIdHex,
                    bytes = result,
                    producedAtMs = System.currentTimeMillis()
                ))
            },
            debugName = "FDI"
        ))
        
        // (4) GET_BLACKLIST, если blacklist_count > 0
        if (session.blacklistCount > 0) {
            ops.add(PreparedOperation(
                innerOpcode = 0x14,
                bytes = byteArrayOf(0x14),
                onResult = { result ->
                    // BLK, marker 0x94, variable size
                    session.pendingOutgoingReports.add(OutgoingReportEntity(
                        reportId = UUID.randomUUID().toString(),
                        type = "blacklist_report",
                        targetReaderId = readerIdHex,
                        bytes = result,
                        producedAtMs = System.currentTimeMillis()
                    ))
                },
                debugName = "GET_BLACKLIST"
            ))
        }
        
        // (5) REVOKE_KEY для каждого pending intent. Подпись (AndroidKeyStore)
        // тоже выносится в lazy builder.
        val pendingRevokes = pendingRevokeDao.forReader(readerIdHex, "pending")
        for (intent in pendingRevokes) {
            // requester — любой активный ключ пользователя на этом ридере
            val requester = issuedKeyDao.firstActiveForReader(readerIdHex, nowDevice * 1000, thisDevice = true)
                ?: continue  // нет requester'а — пропустить (пользователь может отозвать только через сервер)
            val intentCopy = intent
            val requesterBytes = requester.fullKeyBytes
            ops.add(PreparedOperation(
                innerOpcode = 0x15,
                builder = { s ->
                    val nonce = s.currentFreshNonce ?: return@PreparedOperation ByteArray(0)
                    Serialization.buildRevokeKeyOperation(
                        requesterKey = requesterBytes,
                        targetKey = intentCopy.targetFullKeyBytes,
                        usedNonce = nonce,
                        readerTimeEcho = readerTimeSec,
                        readerIdBytes = readerIdBytes,
                        signer = keyManager
                    )
                },
                onResult = { result ->
                    // OP_RESULT 0x95 — при успехе помечаем intent доставленным.
                    if (Serialization.opResultSucceeded(result)) {
                        session.pendingDeliveredRevokes.add(intentCopy.intentId)
                    }
                },
                debugName = "REVOKE_KEY(${intent.targetKeyIdHex.take(8)})"
            ))
        }
        
        // (6) ACCESS — последний, если есть пригодный key. innerOpcode=0x01
        // обрабатывается в handleFetch особым образом (lazy-build с актуальным
        // nonce и свежайшим ключом — см. §8.5).
        val usable = issuedKeyDao.firstActiveForReader(readerIdHex, nowDevice * 1000, thisDevice = true)
        if (usable != null) {
            ops.add(PreparedOperation(
                innerOpcode = 0x01,
                bytes = byteArrayOf(),
                onResult = { result ->
                    try {
                        val verdict = Serialization.parseAccessVerdict(result)
                        session.lastAccessVerdict = verdict.resultName
                        // ACCESS_VERDICT layout: marker(1) result(1) …
                        // При RES_OK дополнительно ставим в очередь
                        // GET_PASSAGE_RECEIPT (shared §15) — см. ниже.
                        if (result.size >= 2 && result[1] == 0x00.toByte()) {
                            enqueuePassagePickup(session, readerIdHex)
                        }
                    } catch (_: Exception) {}
                },
                debugName = "ACCESS"
            ))
        }
        
        return ops
    }

    /**
     * Добавляет в operations queue запрос GET_PASSAGE_RECEIPT (inner_opcode 0x16)
     * прямо во время обработки результата ACCESS — новый op подхватится на
     * следующем FETCH (порядок гарантирован). Ридер уже скэшировал данные
     * прохода, квитанция придёт inline (225 B: marker 0x96 + receipt 192 +
     * next_nonce 32). В outgoing уходит только валидный PASSAGE_ENVELOPE (0x96);
     * PASSAGE_NONE (0x97) игнорируется. Это даёт серверу учёт проходов без
     * онлайна ридера.
     */
    private fun enqueuePassagePickup(session: TapSession, readerIdHex: String) {
        session.operationsQueue.addLast(PreparedOperation(
            innerOpcode = 0x16,
            bytes = byteArrayOf(0x16),
            onResult = { result ->
                if (result.isNotEmpty() && result[0] == 0x96.toByte() && result.size >= 1 + 192) {
                    val receipt = result.copyOfRange(1, 1 + 192)
                    session.pendingOutgoingReports.add(OutgoingReportEntity(
                        reportId = UUID.randomUUID().toString(),
                        type = "passage_receipt",
                        targetReaderId = readerIdHex,
                        bytes = receipt,
                        producedAtMs = System.currentTimeMillis()
                    ))
                }
            },
            debugName = "GET_PASSAGE_RECEIPT"
        ))
    }

    companion object {
        const val TIME_DRIFT_THRESHOLD_SEC = 15L
    }
}
```

**Важно про nonce chain.** Следующая signed-операция (ACCESS, TIME_SYNC, REVOKE_KEY) использует `currentFreshNonce`, который приходит в result от предыдущей операции. Поэтому:
- `currentFreshNonce` при построении очереди = `info.freshNonce`.
- Перед отдачей каждой signed-операции через `FETCH` — её payload собирается ЛЕНИВО через `PreparedOperation.builder` с **актуальным** nonce (на случай если между операциями был UNSIGNED op, который не менял nonce, или SIGNED который поменял). ACCESS (0x01) — особый случай: строится прямо в `handleFetch` (см. §8.5).
- На практике всё signed-building (включая дорогой `KeyManager.sign` через AndroidKeyStore) откладывается из `handlePushInfo` до момента перед отправкой в `handleFetch` — это позволяет ответить на PUSH_INFO быстро и не словить HCE-дроп.

### 8.5 Обработчики APDU команд

```kotlin
// Все — suspend-методы TapController, вызываются из handleApduInner под amutex.
// runBlocking НЕ используется: DAO/Keystore вызываются напрямую как suspend.

private suspend fun handlePushInfo(session: TapSession, apdu: ByteArray): ByteArray {
    if (apdu.size < 5 + 146) return SW_WRONG_LENGTH
    val lc = apdu[4].toInt() and 0xFF
    if (lc != 146) return SW_WRONG_LENGTH
    val infoBytes = apdu.sliceArray(5 until 5 + 146)
    
    val info = try { Serialization.parseInfo(infoBytes) }
              catch (_: Exception) { return SW_WRONG_LENGTH }
    
    session.readerId = info.readerId
    session.readerTimeSec = info.readerTime
    session.currentFreshNonce = info.freshNonce
    session.maxApduSize = info.maxApduSize
    session.filterVersion = info.filterVersion
    session.blacklistCount = info.blacklistCount
    session.sessionSeq = info.sessionSeq
    
    // Верификация reader_signature, если ридер известен.
    val readerKnown = readerDao.find(info.readerId.toHex())
    if (readerKnown != null) {
        val signedPayload = Domains.INF + info.signedRange
        if (Ed25519.verify(readerKnown.readerPubkey, signedPayload, info.reader_signature)) {
            sigFailStreak = 0
            session.readerPubkey = readerKnown.readerPubkey
        } else {
            // RF-корапт в signed-диапазоне. До SIG_FAIL_RETRY_LIMIT раз принимаем
            // PUSH_INFO (SW_OK), но на первом FETCH вернём FETCH_ERROR=SESSION_LOST,
            // чтобы ридер переотправил PUSH_INFO с новым fresh_nonce (см. §8.7).
            sigFailStreak++
            session.readerPubkey = null
            if (sigFailStreak <= SIG_FAIL_RETRY_LIMIT) {
                session.pushInfoSigRetryPending = true
                armInactivityWatchdog(session)
                return SW_OK
            }
            // Превышен лимит — продолжаем без signed-операций (их содержимое
            // FILTER_UPDATE/BLK подписано сервером, доставить можно).
        }
    } else {
        session.readerPubkey = null  // ридер неизвестен — signed-ops пропускаем
    }
    
    // Build operations queue. Signed-операции (ACCESS/TIME_SYNC/REVOKE_KEY)
    // пропускаются, если readerPubkey == null.
    val allOps = decisionTree.buildOperations(session)
    val ops = allOps.filter {
        session.readerPubkey != null || it.innerOpcode !in SIGNED_OPCODES
    }
    session.operationsQueue.addAll(ops)
    armInactivityWatchdog(session)
    
    return SW_OK
}

private suspend fun handleFetch(session: TapSession, apdu: ByteArray): ByteArray {
    // Если предыдущий PUSH_INFO не прошёл verify — просим ридера переотправить
    // его (FETCH_ERROR=0x03, reason=SESSION_LOST=0x02). Ридер очистит prev_result
    // и пришлёт свежий PUSH_INFO с новым nonce.
    if (session.pushInfoSigRetryPending) {
        session.pushInfoSigRetryPending = false
        return build_response(byteArrayOf(0x03, 0x02), SW_OK)
    }
    if (apdu.size < 5) return SW_WRONG_LENGTH
    val lc = apdu[4].toInt() and 0xFF
    if (apdu.size < 5 + lc + 1) return SW_WRONG_LENGTH  // +1 Le
    val data = apdu.sliceArray(5 until 5 + lc)
    
    // Parse prev_result
    if (data.size < 2) return SW_WRONG_LENGTH
    val b0 = data[0].toInt() and 0xFF
    val b1 = data[1].toInt() and 0xFF
    
    val prevResultBytes: ByteArray? = when {
        b0 == 0 && b1 == 0 -> null
        b0 == 0xFF && b1 == 0xFF -> {
            if (data.size < 6) return SW_WRONG_LENGTH
            val msgId = ByteBuffer.wrap(data, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
            val buf = session.incomingChunks[msgId]
            if (buf == null || buf.filled != buf.total) return SW_REF_NOT_FOUND
            session.incomingChunks.remove(msgId)
            buf.bytes
        }
        else -> {
            val len = b0 or (b1 shl 8)
            if (data.size < 2 + len) return SW_WRONG_LENGTH
            data.sliceArray(2 until 2 + len)
        }
    }
    
    // Подать prev_result предыдущей операции (если была)
    if (prevResultBytes != null && session.lastSentOperation != null) {
        try {
            session.lastSentOperation!!.onResult(prevResultBytes)
        } catch (e: Exception) {
            Log.e("TAP", "onResult callback failed", e)
        }
        // Обновить currentFreshNonce из result (если есть next_nonce в формате)
        val nextNonce = Serialization.extractNextNonce(prevResultBytes)
        if (nextNonce != null) session.currentFreshNonce = nextNonce
    }
    session.lastSentOperation = null
    
    // Выбрать следующую операцию
    val op = session.operationsQueue.removeFirstOrNull()
        ?: return build_response(byteArrayOf(0x00 /*NO_OP*/), SW_OK)
    
    // Lazy-build с актуальным nonce.
    val opBytes: ByteArray = when (op.innerOpcode) {
        0x01.toByte() -> {
            // ACCESS — build with current nonce и свежайшим активным ключом.
            val usable = issuedKeyDao.firstActiveForReader(
                session.readerId!!.toHex(), System.currentTimeMillis(), thisDevice = true
            ) ?: return build_response(byteArrayOf(0x03, 0x01), SW_OK)  // ERROR BAD_PREV
            val nonce = session.currentFreshNonce
                ?: return build_response(byteArrayOf(0x03, 0x01), SW_OK)
            Serialization.buildAccessOperation(
                issuedKey = usable.fullKeyBytes,
                usedNonce = nonce,
                readerTimeEcho = session.readerTimeSec,
                readerId = session.readerId!!,
                keyManager = keyManager
            )
        }
        else -> {
            // Если у операции задан builder (TIME_SYNC / REVOKE_KEY) — строим
            // лениво (AndroidKeyStore.sign вынесен сюда из buildOperations).
            val builder = op.builder
            if (builder != null) {
                builder(session).also { built ->
                    if (built.isEmpty()) return build_response(byteArrayOf(0x03, 0x01), SW_OK)
                }
            } else op.bytes
        }
    }
    session.lastSentOperation = op
    
    // Упаковать в OP_SINGLE или OP_CHUNKED в зависимости от размера
    val firstChunkMax = session.maxApduSize - 12 /*header*/ - 2 /*SW*/
    return if (opBytes.size + 4 + 2 <= session.maxApduSize) {
        // OP_SINGLE: [status=01][inner 1B][len 2B LE][bytes]
        val buf = ByteBuffer.allocate(4 + opBytes.size).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01)
        buf.put(op.innerOpcode)
        buf.putShort(opBytes.size.toShort())
        buf.put(opBytes)
        build_response(buf.array(), SW_OK)
    } else {
        // OP_CHUNKED: [status=02][inner 1B][msg_id 4B][total 4B LE][first_chunk_len 2B LE][first_chunk_bytes]
        val msgId = Random.nextInt()
        session.outgoingChunks[msgId] = opBytes
        val firstChunkLen = minOf(opBytes.size, firstChunkMax)
        val buf = ByteBuffer.allocate(12 + firstChunkLen).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x02)
        buf.put(op.innerOpcode)
        buf.putInt(msgId)
        buf.putInt(opBytes.size)
        buf.putShort(firstChunkLen.toShort())
        buf.put(opBytes, 0, firstChunkLen)
        build_response(buf.array(), SW_OK)
    }
}

private fun handleReadChunk(session: TapSession, apdu: ByteArray): ByteArray {
    if (apdu.size < 5 + 10 + 1) return SW_WRONG_LENGTH
    val data = apdu.sliceArray(5 until 15)
    val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
    val msgId = buf.int
    val offset = buf.int
    val maxLen = buf.short.toInt() and 0xFFFF
    
    val fullBytes = session.outgoingChunks[msgId] ?: return SW_REF_NOT_FOUND
    val remaining = fullBytes.size - offset
    if (remaining < 0) return SW_WRONG_LENGTH
    
    val chunkLen = minOf(remaining, maxLen)
    val flags = if (chunkLen == remaining) 0x01.toByte() else 0x00.toByte()
    
    val out = ByteBuffer.allocate(3 + chunkLen).order(ByteOrder.LITTLE_ENDIAN)
    out.putShort(chunkLen.toShort())
    out.put(flags)
    out.put(fullBytes, offset, chunkLen)
    
    if (flags == 0x01.toByte()) {
        session.outgoingChunks.remove(msgId)
    }
    
    return build_response(out.array(), SW_OK)
}

private fun handlePushChunk(session: TapSession, apdu: ByteArray): ByteArray {
    if (apdu.size < 5 + 15 + 1) return SW_WRONG_LENGTH
    val lc = apdu[4].toInt() and 0xFF
    val data = apdu.sliceArray(5 until 5 + lc)
    
    val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
    val msgId = buf.int
    val offset = buf.int
    val total = buf.int
    val flags = buf.get()
    val chunkLen = buf.short.toInt() and 0xFFFF
    val chunkBytes = ByteArray(chunkLen).also { buf.get(it) }
    
    val buffer = session.incomingChunks.getOrPut(msgId) { IncomingChunkBuffer(total) }
    if (buffer.total != total || offset + chunkLen > total) return SW_WRONG_LENGTH
    
    System.arraycopy(chunkBytes, 0, buffer.bytes, offset, chunkLen)
    buffer.filled += chunkLen
    buffer.lastSeen = System.currentTimeMillis()
    
    return SW_OK
}

private fun handleEnd(session: TapSession): ByteArray {
    publishCompleted(session, finalMessageOverride = "Сессия завершена")
    // Commit ДО clear: иначе onDeactivated увидит null snapshot и накопленные
    // отчёты (FDI, BLK, REVOKE_KEY ack, passage_receipt) потеряются.
    // finishSessionAsync ставит session.committed=true (guard), поэтому
    // onDeactivated не сделает повторный коммит.
    finishSessionAsync(session, origin = "END")
    inactivityWatchdog?.cancel(); inactivityWatchdog = null
    sessionHolder.clear()
    return SW_OK
}

private fun build_response(payload: ByteArray, sw: ByteArray): ByteArray {
    return payload + sw
}
```

### 8.6 Построение подписанных операций

`Serialization.buildAccessOperation`, `buildTimeSyncOperation`, `buildRevokeKeyOperation` выполняют:
1. Собрать payload-структуру согласно shared §5.
2. Вызвать `keyManager.sign(Domains.XXX + signing_input)` для получения `phone_signature`.
3. Вернуть полный payload с signature.

Все эти методы должны быть идемпотентны. Сам `keyManager.sign` идёт через AndroidKeyStore и может занять ~200-400 мс, поэтому вызывается ЛЕНИВО (в `handleFetch` через `PreparedOperation.builder` / ветку ACCESS), а не в `handlePushInfo` — это держит ответ на PUSH_INFO быстрым и не роняет HCE-сессию.

```kotlin
object Serialization {
    // ... parseInfo, extractNextNonce, parseAccessVerdict, и т.п.
    
    fun buildAccessOperation(
        issuedKey: ByteArray,           // 151 B
        usedNonce: ByteArray,            // 32 B
        readerTimeEcho: Long,
        readerId: ByteArray,             // 16 B
        keyManager: KeyManager
    ): ByteArray {
        require(issuedKey.size == 151)
        require(usedNonce.size == 32)
        require(readerId.size == 16)
        
        val keyId = issuedKey.sliceArray(0..15).let { /* первые 16 B header */
            // но key_id не там, он вычисляется отдельно.
            // Исправление: key_id = BLAKE2s(reader_id||phone_pubkey||issued_at||serial, 16)
            computeKeyIdFromIssuedKey(issuedKey)
        }
        
        val signingInput = readerId + usedNonce + 
            ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array() + 
            keyId
        val signature = keyManager.sign(Domains.RSP + signingInput)
        
        val buf = ByteBuffer.allocate(256).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(issuedKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }
    
    fun buildTimeSyncOperation(
        grantBytes: ByteArray,      // 148 B
        statementBytes: ByteArray   // 140 B
    ): ByteArray {
        require(grantBytes.size == 148)
        require(statementBytes.size == 140)
        val buf = ByteBuffer.allocate(289)
        buf.put(0x12.toByte())
        buf.put(grantBytes)
        buf.put(statementBytes)
        return buf.array()
    }
    
    fun buildRevokeKeyOperation(
        requesterKey: ByteArray,    // 151 B
        targetKey: ByteArray,       // 151 B
        usedNonce: ByteArray,        // 32 B
        readerTimeEcho: Long,
        readerIdBytes: ByteArray,    // 16 B
        signer: KeyManager
    ): ByteArray {
        require(requesterKey.size == 151)
        require(targetKey.size == 151)
        val requesterKeyId = computeKeyIdFromIssuedKey(requesterKey)
        val targetKeyId = computeKeyIdFromIssuedKey(targetKey)
        
        val signingInput = readerIdBytes + usedNonce + 
            ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array() +
            requesterKeyId + targetKeyId
        val signature = signer.sign(Domains.REV + signingInput)
        
        val buf = ByteBuffer.allocate(407).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x15.toByte())
        buf.put(requesterKey)
        buf.put(targetKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }
    
    fun buildFilterUpdateOperation(
        courierIdHex: String,
        filterPackageBytes: ByteArray
    ): ByteArray {
        val courierId = hexToBytes(courierIdHex)
        require(courierId.size == 16)
        val buf = ByteBuffer.allocate(1 + 16 + filterPackageBytes.size)
        buf.put(0x13.toByte())
        buf.put(courierId)
        buf.put(filterPackageBytes)
        return buf.array()
    }
    
    fun extractNextNonce(resultBytes: ByteArray): ByteArray? {
        if (resultBytes.isEmpty()) return null
        // Shared §9: nonce-ring ридера ведётся только для signed-операций
        // (ACCESS, TIME_SYNC, REVOKE_KEY). Хотя FDI (0x91) и BLK (0x94) физически
        // содержат next_nonce в своём ответе, на стороне phone он НЕ используется
        // как "свежий" для следующего signed-запроса — это привело бы к рассогласованию
        // с ring'ом ридера. Возвращаем null для unsigned-опов.
        return when (resultBytes[0]) {
            0x81.toByte() -> {  // ACCESS_VERDICT
                if (resultBytes.size >= 42) resultBytes.sliceArray(10..41) else null
            }
            0x92.toByte(), 0x93.toByte(), 0x95.toByte() -> {  // OP_RESULT (TIME_SYNC / FILTER_UPDATE / REVOKE_KEY)
                // next_nonce — последние 32 B ext
                if (resultBytes.size >= 32) resultBytes.sliceArray(resultBytes.size - 32 until resultBytes.size) else null
            }
            else -> null  // 0x91 FDI, 0x94 BLK — не consume'им nonce
        }
    }
    
    // ... парсеры для verdict, extraction receipt и т.п.
}
```

### 8.7 Таймауты и очистка

- **Inactivity watchdog**: после PUSH_INFO армируется корутинный watchdog (`INACTIVITY_TIMEOUT_MS = 5000` мс). Он раз в 1 c сверяет `now - session.lastActivityAt`; если ридер молчит дольше 5 c — сессия сбрасывается, UI переходит в `Failed("Связь с ридером потеряна")`. Каждый APDU вызывает `session.touch()`, обновляя `lastActivityAt`. (Это не «TTL 30 c» — watchdog следит именно за тишиной между APDU.)
- **Нет сессии на FETCH**: если APDU прилетел без активной сессии, на `FETCH` (0xC2) возвращается явный `ERROR(SESSION_LOST)` (`0x03 0x02` + `0x9000`) в валидном FETCH-конверте — ридер начинает заново. На прочие INS возвращается `0x6A88`.
- **Sig-fail retry**: если `reader_signature` в PUSH_INFO не прошла verify, до `SIG_FAIL_RETRY_LIMIT = 2` раз phone принимает PUSH_INFO (SW_OK), но на первом же FETCH возвращает `FETCH_ERROR=SESSION_LOST` (`0x03 0x02`). Ридер переотправляет PUSH_INFO с новым `fresh_nonce` — если предыдущий FAIL был из-за RF-битов в эфире, свежие байты проходят verify. При превышении лимита signed-операции просто пропускаются.
- **Incoming chunk buffers**: удаляются после полной сборки (consumed в FETCH с REFERENCE). Максимум 2 активных incoming-буфера (`MAX_INCOMING_BUFFERS`, shared §4.7) — самый старый вытесняется.
- **Outgoing chunk buffers**: удаляются после последнего READ_CHUNK (flags.LAST=1).
- **Финализация сессии**: коммит всей накопленной телеметрии (`ContactHistory`, pending filter deliveries → `markDelivered`, pending revoke intents → `deleteById`, outgoing reports → `saveDedup`) делается **в `handleEnd` (на END от ридера)** через `finishSessionAsync`, ДО очистки сессии. `onDeactivated` — резервный путь: если END не пришёл (аномальный обрыв поля), коммит делается там. Двойной коммит исключён guard'ом `session.committed`. Сам коммит всегда **асинхронный** (`scope.launch(Dispatchers.IO)`).

## 8bis. BLE-канал (shared §16)

Альтернативный транспорт для того же протокола операций, что и HCE. Пакет `ble/`.
Где HCE использует APDU-команды от ридера, BLE использует GATT: phone — **GATT
client**, ридер — **GATT server**. Логика операций (INFO → очередь → ACCESS/
TIME_SYNC/FILTER_UPDATE/FDI/BLK/REVOKE/PASSAGE) идентична; меняется только wire.
BLE — опциональная фича: устройства без BLE работают через NFC как раньше
(`uses-feature bluetooth_le required=false`).

### 8bis.1 GATT-профиль

UUID'ы и константы — в `ble/BleConstants.kt` (единый контракт с
`firmware/src/ble/ble_channel.cpp`; менять только синхронно):

| Константа | UUID / значение | Назначение |
|---|---|---|
| `SERVICE_UUID`        | `5c0da001-…-000000000000` | сервис SCUD (по нему идёт скан) |
| `CHR_INFO_NOTIFY`     | `…-000000000001` | notify: INFO от ридера (146 B) |
| `CHR_OP_WRITE`        | `…-000000000002` | write_no_response: операция от phone |
| `CHR_RESULT_NOTIFY`   | `…-000000000003` | notify: result операции от ридера |
| `CHR_CONTROL`         | `…-000000000004` | write: суб-команды RESET (0x01) / END (0x02) |
| `MTU_REQUEST`         | `247` | целевой MTU (iOS/Android обычно дают 247) |

### 8bis.2 BleSession — зеркало TapSession

`ble/BleSession.kt` — один сеанс с конкретным ридером. Корутинный жизненный цикл:

```kotlin
val session = BleSession(context, device)
val info = session.connect()        // suspends until INFO received (146 B)
val result = session.runOperation(opBytes)  // write op → await result
session.close()
```

`connect()` пошагово (через `CompletableDeferred` на каждый GATT-колбэк):
`connectGatt(TRANSPORT_LE)` → `discoverServices()` → `requestMtu(247)` →
`enableNotifications` на `CHR_INFO_NOTIFY` и `CHR_RESULT_NOTIFY` (через запись CCC
дескриптора) → приём первого INFO push из `infoChannel`. `runOperation` назначает
1-байтовый `op_seq` (§16.5.1), пишет `[op_seq][op_bytes]` в `CHR_OP_WRITE` и ждёт
собранный ответ с тем же `op_seq` (корреляция по `pending[op_seq]→Deferred`, не
позиционно). `close()` шлёт CONTROL=END и закрывает GATT.

### 8bis.3 Framing и flow control

Каждое логическое сообщение нарезается на PDU размером `mtu - 3` (ATT header;
20 B если MTU не согласован). Формат кадра (shared §16.5):

```
PDU = [seq 1B] [flags 1B] [total_len 4B LE — только на первом PDU] [chunk]
flags: bit0 = LAST, bit1 = HAS_TOTAL (total_len несёт только seq==0)
```

- **Produce** — чистая функция `BleFraming.frame(data, maxPdu)` (юнит-тестится
  против golden-векторов `ble_framing`, байт-в-байт зеркалит firmware
  `ble_frame_message`).
- **Consume** — `ReassemblyBuf.feed(pdu)`: накапливает кадры (контроль `seq`,
  сверка `total_len`) и возвращает собранный blob по флагу LAST. Отдельные буферы
  на INFO и RESULT.
- **Flow control B1**: исходящие кадры пишутся `WRITE_NO_RESPONSE` строго
  по одному — следующий кадр шлётся только после `onCharacteristicWrite`
  предыдущего (`pendingWrite` / `CompletableDeferred`). Иначе пачка
  write_no_response переполняет очередь GATT и Android молча теряет кадры →
  ридер видит `seq`-gap и рвёт операцию. Таймаут ack одного кадра —
  `WRITE_ACK_TIMEOUT_MS = 3000` мс.

`ble/BleScanner.kt` ищет ридеры по `SERVICE_UUID`; UI — `ui/ble/`
(`BleReadersScreen` + `BleSessionScreen` и их ViewModel'и).

## 9. UI экраны

### 9.1 Навигация

```kotlin
sealed class Screen(val route: String) {
    object Auth : Screen("auth")
    object Home : Screen("home")
    object Tap : Screen("tap")
    object Permits : Screen("permits")
    object PermitDetail : Screen("permits/{permitId}") {
        fun of(permitId: String) = "permits/$permitId"
    }
    object Keys : Screen("keys?permitId={permitId}")
    object Tasks : Screen("tasks")
    object Settings : Screen("settings")
}
```

При запуске — проверка: если `account == null` → Auth, иначе Home.

### 9.2 AuthScreen

Требования:
- Поля: домен (может быть `ip:port`), логин, пароль.
- Кнопка "Войти".
- Ошибки показываются inline.
- Успех → генерация ключей → register-device → Home.

```kotlin
@Composable
fun AuthScreen(onLoggedIn: () -> Unit, viewModel: AuthViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    
    Column(Modifier.fillMaxSize().padding(24.dp)) {
        Text("SCUD", style = MaterialTheme.typography.headlineLarge)
        
        OutlinedTextField(
            value = state.domain,
            onValueChange = viewModel::onDomainChange,
            label = { Text("Домен или ip:port") }
        )
        OutlinedTextField(
            value = state.login,
            onValueChange = viewModel::onLoginChange,
            label = { Text("Логин") }
        )
        OutlinedTextField(
            value = state.password,
            onValueChange = viewModel::onPasswordChange,
            label = { Text("Пароль") },
            visualTransformation = PasswordVisualTransformation()
        )
        
        if (state.error != null) {
            Text(state.error, color = MaterialTheme.colorScheme.error)
        }
        
        Button(
            onClick = { viewModel.submit(onLoggedIn) },
            enabled = !state.loading
        ) {
            if (state.loading) CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.size(20.dp))
            else Text("Войти")
        }
    }
}
```

Логика формирования base_url в AuthViewModel:

```kotlin
fun buildBaseUrl(input: String): String {
    val trimmed = input.trim().removeSuffix("/")
    // Check if it looks like ip:port
    val colonIdx = trimmed.lastIndexOf(':')
    val hasPort = colonIdx > 0 && trimmed.substring(colonIdx + 1).toIntOrNull() != null
    val prefix = if (hasPort) "http://" else "https://"
    return "$prefix$trimmed/"
}
```

### 9.3 HomeScreen

```
┌────────────────────────────────────┐
│  id: petrov                        │
│  3 пропусков  ·  2 ключа выпущено  │
│  5 задач на сервер                 │
│  2 задачи на READER                │
├────────────────────────────────────┤
│                                    │
│        ┌─────────────────┐         │
│        │  [NFC icon]     │         │
│        │  Взаимодейство- │         │
│        │  вать с READER  │         │
│        └─────────────────┘         │
│                                    │
├────────────────────────────────────┤
│   [Пропуски]   [Ключи]  [Задачи]   │
│   [Настройки]                      │
└────────────────────────────────────┘
```

Кнопка "Взаимодействовать с READER" → переход на TapScreen.

Все счётчики — Flow из DAO: `COUNT(*)` соответствующих таблиц.

### 9.4 TapScreen

Полноэкранный экран с прогрессом касания.

**Важная особенность в query-response модели:** phone полностью пассивен во время tap-сессии. Весь прогресс (какая операция выполняется сейчас, каков её результат) наблюдается через callback'и `onResult` внутри `TapSession`. TapController публикует состояние через `StateFlow<TapUiState>`, TapScreen на него подписывается.

**Требования к UI:**
- При входе: вызвать `cardEmulation.setPreferredService(activity, componentName)` для приоритета HCE над другими сервисами.
- Показать анимацию "Поднесите телефон".
- Таймаут 5 минут; при истечении — вернуться на Home.
- Прогресс-сообщения по факту выполнения операций: "Передал INFO", "Получил FILTER_UPDATE → фильтр доставлен", "Получил ACCESS → дверь открыта".
- Итоговый статус: "Дверь открыта ✓" / "Отказ: EXPIRED" / "Ошибка: ..." / список выполненных операций.
- Кнопка "Закрыть" внизу, либо автозакрытие через 5 секунд после access-verdict.

**TapController ↔ UI связка:**

```kotlin
// В TapController:
private val _uiState = MutableStateFlow<TapUiState>(TapUiState.Waiting)
val uiState: StateFlow<TapUiState> = _uiState.asStateFlow()

// Внутри handlePushInfo, после успешного парсинга:
_uiState.value = TapUiState.InProgress(
    readerName = readerKnown?.displayName ?: "Неизвестный ридер",
    completedOps = emptyList()
)

// Внутри handleFetch, при обработке prev_result через onResult callback:
// — после каждой операции append в completedOps.

// При NO_OP в FETCH (финал):
_uiState.value = TapUiState.Completed(...)

// При onDeactivated (разрыв до завершения):
_uiState.value = TapUiState.Completed(...)  // с тем что успели
```

```kotlin
sealed interface TapUiState {
    data object Waiting : TapUiState
    data class InProgress(
        val readerName: String,
        val completedOps: List<CompletedOp>,
        val currentActivity: String = "Обмен с READER…"
    ) : TapUiState
    data class Completed(
        val readerName: String,
        val completedOps: List<CompletedOp>,
        val finalMessage: String,      // "Дверь открыта" / "Сессия завершена"
        val success: Boolean
    ) : TapUiState
    data class Failed(val message: String) : TapUiState
}

data class CompletedOp(
    val name: String,        // "TIME_SYNC" / "FILTER_UPDATE" / ...
    val result: String,      // "OK" / "EXPIRED" / "BAD_SIGNATURE"
    val icon: ImageVector    // для UI: ✓ или ✗ или 🕐
)
```

**TapViewModel:**

```kotlin
@HiltViewModel
class TapViewModel @Inject constructor(
    private val tapController: TapController,
    @ApplicationContext private val context: Context
) : ViewModel() {
    
    val state: StateFlow<TapUiState> = tapController.uiState
    
    private var timeoutJob: Job? = null
    
    fun startTapMode(activity: Activity) {
        val cardEmulation = CardEmulation.getInstance(NfcAdapter.getDefaultAdapter(context))
        val component = ComponentName(context, ScudHceService::class.java)
        cardEmulation.setPreferredService(activity, component)
        
        tapController.reset()  // reset uiState → Waiting
        
        timeoutJob = viewModelScope.launch {
            delay(5 * 60 * 1000L)
            tapController.forceTimeout()
        }
    }
    
    fun stopTapMode(activity: Activity) {
        timeoutJob?.cancel()
        val cardEmulation = CardEmulation.getInstance(NfcAdapter.getDefaultAdapter(context))
        cardEmulation.unsetPreferredService(activity)
    }
}
```

**TapScreen:**

```kotlin
@Composable
fun TapScreen(onClose: () -> Unit, viewModel: TapViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val activity = LocalContext.current as Activity
    
    DisposableEffect(Unit) {
        viewModel.startTapMode(activity)
        onDispose { viewModel.stopTapMode(activity) }
    }
    
    // Auto-close after 5 sec when completed
    LaunchedEffect(state) {
        if (state is TapUiState.Completed) {
            delay(5000)
            onClose()
        }
    }
    
    Column(Modifier.fillMaxSize().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        when (val s = state) {
            is TapUiState.Waiting -> {
                Spacer(Modifier.weight(1f))
                Icon(Icons.Default.Nfc, contentDescription = null, modifier = Modifier.size(96.dp))
                Text("Поднесите телефон к READER")
                CircularProgressIndicator()
                Spacer(Modifier.weight(1f))
            }
            is TapUiState.InProgress -> {
                Text(s.readerName, style = MaterialTheme.typography.titleLarge)
                Text(s.currentActivity)
                LazyColumn {
                    items(s.completedOps) { op ->
                        Row {
                            Icon(op.icon, contentDescription = null)
                            Text("${op.name}: ${op.result}")
                        }
                    }
                }
            }
            is TapUiState.Completed -> {
                Icon(
                    if (s.success) Icons.Default.CheckCircle else Icons.Default.Info,
                    contentDescription = null,
                    modifier = Modifier.size(96.dp),
                    tint = if (s.success) Color.Green else MaterialTheme.colorScheme.primary
                )
                Text(s.finalMessage, style = MaterialTheme.typography.titleLarge)
                LazyColumn {
                    items(s.completedOps) { op ->
                        Row {
                            Icon(op.icon, contentDescription = null)
                            Text("${op.name}: ${op.result}")
                        }
                    }
                }
                Button(onClick = onClose) { Text("Закрыть") }
            }
            is TapUiState.Failed -> {
                Icon(Icons.Default.Error, contentDescription = null)
                Text(s.message, color = MaterialTheme.colorScheme.error)
                Button(onClick = onClose) { Text("Закрыть") }
            }
        }
    }
}
```

### 9.5 PermitsScreen

Требования:
- Список permit cards. В каждом: название, срок действия, `active_keys_count / n_parallel`.
- Индикаторы: 🕐 (часы) если есть активный grant для (permit, phone_pubkey), ✓ (зелёная галочка) если есть issued_key на этом устройстве.
- **Pull-to-refresh** → GET /permits.
- **Dropdown-сортировка**: по expires_at ↑, ↓, по активным ключам ↑, ↓.
- **Switch**: "показывать permits с 0 активных ключей" (по умолчанию ON).
- **Долгое нажатие** → режим выбора с чекбоксами + "выбрать все" в топе.
- В режиме выбора снизу появляется bottom bar:
  - Кнопка "Аннулировать (N)" активна только если все выбранные `active_keys_count == 0`.
  - Нажатие → диалог подтверждения → POST /permits/{id}/revoke для каждого (параллельно через IO-корутины).
- **Swipe влево/вправо в обычном режиме** — аннулирование одного:
  - Если `active_keys_count > 0` → swipe отбрасывается, toast "сначала отзовите N ключей".
  - Иначе → диалог подтверждения → POST /permits/{id}/revoke.
- Tap по permit → Keys с фильтром на этот permit.

```kotlin
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PermitsScreen(onOpenKeys: (permitId: String) -> Unit, viewModel: PermitsViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val pullRefreshState = rememberPullToRefreshState()
    
    if (pullRefreshState.isRefreshing) {
        LaunchedEffect(Unit) { viewModel.refresh(); pullRefreshState.endRefresh() }
    }
    
    Column(Modifier.fillMaxSize().nestedScroll(pullRefreshState.nestedScrollConnection)) {
        // Sort dropdown + switch
        Row(Modifier.padding(8.dp)) {
            SortDropdown(state.sortMode, onSelect = viewModel::onSortChange)
            Spacer(Modifier.weight(1f))
            Switch(checked = state.showEmpty, onCheckedChange = viewModel::onShowEmptyChange)
            Text("0 ключей")
        }
        
        LazyColumn {
            if (state.selectionMode) {
                item {
                    Row(Modifier.clickable { viewModel.toggleSelectAll() }) {
                        Checkbox(checked = state.allSelected, onCheckedChange = null)
                        Text("Выбрать все")
                    }
                }
            }
            items(state.visiblePermits, key = { it.permitId }) { permit ->
                PermitRow(
                    permit = permit,
                    selectionMode = state.selectionMode,
                    selected = permit.permitId in state.selectedIds,
                    onClick = { 
                        if (state.selectionMode) viewModel.toggleSelect(permit.permitId)
                        else onOpenKeys(permit.permitId)
                    },
                    onLongClick = { viewModel.enterSelectionMode(permit.permitId) },
                    onSwipe = { direction -> viewModel.attemptSwipeRevoke(permit.permitId, direction) }
                )
            }
        }
        
        PullToRefreshContainer(state = pullRefreshState, modifier = Modifier.align(Alignment.TopCenter))
    }
    
    if (state.selectionMode) {
        BottomAppBar {
            Button(
                onClick = { viewModel.showBulkRevokeDialog() },
                enabled = state.canBulkRevoke
            ) {
                Text("Аннулировать (${state.selectedIds.size})")
            }
        }
    }
    
    if (state.showConfirmDialog != null) {
        ConfirmRevokeDialog(
            permits = state.showConfirmDialog,
            onConfirm = viewModel::confirmRevoke,
            onDismiss = viewModel::dismissDialog
        )
    }
}
```

### 9.6 KeysScreen

Требования:
- Список issued_keys. Опционально фильтр по permit_id через query-параметр из навигации.
- Dropdown-фильтр "все / конкретный permit" (если пришёл с фильтром — сразу предвыбран).
- Сортировка: по expires_at.
- Ключи на текущем устройстве (`belongsToThisDevice == true`) — выделены **неярким** фоном (subtle backgroundColor отличный от фона).
- Режим выбора (долгое нажатие) с чекбоксами.
- В bottom bar при выделении:
  - Кнопка **"Отозвать через сервер"** (красный фон, иконка ☁). Подтверждение → POST /keys/{id}/revoke-on-server.
  - Кнопка **"Отозвать через READER"** (синий фон, иконка чипа). Подтверждение → INSERT в pending_revoke_intents для каждого выбранного.
- Swipe:
  - **Влево** (красный, облако) → отзыв через сервер.
  - **Вправо** (синий, чип) → отзыв через READER.
  - Подтверждение обязательно.
- Если на экране есть permit-фильтр — над списком показывать блок информации: название permit, описание, `active_keys_count / n_parallel`, кнопка **"Запросить новый ключ"** (активна если `active_keys_count < n_parallel`).

Диалог запроса ключа:
- Поле "Срок действия" (input, dropdown с пресетами: 1ч, 8ч, 24ч, или "Custom" → numeric input). Максимум — `permit.max_token_ttl_seconds`.
- Чекбокс "Запросить grant" (активен и проставлен только если в Room нет активного grant для этого permit).
- Кнопка "Запросить" → POST /keys/request → save in Room → close dialog, scroll to new key.

### 9.7 TasksScreen

Две вкладки: "На сервер" и "На READER".

**Вкладка "На сервер":**
- Список `outgoing_reports` с бейджами по типу (receipt/FDI/BLK).
- Кнопка "Отправить все" → POST /reports/submit → удалить accepted.
- Записи с `retry_count > 3` — красным, плюс текст "3+ попытки".

**Вкладка "На READER":**
- Список `pending_filter_deliveries` с статусом + `pending_revoke_intents`.
- Pull-to-refresh → GET /courier/available → показать новые доступные посылки отдельной секцией "Доступные" с кнопкой "Скачать" на каждой.
- Скачанные посылки отображают: "Посылка для [reader name]", кнопка "Забыть".
- Pending revoke intents отображают: "Отозвать ключ на [reader name]", кнопка "Отменить".
- Объединенная логика: все элементы обеих таблиц показываются как задачи, которые нужно доставить через tap-сценарий.

### 9.8 SettingsScreen

- Отображение: домен, логин, display_name, device_id.
- Кнопка **"Выйти"** с подтверждением → LogoutUseCase: POST /auth/logout + clear Room + KeyManager.clear() + перейти на Auth.

## 10. Use cases (подробнее)

### 10.1 LoginUseCase

```kotlin
class LoginUseCase @Inject constructor(
    private val api: ScudApi,  // с baseUrl, заданным после ввода домена
    private val apiFactory: ScudApiFactory,
    private val accountDao: AccountDao,
    private val keyManager: KeyManager
) {
    suspend operator fun invoke(domainInput: String, login: String, password: String): Result<Unit> {
        return try {
            val baseUrl = AuthHelpers.buildBaseUrl(domainInput)
            val api = apiFactory.create(baseUrl)
            
            val loginResp = api.login(LoginRequest(login, password, deviceInfo()))
            
            // Generate ed25519 keypair if not exists
            val pubkey = if (!keyManager.hasKeyPair()) {
                keyManager.generateAndStore()
            } else {
                keyManager.getPublicKey()
            }
            
            // Save account with tokens but without device_id yet
            accountDao.upsert(AccountEntity(
                domain = domainInput,
                userId = loginResp.userId,
                userGroupId = loginResp.userGroupId,
                displayName = loginResp.displayName,
                sessionToken = loginResp.sessionToken,
                refreshToken = loginResp.refreshToken,
                deviceId = null,
                phonePubkeyBase64 = Base64.encodeToString(pubkey, Base64.NO_WRAP)
            ))
            
            // Register device
            val regResp = api.registerDevice(RegisterDeviceRequest(
                phonePubkey = Base64.encodeToString(pubkey, Base64.NO_WRAP),
                deviceLabel = "${Build.MODEL} (${Build.VERSION.RELEASE})"
            ))
            
            accountDao.updateDeviceId(regResp.deviceId)
            
            Result.success(Unit)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
```

### 10.2 RequestKeyUseCase

```kotlin
class RequestKeyUseCase @Inject constructor(
    private val api: ScudApi,
    private val issuedKeyDao: IssuedKeyDao,
    private val timeGrantDao: TimeGrantDao,
    private val readerDao: ReaderDao,
    private val account: CurrentAccount
) {
    suspend operator fun invoke(
        permitId: String,
        validitySeconds: Int,
        requestGrant: Boolean
    ): Result<IssuedKeyEntity> {
        return runCatching {
            val resp = api.requestKey(RequestKeyRequest(permitId, validitySeconds, requestGrant))
            val keyBytes = Base64.decode(resp.issuedKey.fullKeyBase64, Base64.NO_WRAP)
            
            val entity = IssuedKeyEntity(
                keyIdHex = resp.issuedKey.keyIdHex,
                permitId = permitId,
                readerId = extractReaderIdFromKey(keyBytes).toHex(),
                issuedAtMs = parseIso(resp.issuedKey.issuedAt).toEpochMilli(),
                expiresAtMs = parseIso(resp.issuedKey.expiresAt).toEpochMilli(),
                fullKeyBytes = keyBytes,
                belongsToThisDevice = true
            )
            issuedKeyDao.insert(entity)
            
            resp.timeGrant?.let { gr ->
                val grantBytes = Base64.decode(gr.fullGrantBase64, Base64.NO_WRAP)
                timeGrantDao.insert(TimeGrantEntity(
                    grantId = gr.grantId,
                    permitId = permitId,
                    readerId = entity.readerId,
                    kind = "soft",
                    issuedAtMs = System.currentTimeMillis(),
                    expiresAtMs = parseIso(gr.expiresAt).toEpochMilli(),
                    fullGrantBytes = grantBytes
                ))
            }
            
            // Ensure readers_known row exists (fetch if missing)
            ensureReaderKnown(entity.readerId)
            
            entity
        }
    }
    
    private suspend fun ensureReaderKnown(readerIdHex: String) {
        if (readerDao.find(readerIdHex) == null) {
            val resp = api.reader(readerIdHex)
            readerDao.insert(ReaderKnownEntity(
                readerId = readerIdHex,
                displayName = resp.displayName,
                description = resp.description,
                readerPubkey = Base64.decode(resp.readerPubkey, Base64.NO_WRAP),
                readerGroupId = resp.groupId
            ))
        }
    }
}
```

### 10.3 SubmitReportsUseCase

```kotlin
class SubmitReportsUseCase @Inject constructor(
    private val api: ScudApi,
    private val dao: OutgoingReportDao
) {
    suspend operator fun invoke(): SubmitResult = withContext(Dispatchers.IO) {
        val all = dao.getAll()
        if (all.isEmpty()) return@withContext SubmitResult(0, 0)
        
        val req = SubmitReportsRequest(reports = all.map { r ->
            ReportDto(
                type = r.type,
                targetReaderId = r.targetReaderId,
                bytes = Base64.encodeToString(r.bytes, Base64.NO_WRAP)
            )
        })
        
        val resp = try { api.submitReports(req) }
        catch (e: Exception) {
            dao.incrementRetryCountAll()
            return@withContext SubmitResult(accepted = 0, rejected = all.size, error = e.message)
        }
        
        // accepted[i] соответствует входному reports[i] по индексу (или по id — зависит от API)
        // Здесь предполагается, что сервер возвращает report_id'ы, присвоенные (не наши).
        // Наш подход: индексное соответствие. Так как /reports/submit возвращает accepted (list) и rejected (list с index),
        // мы удаляем accepted'ы по индексу.
        
        val rejectedIndices = resp.rejected.map { it.index }.toSet()
        val toDelete = all.withIndex()
            .filter { it.index !in rejectedIndices }
            .map { it.value.reportId }
        dao.deleteByIds(toDelete)
        
        // Для rejected — increment retry_count
        val toIncrement = all.withIndex()
            .filter { it.index in rejectedIndices }
            .map { it.value.reportId }
        dao.incrementRetryCount(toIncrement)
        
        SubmitResult(accepted = toDelete.size, rejected = toIncrement.size)
    }
}
```

## 11. Конфигурация Retrofit и OkHttp

```kotlin
@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {
    
    @Provides
    @Singleton
    fun provideOkHttp(authInterceptor: AuthInterceptor): OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .addInterceptor(authInterceptor)
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)  // для download посылок до 127 KB
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
    
    @Provides
    @Singleton
    fun provideRetrofitFactory(okHttp: OkHttpClient, json: Json): ScudApiFactory =
        ScudApiFactory(okHttp, json)
}

class ScudApiFactory(
    private val okHttp: OkHttpClient,
    private val json: Json
) {
    private val cache = mutableMapOf<String, ScudApi>()
    
    @Synchronized
    fun create(baseUrl: String): ScudApi {
        return cache.getOrPut(baseUrl) {
            Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(okHttp)
                .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                .build()
                .create(ScudApi::class.java)
        }
    }
}
```

Баз-url всегда заканчивается `/`. Определяется при первом логине из введённого домена.

## 12. Критерии приёмки

1. **Онбординг:** введение домена `scud.example.com`, логин/пароль → вход. Генерация ключей → автоматически. register-device → сохранение device_id. Открывается Home.
2. **HCE работает:** при tap на реальном или эмулированном ридере:
   - SELECT AID → SW_OK.
   - PUSH_INFO → парсится 146 B, верифицируется подпись reader'а (если известен), строится operations queue.
   - FETCH — возвращает одну операцию из очереди в OP_SINGLE/OP_CHUNKED. NO_OP когда очередь пуста.
   - READ_CHUNK — корректно отдаёт чанки больших операций (FILTER_UPDATE, REVOKE_KEY).
   - PUSH_CHUNK — корректно собирает большие prev_result от reader (большие BLK).
3. **Дерево решений:** при tap с имеющимся issued_key — ACCESS-операция попадает в очередь, reader её выполняет, phone получает verdict через prev_result, UI показывает "Дверь открыта".
4. **Nonce chain работает:** последовательность signed-операций (TIME_SYNC → REVOKE_KEY → ACCESS) корректно использует next_nonce из предыдущих results. Ни одна signed-операция не отправляется со stale nonce.
5. **Pull-to-refresh на Permits:** свайп вниз → GET /permits → список обновляется.
6. **Запрос нового ключа:** на экране Keys (с фильтром permit) → диалог → POST /keys/request → ключ и (опционально) grant сохраняются в Room.
7. **Отзыв swipe-влево:** свайп на ключе влево → диалог → POST /keys/{id}/revoke-on-server → ключ исчезает из списка активных.
8. **Отзыв swipe-вправо:** свайп вправо → диалог → создаётся pending_revoke_intent → виден на Tasks/на READER.
9. **Курьер:** на Tasks pull-to-refresh → GET /courier/available → видны посылки → tap "Скачать" → POST /courier/download → посылка в Room со статусом downloaded → при tap на нужный ридер доставляется (через FETCH → OP_CHUNKED), receipt приходит от reader через prev_result, попадает в outgoing_reports.
10. **Отправка отчётов:** Tasks/на сервер → "Отправить все" → POST /reports/submit → отчёты удаляются.
11. **Logout:** Settings → Выйти → Room и Keystore полностью очищены.

### 12.1 Unit tests

- `test_compute_key_id`: совпадает с эталоном из Python (сверяется с референсным значением).
- `test_parse_info`: корректно парсит 146 B (без wire opcode префикса), все поля.
- `test_build_access_operation`: корректно собирает 256 B payload (inner_opcode 0x01 в начале + issued_key + nonce + time + signature), подпись валидна на эталонном pubkey.
- `test_build_revoke_key_operation`: корректно собирает 407 B payload с двумя issued_keys и подписью.
- `test_build_time_sync_operation`: корректно собирает 289 B payload.
- `test_build_filter_update_operation`: формирует `0x13 || courier_id(16) || filter_package`.
- `test_parse_access_verdict`: парсит 42 B с marker 0x81, извлекает result и next_nonce.
- `test_extract_next_nonce_from_results`: корректно находит next_nonce в AccessVerdict (0x81), FDI (0x91), BLK (0x94), OP_RESULT (0x92/0x93/0x95).
- `test_outgoing_report_dedup`: для FDI с одинаковым target_reader_id сохраняется только последний.
- `test_keystore_wrapping`: generate → sign → verify через pubkey.

### 12.2 Instrumented tests (androidTest)

- `test_db_migrations`: при upgrade схемы данные сохраняются (хотя сейчас version=1 — тест будет шаблоном).
- `test_hce_select_aid`: при получении SELECT AID APDU service отвечает SW_OK.
- `test_hce_push_info_parsing`: PUSH_INFO APDU (0x00 0xC1 0x00 0x00 0x92 <146 B> 0x00) → SW_OK, session.readerId заполнен.
- `test_hce_fetch_empty_queue`: после PUSH_INFO без pending operations → FETCH возвращает `[0x00] 9000` (NO_OP).
- `test_hce_op_chunked_read_chunk`: при большой операции (> MTU) FETCH возвращает OP_CHUNKED, последующие READ_CHUNK корректно отдают остаток, финальный flags.LAST=1.
- `test_hce_push_chunk_reassembly`: серия PUSH_CHUNK APDU от reader с одним msg_id корректно собирается, потом доступна через FETCH с prev_result=REFERENCE.

## 13. Ключевые замечания

- **BuildConfig.APP_VERSION** — автоматически через gradle. Передавать в LoginRequest.device_info.app_version.
- **Минимальный API 26 (Android 8)** — HCE нуждается в relatively modern Android. Если таргетируем на 8.0+, нужно обойтись без features API 28+ или делать fallback (например, StrongBox). `setIsStrongBoxBacked(true)` обёрнуто в try/catch именно поэтому.
- **requireDeviceUnlock=true в apduservice.xml** — требует разблокированного устройства для HCE. Это согласуется с `setUnlockedDeviceRequired(true)` на Keystore-ключе.
- **HCE threading (N4, корутинная модель):** `processCommandApdu()` вызывается системой на NFC binder-потоке, но НЕ блокирует его: возвращает `null`, а ответ доставляется позже через `sendResponseApdu`. Поэтому:
  - Вся обработка APDU идёт в корутине на `Dispatchers.IO`, вне binder-потока — это снимает ограничение HCE-таймаута на тяжёлые операции.
  - Состояние сессии сериализуется через `kotlinx.coroutines.sync.Mutex` (`amutex.withLock`), а НЕ через `synchronized`/`Object` — этот замок можно удерживать через suspend-вызовы DAO/Keystore.
  - Все `handle*`-обработчики, читающие Room (`firstActiveForReader`, `forReader` и т.п.), — `suspend` и вызываются напрямую. **`runBlocking` в коде отсутствует.**
  - Дорогой `KeyManager.sign` (AndroidKeyStore, ~200-400 мс) вынесен из `handlePushInfo` в ленивые `PreparedOperation.builder` (вычисляются в `handleFetch`) — ответ на PUSH_INFO возвращается быстро.
  - Финализация (commit в Room, запись в `contact_history`) — в `handleEnd`/`onDeactivated` через `scope.launch(Dispatchers.IO)`.
- **Nonce chain между операциями:**
  - Первая signed-операция использует `info.freshNonce`.
  - Каждая последующая signed — использует next_nonce из прилетевшего prev_result.
  - Если между signed-операциями был UNSIGNED op (FDI, BLK) — nonce НЕ обновляется (эти операции не consume'ят nonce, и их next_nonce игнорируется).
  - `Serialization.extractNextNonce` (см. §8.6) ротирует `currentFreshNonce` ТОЛЬКО на signed-результатах: **0x81** (ACCESS_VERDICT), **0x92** (TIME_SYNC), **0x93** (FILTER_UPDATE), **0x95** (REVOKE_KEY). Для **0x91** (FDI) и **0x94** (BLK) функция возвращает `null` — phone НЕ потребляет их next_nonce, иначе рассинхронизируется nonce-ring ридера (shared §9).
- **Refresh-поток гонки:** если два параллельных запроса получают 401, refresh должен выполняться только один раз. Использовать Mutex.
- **Не логировать токены:** HttpLoggingInterceptor на Level.BASIC (headers не пишутся). Или добавить sanitizer.
- **ProGuard/R8:** BouncyCastle — добавить rules на сохранение SPI-классов. kotlinx.serialization — `@Keep` на всех @Serializable.
- **При ошибке APDU:** HCE service всегда возвращает валидный 2-байтовый SW, никогда не крашится.
- **Connection pooling OkHttp** по умолчанию. При работе оффлайн все запросы падают быстро с IOException, их должны ловить use cases.
