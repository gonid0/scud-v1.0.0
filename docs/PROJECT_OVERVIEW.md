# SCUD — полное описание проекта

> **Версия 1.0.0** — функционально завершён. Единственный явно открытый пункт — батарейный режим
> (энергосбережение + CLRC663), оформленный как roadmap в [`13_power_hal_architecture.md`](13_power_hal_architecture.md).
> См. также корневой [`README.md`](../README.md) и [`CHANGELOG.md`](../CHANGELOG.md).
>
> Сводный обзор всей системы: бизнес-логика, архитектура, компоненты, протокол, API,
> модель данных, процессы, инварианты безопасности и развёртывание.
>
> Это «карта верхнего уровня». Канонические детали — в `docs/`:
> [`00_shared_protocol`](00_shared_protocol%20%281%29.md) (байты/опкоды/крипто),
> [`05_business_logic`](05_business_logic.md) (логика),
> [`06_api_cheatsheet`](06_api_cheatsheet.md) (curl-рецепты),
> [`04_deployment`](04_deployment.md) (эксплуатация),
> [`03_android_spec`](03_android_spec.md) (ТЗ Android).

---

## 1. Что такое SCUD и зачем

**SCUD** — самостоятельно-хостируемая **офлайн-система контроля и управления доступом** (СКУД): пускает или не пускает людей через двери, турникеты и шлагбаумы на основании выданных администратором пропусков. Это дипломный проект (ВКР), в котором все четыре стороны системы реализованы самостоятельно.

### Ключевой инвариант

**Ридер никогда не выходит в интернет.** Он работает офлайн с локальным белым/чёрным списком, который периодически обновляется. Связь «ридер ↔ сервер» обеспечивает телефон сотрудника в роли **data-mule** (курьера данных):

```
[Server] ←──HTTPS (когда пользователь онлайн)──→ [Android] ←──NFC tap / BLE──→ [Reader] ──GPIO──→ [Lock]
```

Любое решение о проходе ридер принимает сам (есть ли ключ, не отозван ли, не истёк ли). Сервер постфактум получает квитанции о проходах через те же телефоны.

### Чем это полезно

- **Отказоустойчивость**: сбой сервера или сети не блокирует физический проход.
- **Простая инфраструктура**: ридеру нужно только питание (12 В / батарея) — без Ethernet, Wi-Fi, GSM.
- **Self-hosted**: вся БД у заказчика, ничего не уходит в сторонние SaaS.
- **Автоматический учёт посещений**: каждый проход — подписанная ридером квитанция (`passage_receipt`), доставляемая на сервер при следующей синхронизации.

---

## 2. Архитектура системы (4 компонента)

| Компонент | Технологии | Расположение | Роль |
|---|---|---|---|
| **Backend** | Python 3.11+, FastAPI, PostgreSQL 15, SQLAlchemy 2 (async), Alembic | `Backend/` | Хранит данные, выпускает подписанные ключи, генерирует bloom-фильтры, валидирует квитанции, отдаёт админ-API и web-панель |
| **ESP32 Reader** | C++ / Arduino (PlatformIO), PN532 (NFC, UART), DS3231 (RTC), опц. NimBLE | `ESP32/firmware/` | Офлайн-проверка доступа, открытие замка, подпись квитанций |
| **Android App** | Kotlin, Jetpack Compose, Room, Hilt, Retrofit | `AndroidApp/` | Data-mule между сервером и ридерами; NFC HCE / BLE; UI пользователя |
| **Desktop Provisioner** | .NET 9, MAUI, MVVM | `Desktop/ScudProvisioner/` | Первичная настройка ридера: генерация ключей в NVS, enrollment в backend |

Дополнительно: спецификации (единый источник правды по протоколу) — соседние файлы в этой папке (`docs/`); `Code.sln` — solution для Desktop.

### Поток данных

```
                    ┌──────────────────────────────────────┐
                    │  Сеть заказчика (LAN/VPN)            │
   Интернет → nginx │  app ×2 (uvicorn) → postgres 15      │
            TLS+RL  │           ↑                          │
                    │        worker (long-poller)          │
                    └──────────────────────────────────────┘
                              ▲
                              │ HTTPS (опционально, когда пользователь онлайн)
                         ┌──────────┐
                         │ Android  │  data-mule
                         └──────────┘
                          │         │
                  NFC HCE │         │ BLE (опц., только mains-питание)
                          ▼         ▼
                     ┌──────────┐ ┌──────────┐
                     │ ESP32    │ │ ESP32    │
                     │ reader   │ │ reader   │ → GPIO → Lock
                     └──────────┘ └──────────┘
```

### Принцип разработки: единый протокол, три реализации

Документ [`00_shared_protocol`](00_shared_protocol%20%281%29.md) — **единственный источник правды** по байтовым форматам, опкодам и доменам подписи. Backend (Python), Firmware (C++) и Android (Kotlin) — три **независимые реализации одного байт-точного контракта**. Любое изменение протокола обязано синхронно приземлиться во всех трёх (Ed25519-проверка падает на одном перевёрнутом бите).

Байт-идентичность трёх реализаций **доказывается прогоном**: единый golden-корпус [`test_vectors/protocol_v1.json`](test_vectors/protocol_v1.json) (12 доменов, `key_id`, BLAKE2s, Ed25519, MurmurHash3, bloom, все wire-структуры + BLE/APDU-фрейминг) генерируется из backend-референса и сверяется conformance-тестами во **всех трёх** реализациях (backend `pytest`, Android `testDebugUnitTest`, firmware host-тест) — **CI-gated**. Это #1 архитектурная гарантия: один разошедшийся байт ломает verify на ридере.

---

## 3. Акторы и роли

| Актор | Роль |
|---|---|
| **Сотрудник** | Владелец ключа, пользователь Android-app |
| **Администратор** | Выдаёт/отзывает пропуска, мониторит проходы (web-панель или JSON API) |
| **Курьер** | *Право* того же сотрудника: переносит filter-обновления на ридер |
| **Authority** | *Право* телефона переподписывать время ридеру (`time grant`) |
| **Reader** | Железо у двери: проверяет ACCESS, открывает замок |
| **Backend** | Хранит данные, выпускает ключи, валидирует квитанции |
| **External system** | CRM / табельная / 1С — получает события через webhooks |

> Один телефон одного сотрудника может одновременно быть владельцем ключа, курьером и time-authority — это разные **права**, не разные сущности.

---

## 4. Доменные сущности (модель данных)

| Сущность | Описание | Ключевые поля |
|---|---|---|
| **User** | Сотрудник | `user_id`, `login`, `password_hash` (Argon2id), `display_name`, `user_group_id`, `is_active` |
| **UserDevice** | Телефон пользователя (своя Ed25519-пара в Android Keystore) | `device_id`, `user_id`, `phone_pubkey` (32 B), `device_label`, `is_active` |
| **Reader** | Физический ридер | `reader_id` (16 B UUID), `reader_group_id`, `reader_pubkey` (32 B), `server_ed25519_priv/pub`, `server_x25519_priv/pub`, `last_applied_filter_version`, `last_contact_at`, `last_known_time`, `is_active` |
| **ReaderGroup** | Логический контейнер ридеров («этаж», «корпус») | `group_id`, `name`, `description` |
| **Permit** | Право пользователя на доступ к одному ридеру в окне времени | `permit_id`, `user_id`, `reader_id`, `valid_from`, `valid_until`, `n_parallel`, `max_token_ttl_seconds`, `revoke_initiated_at`, `revoked_at` |
| **IssuedKey** | Конкретный 151-байтовый подписанный сервером пропуск | `key_id` (16 B), `permit_id`, `reader_id`, `device_id`, `phone_pubkey`, `issued_at`, `expires_at`, `serial`, `status`, `committed_filter_version`, `full_key_bytes` (151 B) |
| **TimeGrant** | Право телефона переподписывать время ридеру | `grant_id`, `permit_id`, `reader_id`, `authority_pubkey`, `kind` (soft/hard), `expires_at`, `full_grant_bytes` (148 B) |
| **FilterPackage** | Подписанный bloom-фильтр отзывов + whitelist + blacklist-delta | `(filter_version, reader_id)`, `m_bits`, `k_hashes`, `hash_seed`, `whitelist_count`, `blacklist_delta_count`, `package_bytes`, `is_current` |
| **DeliveryTask** | Задача «доставить filter vN на ридер R» | `task_id`, `reader_id`, `filter_version`, `completed_at`, `completion_source` (receipt/superseded), `courier_user_id` |
| **PassageEvent** | Квитанция о проходе (учёт посещений) | `event_id`, `reader_id`, `receipt_nonce`, `key_id`, `permit_id`, `user_id`, `passed_at`, `direction`, `session_seq`, `verdict`, `raw_receipt` (192 B); **unique** `(reader_id, receipt_nonce)` |
| **ReaderReport** | Входящий блоб от ридера через курьера (в очередь worker'у) | `report_id`, `report_type`, `reader_id`, `raw_bytes`, `processed_at` |
| **WebhookSubscription** | Подписка внешней системы на события | `webhook_id`, `url`, `event_types`, `secret` (HMAC), `is_active`, `consecutive_failures` |
| **ApiKey** | Токен для скриптов / интеграций / web-панели (`sk_admin_*`, `sk_integration_*`) | `api_key_id`, `key_hash` (SHA256), `key_prefix`, `kind`, `name`, `expires_at`, `revoked_at` |
| **Session** | Короткоживущий bearer/refresh-токен app-пользователя | `session_id`, `user_id`, `device_id`, `session_token`, `refresh_token`, `*_expires_at` |
| **AdminAuditLog** | Append-only журнал админ-действий | `audit_id`, `occurred_at`, `actor_id`, `action`, `target_type`, `target_id`, `details` (JSON) |
| **BackgroundTask** | Очередь фоновых задач для worker'а | `task_id`, `task_type`, `payload`, `status`, `retry_count` |

---

## 5. Backend (Python / FastAPI)

### 5.1 Технологический стек

- **Python 3.11+**, **FastAPI** (async REST) + **uvicorn**.
- **PostgreSQL 15** + **SQLAlchemy 2** (async ORM) + **asyncpg**; миграции через **Alembic**.
- **PyNaCl** (Ed25519/X25519), **argon2-cffi** (пароли), **mmh3** (MurmurHash3 для bloom), **Pydantic 2**, **itsdangerous** (подпись cookie), **Jinja2** (web-панель).
- Запуск: `uvicorn scud.main:app`; отдельный сервис **worker** (`python -m scud.worker`) для фоновых задач.
- Docker: multistage Dockerfile (~70 МБ), docker-compose: `postgres → migrate (one-shot) → app + worker`.

### 5.2 Слоистая архитектура

```
API-роутеры (FastAPI APIRouter)         src/scud/api/{app,admin,admin_web}/
        ↓  (DI через scud.api.deps)
Доменная логика (бизнес-правила)         src/scud/domain/
        ↓
Репозитории (доступ к данным)            src/scud/db/repositories/
        ↓
ORM-модели (SQLAlchemy)                  src/scud/db/models.py
        ↓
PostgreSQL (asyncpg)
```

Прочие пакеты: `crypto/` (подпись, сериализация, bloom, sealed-box, key_id), `observability/` (Prometheus), `worker_handlers/` (обработчики фоновых задач), `config.py` (Pydantic Settings), `main.py` (app-фабрика).

### 5.3 API: три поверхности и аутентификация

| Префикс | Назначение | Auth |
|---|---|---|
| `/api/v1/app/*` | App API для Android | `Authorization: Bearer <session_token>` |
| `/api/v1/admin/*` | Admin API для скриптов/интеграций | `X-Api-Key: <admin api key>` (SHA256 hash) |
| `/admin/*` | Server-rendered web-панель | Cookie-сессия (login через `/admin/login`, подпись `SCUD_WEB_SECRET`, TTL 8 ч) |
| `/health`, `/metrics` | Healthcheck / Prometheus | без auth (metrics закрывается на уровне сети) |

### 5.4 App API (`/api/v1/app/`)

| Метод + путь | Назначение |
|---|---|
| `POST /auth/login` | Логин → `session_token` + `refresh_token` |
| `POST /auth/refresh` | Ротация токенов |
| `POST /auth/register-device` | Регистрация телефона (`phone_pubkey`) → `device_id` |
| `POST /auth/logout` | Отзыв текущей сессии |
| `GET /my-data` | Сводка: профиль + permits + устройства + активные гранты |
| `GET /permits` | Список permits пользователя |
| `GET /permits/{id}/keys` | Ключи по permit (все устройства) |
| `POST /permits/{id}/revoke` | Пользовательский отзыв permit (если нет активных ключей) |
| `POST /keys/request` | Выпуск нового ключа по permit (+ опц. time-grant) |
| `POST /keys/{id}/revoke-on-server` | Немедленный server-side отзыв ключа |
| `GET /readers` / `GET /readers/{id}` | Список / карточка ридера (с контролем доступа) |
| `GET /courier/available` | Открытые задачи доставки фильтров, доступные пользователю |
| `POST /courier/download` | Скачать конкретный filter-пакет |
| `POST /reports/submit` | Пакетная отправка квитанций (passage_receipt / FDI / blacklist / delivery_receipt) |

### 5.5 Admin API (`/api/v1/admin/`, `X-Api-Key`)

| Группа | Эндпоинты |
|---|---|
| **Users** | `POST/GET /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/reset-password` |
| **Reader Groups** | `POST/GET /reader-groups`, `PATCH/DELETE /reader-groups/{id}` (delete падает, если есть ридеры) |
| **Readers** | `POST /readers/enroll` (генерит server-ключи), `GET /readers`, `GET/PATCH /readers/{id}` |
| **Permits** | `POST/GET /permits`, `GET/PATCH /permits/{id}`, `GET /permits/templates`, `POST /permits/issue-from-template`, `POST /permits/{id}/revoke` (двухфазный) |
| **Keys** | `GET /keys`, `GET /keys/{id}`, `POST /keys/{id}/revoke` |
| **API Keys** | `POST/GET /api-keys`, `POST /api-keys/{id}/revoke` (plaintext показывается один раз; самоотзыв запрещён) |
| **Passages** (read-only) | `GET /passages`, `GET /passages/stats?group_by=reader\|user\|day`, `GET /passages/export.csv` |
| **Webhooks** | `POST/GET /webhooks`, отзыв/toggle (см. §10) |
| **Observability** | `GET /readers/{id}/delivery-status`, `GET /background-tasks`, `GET /audit-log` |

### 5.6 Admin web-панель (`/admin/`, Jinja2)

Dashboard (счётчики + последние проходы + stale-ридеры), Пользователи (CRUD, устройства), Группы ридеров, Ридеры (enrollment, toggle), Permits (создание, двухфазный revoke с цветовой индикацией `active`/`revoking`/`revoked`), Issued keys (фильтр + force-revoke), Проходы (фильтр + CSV-экспорт), API keys, Webhooks, Audit log. Пагинация — cursor-based на больших таблицах.

### 5.7 Crypto-модули (`src/scud/crypto/`)

- **`signing.py`** — Ed25519 с доменным разделением (`sign_detached`/`verify_detached` над `domain || payload`). 12 доменов (см. §9.2).
- **`key_id.py`** — `key_id = BLAKE2s-128(reader_id ‖ phone_pubkey ‖ issued_at_LE ‖ serial_LE)` → 16 B детерминированный идентификатор ключа.
- **`bloom.py`** — построение/проверка bloom-фильтра (MurmurHash3 x86_32 через `mmh3`).
- **`sealed_box.py`** — X25519 + ChaCha20-Poly1305-IETF: расшифровка блобов «ридер → сервер» (FDI, BLK). Layout: `ephemeral_pub(32) ‖ ciphertext+tag`; nonce = BLAKE2b(eph ‖ server_pub)[:12].
- **`serialization.py`** — упаковка/разбор всех байтовых структур (LE, packed): issued_key (151 B), time_grant (148 B), filter_package (variable), delivery_receipt (112 B), passage_receipt (192 B), FDI, BLK.

### 5.8 Worker и фоновые задачи

Отдельный процесс-поллер обрабатывает `background_tasks`: `generate_filter` (генерация bloom, debounce), `process_report` / `process_delivery_receipt` / `process_fdi_blob` (разбор входящих блобов), `notify_webhook` (доставка с retry/backoff). Обработчики — в `src/scud/worker_handlers/`, бизнес-логика — в `src/scud/domain/reports.py`, `filters.py`.

### 5.9 Миграции (Alembic)

`0001_initial` (все базовые таблицы) → `0002_seed_test_data` → `0003_permit_revoke_initiated` (двухфазный revoke) → `0004_passage_events` (учёт проходов + unique `(reader_id, receipt_nonce)`) → `0005_webhook_subscriptions`.

---

## 6. ESP32 Reader (прошивка)

### 6.1 Сборка

- **PlatformIO** + Arduino. Два окружения: `env:esp32dev` (только NFC) и `env:esp32dev_ble` (добавляет `-DSCUD_BLE_ENABLED=1`, линкует NimBLE).
- Зависимости: `RTClib` (DS3231), `NimBLE-Arduino` (только BLE-сборка), PN532 (HSU/UART, в `lib/`). Хранилище: SPIFFS + кастомная `partitions.csv`.

### 6.2 Структура `src/`

| Модуль | Ответственность |
|---|---|
| `main.cpp` | `setup()` + `loop()`: provisioning-проверка, загрузка состояния, polling NFC-таргета, BLE-tick, чистка nonce/blacklist |
| `crypto/` | Ed25519 (verify), BLAKE2s, MurmurHash3, bloom, X25519 sealed-box, доменные константы |
| `hw/` | LED (GPIO2), замок (GPIO26, настраиваемый импульс), DS3231 RTC (I2C) |
| `transport/` | `apdu.cpp` (PN532 UART2 HSU + APDU-обмен), `transfer.cpp` (конечный автомат tap-сессии) |
| `ops/` | Обработчики inner-опкодов (access, fdi, time_sync, filter_update, blacklist, revoke_key, passage) |
| `state/` | NVS+SPIFFS: immutable (ключи provisioning), authoritative (фильтры A/B), local (blacklist + кольцо nonce для anti-replay) |
| `ble/` | NimBLE-peripheral (GATT), chunked-framing (no-op stub без `SCUD_BLE_ENABLED`) |
| `provisioning/` | Serial-CLI для первичной загрузки ключей |

### 6.3 NFC tap-flow (reader = инициатор PN532)

```
SELECT AID (0xA4, AID = F0 53 43 55 44 01 = "SCUD\x01")
   → PUSH_INFO (0xC1): 146-B INFO {reader_id, reader_time, filter_version,
        blacklist_count, fresh_nonce(32), session_seq} + Ed25519(DOMAIN_INF)
   → цикл FETCH (0xC2) / READ_CHUNK (0xC3) / PUSH_CHUNK (0xC4):
        телефон присылает операции, ридер их исполняет и возвращает результат
   → END (0xC5): телефон прислал NO_OP → cooldown 3–4.5 с
```

При `ACCESS=OK` ридер открывает замок (GPIO) и кэширует passage_receipt в RAM до запроса `GET_PASSAGE_RECEIPT`.

### 6.4 Офлайн-решение о доступе (`ops/access.cpp`)

```
1. Проверка nonce (anti-replay, кольцо nonce)
2. Совпадение reader_id          → иначе RES_WRONG_READER
3. Время: expires_at > now, issued_at не в будущем (CLOCK_SKEW) → иначе RES_EXPIRED
4. Вычислить key_id = BLAKE2s(reader_id ‖ phone_pubkey ‖ issued_at ‖ serial)
5. Локальный blacklist           → найден → RES_REVOKED_BLACKLIST
6. Bloom-фильтр + whitelist       → в bloom и не в whitelist → RES_REVOKED_FILTER
7. Ed25519(server_pub, DOMAIN_KEY ‖ issued_key[0:87], server_sig) → иначе RES_BAD_SIGNATURE
8. Ed25519(phone_pubkey, DOMAIN_RSP ‖ reader_id ‖ nonce ‖ time ‖ key_id, phone_sig)
   → RES_OK: открыть замок, закэшировать passage_receipt
```

Три механизма отзыва: **bloom-фильтр** (вероятностный офлайн-список), **whitelist** (искупители false-positive, до 256), **local blacklist** (явный deny, до 256 слотов в NVS).

### 6.5 Синхронизация и квитанции

- **FILTER_UPDATE** (`0x13`): телефон приносит подписанный сервером filter-пакет (chunked). Ридер проверяет подпись, reader_id, монотонность версии → атомарный swap в неактивный SPIFFS-слот A/B.
- **FDI** (`0x11`): ридер докладывает свою версию фильтра sealed-box'ом (X25519) → сервер видит, какие ридеры «протухли».
- **GET_BLACKLIST** (`0x14`): ридер отдаёт sealed-box со своим blacklist для сбора сервером.
- **PASSAGE_RECEIPT** (`0x16`): после `ACCESS=OK` ридер формирует 192-B квитанцию, подписывает `reader_priv`, добавляет случайный `receipt_nonce`. Идемпотентно: после первой выдачи кэш очищается (`PASSAGE_NONE` на повторный запрос в той же сессии).

### 6.6 Хранилище ключей (NVS namespace `scud_imm`, грузится один раз на boot)

`reader_id(16)`, `reader_ed_priv/pub(32)` (подпись INFO/FDI/BLK/PASSAGE), `server_ed_pub(32)` (verify issued_key/grant), `server_x_pub(32)` (sealed-box), `reader_group_id(16)`.

### 6.7 BLE-канал (опц., `esp32dev_ble`)

Ридер = NimBLE-peripheral (Service UUID `5c0da001-…`), телефон = central. Характеристики: INFO_NOTIFY, OP_WRITE, RESULT_NOTIFY, CONTROL. Chunked-framing `[seq][flags][payload]` (MTU ≤ 247). **Семантика операций идентична NFC** — отличается только транспорт.

---

## 7. Android App (data-mule)

### 7.1 Стек

Kotlin (JVM 17), minSdk 26 / targetSdk 34, Jetpack Compose (Material3), Room 2.6, Hilt 2.56, Retrofit 2.11 + OkHttp + kotlinx-serialization, BouncyCastle + AndroidKeyStore (Ed25519), NFC HCE, BLE. Permissions: `INTERNET`, `NFC`, `BLUETOOTH_SCAN/CONNECT`, `VIBRATE`. HCE-сервис `ScudHceService` (`HOST_APDU_SERVICE`, регистрация AID).

### 7.2 Архитектура

`UI (Compose) → ViewModel (Hilt) → UseCase → Repository → (Room + Retrofit)`. Пакеты: `ui/`, `ui/navigation/` (`ScudNavHost`), `hce/` (tap-логика), `ble/` (central), `data/remote/` (Retrofit API), `data/local/` (Room, 9 таблиц), `data/repository/`, `data/crypto/` (домены, сериализация, подпись), `di/` (Hilt-модули), `domain/{model,usecase}/`.

### 7.3 Экраны и навигация (`ScudNavHost`)

| Экран | Назначение |
|---|---|
| **Auth** | Логин (домен/логин/пароль) + регистрация устройства |
| **Home** | Дашборд: статистика (permits/keys/tasks), карточка-герой «приложи телефон» |
| **Tap** | Реалтайм NFC-проход: имя ридера, лог операций, вердикт, haptic-feedback |
| **Permits / PermitDetail** | Список permits и карточка (ридер, срок, n_parallel, активные ключи) |
| **Keys** | Все ключи (свои + других устройств), фильтр по permit, выпуск/отзыв |
| **Tasks** | Две вкладки: pending-загрузки (фильтры) и pending-отчёты; ручной retry/delete |
| **Settings** | Аккаунт (имя, домен, device_id), logout |
| **BLE Readers** | (опц.) скан + подключение к ESP32 по BLE |

### 7.4 HCE tap-логика (`hce/TapController.kt`, `TapDecisionTree.kt`)

Телефон отвечает на APDU ридера. `TapDecisionTree` строит очередь операций по содержимому INFO:

- есть pending-фильтр новее ридерского → **FILTER_UPDATE** (`0x13`)
- дрейф времени > 15 с и есть TimeGrant → **TIME_SYNC** (`0x12`, подпись `RDR-TIM-v1`)
- всегда → **FDI** (`0x11`)
- `blacklist_count > 0` → **GET_BLACKLIST** (`0x14`)
- есть pending revoke-intent → **REVOKE_KEY** (`0x15`, подпись `RDR-REV-v1`)
- есть валидный ключ → **ACCESS** (`0x01`, подпись `RDR-RSP-v1`)
- после `ACCESS=OK` → **GET_PASSAGE_RECEIPT** (`0x16`)

Подпись операций — приватным ключом в **AndroidKeyStore** (не покидает secure enclave). Подпись INFO ридера верифицируется на устройстве (≤2 ретрая по `SESSION_LOST`, иначе подписанные операции пропускаются).

### 7.5 Data-mule sync

**Вниз (сервер → ридер):** login → register-device → `GET /permits` + `/permits/{id}/keys` (фильтр своих по pubkey) → `GET /courier/available` + `POST /courier/download` → сохранить filter-пакет в Room → доставить как FILTER_UPDATE на тапе.

**Вверх (ридер → сервер):** на тапе ридер отдаёт passage_receipt / FDI / BLK / delivery_receipt → сохраняются в `outgoing_reports` → `POST /reports/submit` (пакетно, retry до 5×, удаление принятых).

**Room (9 таблиц):** `account` (1 строка), `permits`, `issued_keys` (151 B), `time_grants` (148 B), `readers_known`, `pending_filter_deliveries`, `pending_revoke_intents`, `outgoing_reports`, `contact_history`.

### 7.6 BLE (central)

`BleScanner` ищет Service UUID `5c0da001-…` (+ short reader_id в manufacturer-data `0xC0DE`). `BleSession`: connect → MTU 247 → notify INFO/RESULT → chunked OP_WRITE. Та же семантика операций, что и NFC.

---

## 8. Desktop Provisioner (.NET MAUI)

Windows-приложение (`net9.0-windows`, MAUI + CommunityToolkit.Mvvm, `System.IO.Ports`) для **первичной настройки ридера**. Архитектура MVVM: Views (Provision/Settings) → ViewModels → Services (`ProvisionFlow`, `SerialClient`, `BackendApi`, `SettingsService`) → Models (DTO).

### Процедура provisioning (4 этапа)

```
[1] GEN-KEYPAIR  → ESP32 САМ генерирует Ed25519-пару (priv остаётся в NVS)
    SHOW-PUBKEY  → утилита читает только pubkey (32 B)
    + генерирует reader_id (UUID v4, 16 B) на ПК
[2] POST /api/v1/admin/readers/enroll {reader_id, reader_pubkey, group_id, name}
    → backend генерирует server_ed25519 + server_x25519 пары, возвращает pub-части
[3] SET-READER-ID / SET-GROUP-ID / SET-SERVER-ED-PUB / SET-SERVER-X-PUB
    / SET-LOCK-DURATION / SET-TIME  (по serial)
[4] COMMIT → атомарный flush NVS
```

Связь с устройством — **serial/USB** (UART, по умолчанию 115200), с backend — **HTTP** (`X-Api-Key`). Сама утилита крипто-операций не делает: ключи генерит ESP32, серверные — backend. Утилита — «транспорт» + оркестратор. Запускается на офлайн-машине (приватные ключи ридеров не покидают этот хост).

---

## 9. Протокол (shared) — байты, опкоды, крипто

### 9.1 Опкоды

**Wire (APDU, NFC):** `0xA4` SELECT AID · `0xC1` PUSH_INFO · `0xC2` FETCH · `0xC3` READ_CHUNK · `0xC4` PUSH_CHUNK · `0xC5` END.

**Inner (операции):** `0x01` ACCESS · `0x11` FDI · `0x12` TIME_SYNC · `0x13` FILTER_UPDATE · `0x14` GET_BLACKLIST · `0x15` REVOKE_KEY · `0x16` GET_PASSAGE_RECEIPT.

**Result-маркеры:** `0x81` ACCESS_VERDICT · `0x91` FDI · `0x92` TSYNC_RESULT · `0x93` FLT_RESULT · `0x94` BLK · `0x95` REV_RESULT · `0x96` PASSAGE_ENVELOPE · `0x97` PASSAGE_NONE.

**Коды вердикта ACCESS:** `0x00` OK · `0x20` EXPIRED · `0x21` REVOKED_BLACKLIST · `0x22` REVOKED_FILTER · `0x01` BAD_SIGNATURE · `0x02` BAD_FORMAT · `0x03` BAD_NONCE · `0x07` WRONG_READER.

### 9.2 Домены подписи (Ed25519, 16-байтовый префикс `RDR-XXX-v1` + нули)

| Домен | Подписывает | Чем |
|---|---|---|
| `RDR-KEY-v1` | issued_key | server_priv |
| `RDR-INF-v1` | INFO-конверт ридера | reader_priv |
| `RDR-RSP-v1` | ответ телефона в ACCESS | phone_priv |
| `RDR-FLT-v1` | filter_package | server_priv |
| `RDR-RCP-v1` | delivery_receipt | reader_priv |
| `RDR-BLK-v1` | blacklist-конверт | reader_priv |
| `RDR-FDI-v1` | FDI-конверт | reader_priv |
| `RDR-TGR-v1` | time_authority_grant | server_priv |
| `RDR-TIM-v1` | time_sync_statement | phone (authority) |
| `RDR-REV-v1` | revoke_key | phone_priv |
| `RDR-PSG-v1` | passage_receipt | reader_priv |
| `RDR-BLE-v1` | BLE session token | reader_priv |

### 9.3 Крипто-примитивы

- **Ed25519** — все подписи (с доменным разделением, что блокирует cross-protocol-атаки).
- **X25519 + ChaCha20-Poly1305-IETF (sealed box)** — шифрование «ридер → сервер» (FDI, BLK).
- **BLAKE2s** — `key_id`.
- **Argon2id** — хеш паролей пользователей.
- **MurmurHash3** — bloom-фильтр.
- **SHA256** — хеш API-ключей; **HMAC-SHA256** — подпись webhook-payload.

### 9.4 Размеры структур

issued_key **151 B** · time_authority_grant **148 B** · INFO **146 B** · delivery_receipt **112 B** · passage_receipt **192 B** · FDI **241 B** · filter_package — переменный (bloom размерится **per-reader** под популяцию отзывов конкретного ридера, FP-rate 0.001, с конфигурируемым потолком `filter_max_bloom_bytes` ~100 КБ; §3.4).

---

## 10. Ключевые процессы

### 10.1 Онбординг сотрудника
Админ создаёт User → сотрудник логинится в app → app генерирует Ed25519-пару в Keystore и регистрирует pubkey (`register-device`) → ждёт выдачи permit.

### 10.2 Provisioning ридера
См. §8. Одноразовая процедура на офлайн-машине; приватные ключи ридера остаются в NVS, серверные для этого ридера — только в БД.

### 10.3 Выдача permit и запрос ключа
Админ выдаёт Permit (вручную или по шаблону day/week/month/quarter/year/dual_device) → пользователь видит permit и делает `POST /keys/request` → сервер проверяет принадлежность, `n_parallel`, окно времени, `max_token_ttl`, берёт следующий `serial`, подписывает 151-B ключ → телефон сохраняет в Room.

### 10.4 Проход (NFC)
Reader делает SELECT AID → PUSH_INFO (fresh_nonce) → телефон строит очередь операций (TapDecisionTree) → цикл FETCH → на `ACCESS=OK` ридер открывает замок и кэширует квитанцию → телефон забирает `GET_PASSAGE_RECEIPT` → END. Накопленные отчёты позже уходят `POST /reports/submit`.

### 10.5 Учёт проходов (PASSAGE_RECEIPT)
Только владелец ключа может получить квитанцию (без ACCESS=OK кэш пуст) и доставить её. Backend (worker): verify reader-подписи → sanity-check `passed_at` → dedup `(reader_id, receipt_nonce)` → резолв `user_id` через permit → INSERT `passage_events` → webhook fan-out.

### 10.6 Отзыв доступа (три пути)
- **A. Phone-инициированный** (`REVOKE_KEY`): pending-intent → на тапе ключ ложится в local_blacklist ридера → BLK-отчёт → backend: `active → revoked_by_reader`.
- **B. Server-инициированный** (admin revoke key): `active → revoked_by_server`, `committed_filter_version = next`, enqueue `generate_filter` → ридер применяет фильтр → `delivery_receipt` → `revoked_by_server → revoked_in_bloom`.
- **C. Admin revoke permit (двухфазный)**: `revoke_initiated_at = now` (статус `revoking`), все ключи → `revoked_by_server` → после применения фильтра на ридере → `revoked_at = now` (статус `revoked`).

### 10.7 Webhook fan-out
`INSERT passage_events` → выбрать активные подписки → `notify_webhook` task → HTTP POST на `url` (+ `X-SCUD-Signature` HMAC, если есть secret). 2xx → reset счётчика; после ≥10 подряд неудач подписка деактивируется.

---

## 11. Жизненные циклы (state machines)

**IssuedKey:**
```
active ──(revoke-on-server / admin revoke permit)──→ revoked_by_server ──┐
   │                                                                      ├─→ revoked_in_bloom (терминальное)
   └──(приход BLK)──→ revoked_by_reader ─────────────────────────────────┘
   └──(now > expires_at, periodic)──→ expired
```
`is_active` (derived) = `true` для `active`/`revoked_by_server` (ридер ещё принимает ключ до доставки нового фильтра — влияет на счётчик `n_parallel`).

**Permit (двухфазный revoke):** `active → revoking (revoke_initiated_at) → revoked (revoked_at, когда ключи is_active=false)`.

**DeliveryTask:** `created → completed (receipt / superseded)`; навсегда открытая задача = ридер давно офлайн (повод для алерта).

---

## 12. Инварианты безопасности

| # | Инвариант |
|---|---|
| I1 | `reader.priv` никогда не покидает NVS (генерируется *в* ESP32) |
| I2 | `phone.priv` никогда не покидает Android Keystore |
| I3 | `server.priv` для каждого ридера — только в БД сервера |
| I4 | ACCESS не пройдёт без `phone.priv` (подпись включает `fresh_nonce` → нет replay) |
| I5 | `passage_receipt` нельзя подделать вне ридера (подпись `reader.priv`) |
| I6 | `filter_package` нельзя подделать вне сервера (подпись `server.priv`) |
| I7 | Двойной проход одной квитанцией не учтётся (dedup `(reader_id, receipt_nonce)`) |
| I8 | Replay INFO/ACCESS между ридерами невозможен (`reader_id` в домене + одноразовый nonce) |
| I9 | Истёкший ключ не пройдёт (ридер проверяет `expires_at` по RTC до крипто) |
| I10 | Cross-domain подпись невозможна (разные `domain_tag` для разных типов) |
| I11 | Замена ридера не воссоздаёт identity (`reader_id` + `reader_pubkey` должны совпасть с БД) |
| I12 | Admin web-сессии без root-доступа (cookie с TTL 8 ч; revoke API-key инвалидирует мгновенно) |

### Time-sync политика
| Случай | Триггер | Право | Окно дрейфа |
|---|---|---|---|
| Bootstrap | `last_sync == 0` | `soft` или `hard` | soft: ±`ts_boot` (деф 24ч); hard: любое |
| Soft sync | drift > 15 с, есть `soft` | OK | `± (ts_drift × дней)` сек (деф 10 с/день) |
| Hard sync | любой drift, есть `hard` | OK | без проверки |
| Refusal | `now > grant.expires_at` | NOT_AUTHORIZED | — |

---

## 13. Развёртывание

- **Dev:** `cd Backend && cp .env.example .env && docker compose up -d` (postgres → migrate → app + worker). Проверка: `/health`.
- **Prod:** overlay `docker-compose.prod.yml` — 2 реплики app (uvicorn `--workers 4`), postgres не публикуется наружу, nginx side-car (TLS + rate-limit `/auth` 10 req/min, общий 120 req/min), resource limits.
- **Бэкап:** `pg_dump -Fc`. **Миграции:** `docker compose run --rm migrate alembic upgrade head`.
- **Наблюдаемость:** `/health` + Docker healthcheck; структурные логи; `/metrics` (Prometheus): RPS/latency-гистограммы, gauges (active users/readers/permits/keys, passages_total, webhooks active/failing).
- **Интеграции:** CSV-выгрузка проходов (pull), webhooks (push), задел под LDAP/AD-синк.
- **Безопасность:** TLS (nginx + LE), Bearer/X-Api-Key, `.env` через docker secrets, provisioning на отдельной офлайн-машине.

---

## 14. Ограничения и компромиссы

- **Окно отзыва:** деактивированный сотрудник проходит, пока на ридер не доставится новый фильтр (время реакции = до следующего тапа любого курьера). Mitigation: курьером может быть любой сотрудник.
- **BLE-link не шифруется** — полагается на E2E Ed25519/X25519 (сознательный компромисс ради UX без bonding; eavesdropper видит байты, но не подделает).
- **Приватность учёта:** пользователь может не доставить passage_receipt — это его право, но и его проблема (нет учёта).
- **Масштаб:** bloom размерится **per-reader** под число отзывов ридера с потолком `filter_max_bloom_bytes` (~100 КБ; выше потолка FP-rate растёт мягко, whitelist поглощает FP); PostgreSQL — одна нода (passage_events ~200 B/строка → ~20 ГБ/год при 1 млн проходов). Дальше — delta-only / пер-группа фильтры, партиционирование по `passed_at`.
- **RTC-дрейф:** до ~2 мин/месяц без sync; при активном использовании sync приходит на каждом тапе.

---

## 15. Карта кода (где что искать)

| Тема | Путь |
|---|---|
| Байтовые форматы, опкоды, домены | [`00_shared_protocol`](00_shared_protocol%20%281%29.md) |
| Бизнес-логика (карта) | [`05_business_logic.md`](05_business_logic.md) |
| API-рецепты (curl) | [`06_api_cheatsheet.md`](06_api_cheatsheet.md) |
| Golden conformance-векторы (3 impl, CI) | [`test_vectors/`](test_vectors/) (`generate.py` → `protocol_v1.json`) |
| Трекер ремедиации транспорта | [`transport_progress.md`](transport_progress.md) |
| ORM-модели / миграции | `Backend/src/scud/db/models.py`, `Backend/migrations/versions/000*.py` |
| Crypto / парсеры | `Backend/src/scud/crypto/{signing,serialization,sealed_box,bloom,key_id}.py` |
| Выпуск ключей / permits / гранты | `Backend/src/scud/domain/{keys,permits,grants}.py` |
| Обработка отчётов / фильтры | `Backend/src/scud/domain/{reports,filters}.py` |
| Admin / App API | `Backend/src/scud/api/{admin,app,admin_web}/*.py` |
| Firmware: операции / транспорт / state / BLE | `ESP32/firmware/src/{ops,transport,state,ble}/*.cpp` |
| Android: HCE / BLE / репозитории / крипто | `AndroidApp/app/src/main/java/com/vkrauth/app/{hce,ble,data/repository,data/crypto}/*.kt` |
| Provisioning-утилита | `Desktop/ScudProvisioner/` |

---

## 16. Глоссарий

**AID** — Application ID для NFC SELECT. **APDU** — единица обмена ISO 7816-4. **Bloom filter** — вероятностная структура «вероятно содержит». **Bootstrap** — первичная синхронизация RTC через hard-grant. **Committed filter version** — версия фильтра, в которой ключ гарантированно учтён. **Courier** — телефон, переносящий filter_package. **Data-mule** — телефон как курьер данных между сервером и офлайн-ридером. **Domain tag** — 16-байтовый префикс подписи против cross-protocol-атак. **HCE** — Host Card Emulation (эмуляция NFC-карты на Android). **NVS** — Non-Volatile Storage ESP32. **Permit** — логическое право доступа (≠ key — физический подписанный блоб). **Sealed box** — шифрование с эфемерным X25519-ключом. **Tap session** — один цикл NFC-обмена. **Time grant** — право телефона переподписывать время ридеру.
