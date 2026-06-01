# SCUD — Архитектурный аудит и план приведения к качественному состоянию

> Дата: 2026-05-29 · Ветка: `feature/attendance-and-ble-channel`
> Метод: независимое чтение кода + мульти-агентный аудит (12 рецензентов по областям) с
> состязательной верификацией каждой находки отдельным «скептиком».
> Из 134 сырых находок подтверждено **123**, отклонено (опровергнуто на коде) **11**.
> Распределение: **5 critical · 25 high · 54 medium · 39 low**.

ID находок (напр. `BE-SEC-02`, `FW-ARC-01`) сквозные — их можно искать по этому документу.

> ### ✅ Статус закрытия (обновлено 2026-05-31, ветка `feature/transport-hardening`)
> Закрыто доказательно сверх транспортного слоя (см. `docs/transport_progress.md` — там весь
> transport-тир: REPO-H, TESTIN-01/02/05, B1-B10, N4-N6, FW-ARC-01, X4-корпус):
> - **BE-SEC-02** ✅ — admin-API требует `kind=='admin'` (`acc0d9b`).
> - **BE-SEC-04** ✅ — fail-fast на дефолтном `SCUD_WEB_SECRET` в проде (`acc0d9b`).
> - **BE-DAT-01** ✅ — seed-миграция 0002 за `SCUD_SEED_DEV` (вне прод-цепочки) (`acc0d9b`).
> - **TESTIN-04** ✅ — тесты two-phase revoke + delivery-receipt (`acc0d9b`).
> - **ANDROID-01** ✅ — Room-миграция v1→v2 вместо destructive + exportSchema (`7a46373`).
> - **ANDROID-05** ✅ — network-security-config, cleartext только dev-хосты (`7a46373`).
> - **ANDROID-06** ✅ — `allowBackup=false` + DB исключена из backup (`7a46373`).
> - **FW-ARC-03** ✅ — verify server-подписи фильтра на буте (streaming) (`976c99b`).
> - **FW-ARC-06** ✅ — blacklist full → evict-expired-then-fail-closed (`976c99b`).
> - **CRYPTO-04** ✅ — спека приведена к firmware (10 с/день + soft-bootstrap) (`a79bbe0`).
> - **CRYPTO-05** 🔧 — §2.4 честно описывает кастомную схему; KDF-«блендер» — задача наименьшего приоритета.
> - **Параметризация** ([docs/11](11_reader_config_provisioning.md)) — 21+2 параметра ридера → NVS-provisioning (Phase 1/1b) + backend config-колонка (Phase 3).
>
> **Закрыто compile-only на ветке `feature/transport-compile-only`** (host-proven, рантайм
> на реальном ESP32 + телефоне ещё не верифицирован; см. `transport_progress.md`):
> - **B4** ✅ — op↔result-корреляция по BLE через 1-байтовый `op_seq`-префикс (§16.5.1/§16.6).
> - **X2** ✅ — adv-бит `BLE_CAP_BULK` (0x01) + чистый `chooseTransport`/`TransportRouter` (bulk→BLE iff caps).
> - **N3** ✅ — bounded per-chunk retry на NFC READ_CHUNK/PUSH_CHUNK (`READ_CHUNK_RETRIES`=3, deadline-bounded).
> - **N2/B6** ✅ — flash `op_sink` снял 16-КБ потолок приёма (стрим в неактивный A/B-слот + two-pass verify-from-flash); SPIFFS/NimBLE-flash I/O — hardware-only.
> - **handover** ✅ — NFC→BLE `handover_token` (167 B, marker 0x99, reader-sig над `DOMAIN_BLE‖bytes[0:103]`, §17.1); two-radio rendezvous — hardware-only.
> - **X3** ✅ — ACCESS=NFC transport-policy (§16.8 переписан relay≠replay; ридер отвергает proximity-ops на BLE).
> - **X1** 🟨 — per-op dispatch унифицирован (`dispatch_op`); framing-FSM + L2CAP-адаптер отложены (hardware-only).
> - **desktop Phase 4** ✅ — config-template library + editor + полная `SET-*` серия (25 CFG + lock_duration + ble_enabled); dotnet build 0 ошибок; backend DB-mirror через enroll отложен в Phase 3.
>
> **Ещё открыто:** TESTIN-03 (postgres-CI); рантайм-верификация транспорта на железе
> (B4/X2/N3/N2-B6/handover/X3); прочие non-blocker low/medium из разделов ниже.

---

## 0. Вердикт

Архитектура **зрелая и продуманная** для дипломного проекта: реальная офлайн-модель с
data-mule, доменное разделение подписи (12 доменов), грамотная слоистость на `/api/v1/app`,
корректно реализованный объектный контроль доступа (классический IDOR через `/permits/{id}`
**отсутствует** — проверено), well-indexed схема с partial-индексами, двухфазный отзыв.
Это сильная основа.

Однако проект **не готов к production** и имеет несколько системных дефектов, два из которых
ставят под сомнение даже целостность репозитория. Проблемы кластеризуются в 8 сквозных тем
(раздел 5). Ни одна не требует переписывания — это устранимые, конкретные правки.

**Что является блокером прямо сейчас (release-blockers):**

| Тема | Находки |
|---|---|
| 2 из 4 компонентов фактически не в git | `REPO-H-01` (ESP32 — висячий submodule) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; ESP32 вшит в монорепо), `REPO-H-02` (Desktop не закоммичен вообще) ✅ **Решено** (Desktop закоммичен; на feature/transport-compile-only — Phase-4 config-template library + editor + полная `SET-*` серия, compile-only; см. docs/11) |
| Привилегии: integration-ключ = полный admin | `BE-SEC-02` |
| Сид-миграция вливает `admin123` + bootstrap-ключ в прод-цепочку | `BE-DAT-01` |
| Heap-overflow в прошивке (READ_CHUNK) | `FW-ARC-01` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; кламп chunk_len по остатку ёмкости) |
| Android стирает офлайн-ключи при любом bump схемы | `ANDROID-01` |
| Webhooks падают в проде (нет `httpx` в образе) | `OBSERV-02` |
| Нет кросс-реализационных conformance-векторов протокола | `TESTIN-01` / `CRYPTO-02` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; golden-векторы во всех 3 impl + CI) |

---

## 1. P0 — Критические (исправить до всего остального)

### 1.1 `BE-SEC-02` — integration-API-ключ имеет полные права admin
`Backend/src/scud/api/deps.py:50-80`
`get_api_key` (защищает **все** `/api/v1/admin/*`) проверяет только существование/неотозванность/срок,
но **не проверяет `api_key.kind`**. При этом два kind ('admin' / 'integration') существуют, и web-панель
их различает (`admin_web/auth.py:49-51`). Значит integration-ключ, выданный «для интеграции/вебхуков»,
может создавать пользователей, сбрасывать любой пароль (`admin/users.py:128`), энроллить ридеры.
**Fix (S/M):** ввести `AdminApiKey` с проверкой `kind=='admin'` и отдельный `IntegrationApiKey` для
read-only/webhook-маршрутов; либо колонка `scopes`. Минимум — закрыть мутации users/api-keys/readers.

### 1.2 `BE-DAT-01` — сид-миграция 0002 вливает известный пароль в прод-цепочку
`Backend/migrations/versions/0002_seed_test_data.py:32-77`, `docker-compose.yml`
`0002_seed_test_data` — обычный узел линейной Alembic-цепочки; `alembic upgrade head` применяет его
**в любом окружении, включая прод**. Создаётся пользователь `admin` с паролем `admin123` (хэш в репо)
и API-ключ `sk_admin_bootstrap_CHANGE_ME...` с `kind=admin`. Гейта по окружению нет.
**Fix (S):** вынести bootstrap в отдельную явную команду (`python -m scud.bootstrap`), запускаемую
по env-флагу, генерирующую случайный пароль/ключ и печатающую его один раз; убрать сид из цепочки
миграций.

### 1.3 `FW-ARC-01` — heap buffer overflow в приёме чанков (READ_CHUNK)
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; `chunk_len <= остаток ёмкости op_buf`)
`ESP32/firmware/src/transport/transfer.cpp:209-231, 393, 403-413`
`op_buf` выделяется ровно на `total_len`. В цикле приёма `send_read_chunk(...)` копирует в `op_buf+offset`
`chunk_len`, читаемый из ответа телефона; проверяется только `chunk_len <= принятые байты` (~250),
но **не** `chunk_len <= (cap - offset)`. Телефон (или MITM на NFC/BLE) может переполнить кучу записью
за границей буфера — **запись произвольных данных за `op_buf`** на устройстве, принимающем решение о
доступе. Это критическая memory-safety уязвимость.
**Fix (S):** передавать остаток ёмкости в `send_read_chunk` и отклонять/клампить
`chunk_len <= op_buf_cap - offset` до `memcpy`; требовать монотонный прогресс offset.

### 1.4 `ANDROID-01` — `fallbackToDestructiveMigration()` стирает офлайн-крипто при любом bump
`AndroidApp/.../di/DatabaseModule.kt:30-31`, `data/local/ScudDatabase.kt:36`
Room (version 2) собран с `.fallbackToDestructiveMigration()` и **нулём** Migration-объектов. Любой
будущий bump версии **дропнет все таблицы**: `issued_keys` (единственная локальная копия 151-B
подписанных ключей), `pending_revoke_intents`, `outgoing_reports` (недоставленные квитанции/отчёты).
Для offline-first приложения, где телефон — единственный носитель данных, это потеря данных.
**Fix (M):** убрать destructive fallback, добавить явные Migration-объекты (v1→v2 — тривиальный
`ALTER TABLE`), `exportSchema=true`, схему в VCS.

### 1.5 `TESTIN-01` / `CRYPTO-02` — нет кросс-реализационных golden-векторов
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; `docs/test_vectors/protocol_v1.json` + conformance-тесты во всех 3 impl + CI-гейтинг)
`Backend/tests/test_serialization.py`, `AndroidApp/.../SerializationTest.kt`, `ESP32/firmware/src/crypto/*`
Главный инвариант системы — один байт-точный протокол реализован **трижды вручную** (Python/Kotlin/C++).
Общих golden-векторов нет: каждая реализация тестирует себя против собственных байтов. Ed25519
падает на одном бите — расхождение любого из трёх не будет поймано до полевого сбоя.
**Fix (L, но наивысший рычаг):** один язык-нейтральный корпус векторов (`docs/test_vectors/*.json`) с
детерминированными входами и ожидаемыми точными байтами для каждой структуры/домена/bloom/key_id/
sealed-box; в каждой из трёх реализаций — conformance-тест, читающий **тот же** корпус. См. раздел 5.2.

---

## 2. P1 — Высокий приоритет (25)

### 2.1 Учётные данные и секреты
- **`BE-SEC-01`** — session/refresh-токены хранятся в БД **в открытом виде** (`models.py:110-111`,
  `deps.py:27-34`), тогда как API-ключи — SHA256. Утечка дампа/реплики = угон всех живых сессий.
  *Fix (M):* хранить только `sha256`/HMAC токена, искать по хэшу.
- **`BE-SEC-04`** — `SCUD_WEB_SECRET` читается через сырой `os.getenv` с дефолтом
  `'dev-only-secret-CHANGE-IN-PROD'` (`admin_web/config.py:22`); при незаданной env приложение молча
  стартует с публично известным секретом → подделка admin-cookie. *Fix (S):* fail-fast в non-dev.
- **`BE-LAY-05`** — `config.py` без валидаторов, с небезопасными дефолтами (`scud:password`), и самый
  чувствительный секрет вне `Settings`. *Fix (S):* перенести `web_secret` в `Settings`, добавить
  `model_validator`, проверку `+asyncpg`, диапазоны.
- **`BE-SEC-03`** — нет CSRF-защиты на cookie-аутентифицированных `POST /admin/*` (`SameSite=Lax`
  не заменяет CSRF-токен). *Fix (M):* CSRF-токен + проверка Origin/Referer + `SameSite=Strict`.
- **`BE-SEC-05`** — plaintext нового API-ключа уходит в `Location: ...?flash_plaintext=...` и
  логируется nginx (`access_log $request`) + история браузера. *Fix (S):* отдавать через server-side
  flash в подписанной сессии, не в query-string; не логировать.

### 2.2 Безопасность Android-клиента
- **`ANDROID-05`** — `usesCleartextTraffic="true"` глобально + доверие user-installed CA + нет
  certificate pinning; `Bearer` уходит в открытом HTTP. *Fix (M):* HTTPS по умолчанию, cleartext только
  для явного dev-домена, pinning (self-hosted — pin задаётся при энролле).
- **`ANDROID-06`** — `allowBackup=true` с пустыми правилами → Room-БД (подписанные ключи, очереди) и
  EncryptedSharedPreferences уходят в облачный бэкап; мастер-ключ device-bound → восстановление на новом
  устройстве = крах. *Fix (S):* `allowBackup=false` или явные `<exclude>`.
- **`ANDROID-03`** — Ed25519-ключ телефона генерится в софте (BouncyCastle) и AES-оборачивается, а не
  лежит несекретируемым в AndroidKeyStore (StrongBox/TEE); при каждой подписи приватные байты
  материализуются в куче. *Fix (L):* несекретируемый ключ в Keystore + key attestation при энролле.
- **`ANDROID-02`** — блокирующий `runBlocking { dao... }` на NFC binder-потоке под `synchronized(mutex)`
  (`hce/TapController.kt:94,226,263-275,379`). Тайминг-критичный путь делает синхронный SQLite-IO.
  *Fix (L):* предзагружать reader/ключ в память на SELECT_AID; не держать mutex через DB.

### 2.3 Worker и очередь задач
- **`BE-ASY-01`** — claim в одной транзакции, обработка — в другой; `FOR UPDATE SKIP LOCKED`-лок
  отпускается **до** работы. Креш воркера оставляет задачу в `processing` навсегда (нет reclaim).
  *Fix (M):* claim+process+complete в одной транзакции, либо lease + reclaim stale `processing`.
- **`BE-ASY-02` / `OBSERV-04`** — заявленный «retry/backoff» **не существует**: `mark_task_failed`
  ставит терминальный `failed`, `retry_count` лишь инкрементируется, переочередь отсутствует.
  *Fix (M):* реальный backoff (`status='pending'`, `scheduled_at=now+backoff`) + dead-letter + cap.

### 2.4 Крипто / прошивка
- **`CRYPTO-01`** — все критичные случайные значения на ридере (`fresh_nonce`, `receipt_nonce`,
  эфемерный X25519-ключ sealed-box) берутся из `esp_random()` без энтропийного bootstrap. Без активного
  RF `esp_random` не TRNG → риск повтора эфемерного ключа = катастрофический nonce-reuse ChaCha20.
  *Fix (M):* `bootloader_random_enable()` в начале `setup()` / CTR-DRBG; задокументировать требование.
- **`FW-ARC-04`** — нет watchdog; `run_tap_session()` — `while(true)` с блокирующими `apdu_exchange`
  без дедлайна сессии. Зависший телефон/PN532 вешает ридер. *Fix (M):* per-session deadline + `esp_task_wdt`.
- **`CRYPTO-04`** *(см. также раздел 5)* — окно SOFT time-sync: спека §10 = `5*days`, прошивка =
  `10*days` (`time_sync.cpp:105`) + недокументированный 24h-bootstrap. Расхождение в логике, гейтящей
  доступ. *Fix (S):* согласовать спеку и прошивку, закрепить unit-тестом/вектором.
  ✅ **Решено** (по решению: канон = прошивка). §10 и `PROJECT_OVERVIEW` приведены к firmware
  (10 с/день + soft-bootstrap 24ч); оба значения теперь **provisioned** (`ts_drift`/`ts_boot`, Phase 1).

### 2.5 Provisioning
- **`DESKTO-01`** — энролл (создаёт Reader + серверную пару в БД) выполняется **до** записи на
  устройство и COMMIT; при сбое после энролла в БД остаётся «ghost»-ридер, ретрай не идемпотентен →
  накопление осиротевших ридеров. *Fix (M):* device-first или two-phase с компенсирующим
  `DELETE /admin/readers/{id}` в catch; идемпотентный `reader_id`.

### 2.6 Тестирование
- **`TESTIN-02`** — у прошивки **ноль тестов** и нет host-окружения, хотя там вся access-логика и третья
  реализация крипто; CI только компилирует. *Fix (L):* PlatformIO `[env:native]` + Unity, тесты на
  pure-модули, потребляющие golden-векторы.
  ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; **частично**: host-тест (gcc/MSVC) + CI-job есть, `[env:native]`+Unity — follow-up)
- **`TESTIN-03`** — весь backend-suite на `sqlite+aiosqlite`; `FOR UPDATE SKIP LOCKED`, JSONB, row-locks
  **никогда не исполняются** в CI. *Fix (M):* Postgres-тир (services: postgres / testcontainers) с
  marked-подмножеством: конкурентный `/keys/request` vs `n_parallel`, двойной claim() задачи.
- **`TESTIN-04`** — двухфазный отзыв permit и worker-handlers без юнит-тестов. *Fix (M).*
  ✅ **Решено частично** (ветка feature/transport-hardening — см. docs/transport_progress.md; закрыта **только генерация фильтра** — `Backend/tests/test_filters.py`; двухфазный отзыв permit / worker-handlers — всё ещё открыто)
- **`TESTIN-05`** — Android CI **не запускает** даже имеющиеся unit-тесты; реальные тесты — два IDE-стаба.
  *Fix (M):* `./gradlew testDebugUnitTest` как gating-шаг; тесты на HCE state-machine и TapDecisionTree.
  ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; CI запускает `testDebugUnitTest`)

### 2.7 Репозиторий и сборка
- **`REPO-H-01`** — `ESP32` записан как gitlink (mode 160000, commit `7fd365f…`) **без `.gitmodules`**;
  remote = `github.com/gonid0/ESP32.git`, pinned-commit нерезолвится в outer-репо → свежий клон даёт
  **пустой** `ESP32/`. *Fix (M):* решить явно — настоящий submodule (`.gitmodules`+push pinned commit+
  `--recurse-submodules` в CI) **или** вшить в монорепо (`git rm --cached ESP32`, удалить `ESP32/.git`,
  `git add ESP32/`).
  ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; вшит в монорепо, коммит `424fd2b`)
- **`REPO-H-02`** — **вся** `Desktop/ScudProvisioner` (компонент №4) — `?? Desktop/`, не закоммичена.
  *Fix (S):* закоммитить исходники (bin/obj уже игнорятся вложенным `.gitignore`).
  ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; Desktop закоммичен)
- **`REPO-H-05`** — `firmware.yml` делает `checkout@v4` без `submodules: recursive` → при submodule-схеме
  собирает пустоту. *Fix (S):* после REPO-H-01 добавить `with: submodules: recursive` или smoke-проверку.
  ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; firmware vendored + CI-checkout/conformance-job)

### 2.8 Прочее P1
- **`OBSERV-02`** — `notify_webhook` импортирует `httpx`, но он только в `dev`-зависимостях; прод-образ
  (`pip wheel --no-deps`) **без httpx** → каждая доставка вебхука = `ModuleNotFoundError`. *Fix (S):*
  перенести `httpx` в `[project].dependencies` + CI smoke `import scud.worker; _load_handlers()`.
- **`DOCS-S-08`** — протокол только в прозе + три ручных сериализатора, без машинного описания/векторов
  (сейчас совпадают, но держатся на дисциплине). *Fix (L):* IDL/схема структур → codegen или хотя бы
  golden-корпус (см. 5.2).

---

## 3. P2 — Средний приоритет (54) — по темам

> Полный список с локациями — в машинном выводе аудита; ниже самое значимое.

**Слоистость backend**
- `BE-LAY-01` — `admin_web` обходит domain/repository: сырой ORM + бизнес-логика прямо во вьюхах
  (bulk-permits, attendance ~100 строк аналитики). Теневое мини-приложение.
- `BE-LAY-02` — нет иерархии исключений: домен бросает `ValueError("ttl_too_long:...")`, роутеры
  парсят строки через `split(":")`. *Fix:* `DomainError(NotFound/Conflict/Validation)` + один
  exception-handler + стандартный конверт ошибки.
- `BE-LAY-03` — транзакции размазаны по роутерам (`await db.commit()` вручную в ~30 хендлерах),
  `get_session` не делает rollback. *Fix:* unit-of-work в `get_session` (commit/rollback).
- `BE-LAY-04/06/08/09` — дублированный admin-auth (`admin_web` переписывает `get_api_key`), сырой
  ORM-update в admin-роутерах, copy-paste `_accessible_group_ids` (N+1), copy-paste `_audit` в 6 местах.
- `BE-LAY-07` / `OBSERV-14` — debounce-функция глушит реальные ошибки БД через `except Exception`
  (fallback на SQLite-путь) + TOCTOU. *Fix:* ветвление по `dialect.name`, partial-unique-индекс.

**Async/конкуренция**
- `BE-ASY-03` — `process_report` пишет `processing_error` и `raise` в той же транзакции → запись
  откатывается (write-then-rollback).
- `BE-ASY-04` — генерация фильтра без per-reader lock → гонка на `filter_version` PK / двух `is_current`.
  *Fix:* `pg_advisory_xact_lock(reader_id)` или sequence + partial-unique `WHERE is_current`.
- `BE-ASY-05` — «debounce» — только по имени; `filter_generation_debounce_seconds` — мёртвый конфиг;
  задача в `processing` обходит дедуп → повторные генерации.
- `BE-ASY-06` — пул БД без `pool_size/max_overflow/pool_timeout` при `--workers` + воркер → риск
  исчерпания. `BE-ASY-08` — вебхуки доставляются строго последовательно (10s timeout) → один мёртвый
  подписчик блокирует security-critical `generate_filter`.

**Безопасность backend**
- `BE-SEC-06` — нет app-level rate-limit (всё на опциональном nginx); `/admin/login` не в auth-зоне.
- `BE-SEC-07` / `OBSERV-12` — `/metrics`, `/docs`, `/redoc` без auth, и nginx-конфиг их **не** закрывает
  (нет `location`-блока — всё падает в `location /`). `/metrics` ещё и делает 7 COUNT-запросов на скрейп.
- `BE-SEC-09` — admin-cookie всегда `Secure=False`. `BE-SEC-10` — audit-log не tamper-evident и
  безусловно чистится через 365 дней; нет записи событий логина. `BE-SEC-12` — нет reuse-detection
  у refresh-ротации (украденный refresh не инвалидирует семью токенов).

**Модель данных**
- `BE-DAT-02` — нет партиционирования/ретеншена `passage_events` при заявленных ~20 ГБ/год.
  *Fix:* `PARTITION BY RANGE(passed_at)` помесячно + дроп старых партиций в `cleanup`.
- `BE-DAT-03` — дедуп квитанций через check-then-insert (TOCTOU), без `ON CONFLICT DO NOTHING`.
- `BE-DAT-04` — `issued_keys.is_active` имеет **три** источника истины (PG-триггер + Python-присваивания
  + ORM-default) → дрейф, особенно на SQLite (нет триггера) в тестах. *Fix:* `GENERATED ALWAYS … STORED`
  либо единственный писатель (триггер), убрать Python-присваивания.
- `BE-DAT-07` — статусы-строки без ENUM/CHECK (`background_tasks.status`, `api_keys.kind`,
  `completion_source`, `actor_type`); `event_types` — CSV-строка. `BE-DAT-08` — `retry_count` не
  используется (дубль `OBSERV-04`).

**Прошивка**
- `FW-ARC-03` — фильтр перечитывается из flash **без проверки подписи/CRC** на буте (подпись проверяется
  только при FILTER_UPDATE); порча/подмена слота → неверные решения, нет fail-closed. *Fix:* хранить
  подпись пакета в слоте и реверифицировать на загрузке, иначе — fail-closed (пустой фильтр/отказ).
- `FW-ARC-06` — переполнение 256-слотного blacklist = **fail-OPEN**: `REVOKE_KEY` → `NO_SLOT`, ключ не
  заблокирован, но проход состоялся. *Fix:* LRU-by-expiry эвикция + эскалация (флаг в INFO/FDI).
- `FW-ARC-07` — кольцо nonce всего 8 RAM-слотов, эвикция «oldest» может выбросить ещё не использованный
  nonce → ложный `BAD_NONCE`. `FW-ARC-08` — BLE inbound/result — статические синглтоны, не reentrant,
  второй central интерливит коллбеки. *Fix:* один активный коннект + единый dispatch NFC/BLE.

**Android**
- `ANDROID-04` — у исходящих отчётов нет cap/backoff: `retryCount` инкрементируется, но не читается;
  permanently-rejected отчёт пересылается вечно и блокирует очередь. *Fix:* `MAX_RETRIES`+dead-letter+
  WorkManager.
- `ANDROID-08` — `AuthInterceptor` делает `runBlocking` рефреша на OkHttp-потоке. *Fix:* `Authenticator`.
- `ANDROID-09` — дубли pending-revoke-intent (autoGenerate PK, нет unique) → ридер получает отзыв дважды.

**Provisioning**
- `DESKTO-02` — ответы `SET-*` не валидируются; частичный/мусорный конфиг можно закоммитить. *Fix:*
  проверять `OK`/`ERR` на каждый SET + `STATUS` read-back после COMMIT.
- `DESKTO-05` — MAUI/WinUI несоразмерно тяжёл для serial+HTTP-оркестратора; в репо уже есть
  `ESP32/firmware/tools/provisioner.py` (110 строк, тот же flow). *Fix:* свести к одной тестируемой
  реализации (CLI или тонкий WPF).
- `DESKTO-06` — admin-API-ключ в `Preferences` (незашифровано на Windows). *Fix:* `SecureStorage`.

**Репозиторий**
- `REPO-H-03/04` — 55 `.pyc` в git, нет root/Backend `.gitignore`. `REPO-H-07` — нет связи версий
  компонентов с версией протокола (`-v1`); нет CHANGELOG/тегов. `REPO-H-09` — Python-зависимости только
  с `>=`, без lock-файла; `psycopg2-binary/asyncpg` без версии. `REPO-H-12` — нет root README/Make/justfile.

**Тестирование/Ops/Docs**
- `TESTIN-06` — у Desktop нет тестов и CI-воркфлоу. `TESTIN-08` — session-scoped БД с permanent commit →
  cross-test coupling (random-суффиксы как workaround). `TESTIN-09` — e2e заканчиваются на границе сервера
  (нет реального round-trip server→phone→reader→server).
- `OBSERV-01` — метрики per-process in-memory; при `--workers 4` скрейп возвращает срез одного процесса.
  *Fix:* `prometheus_client` multiproc. `OBSERV-05` — логи не структурированы (вопреки 04). `OBSERV-08` —
  нет автобэкапа/PITR/реплики (single-node Postgres). `OBSERV-09` — нет алертинга/агрегации логов.
  `OBSERV-10` — `.env.example` дублирует пароль в `DATABASE_URL`; secrets через env, не docker secrets
  (вопреки доке).
- `DOCS-S-01` — `openapi.yaml` — рукописная теневая спека (version `1.0.0` vs `0.1.0` приложения), не
  сверяется с реальными роутами; FastAPI отдаёт другой `/openapi.json`. *Fix:* один источник —
  генерируемый из приложения + contract-тест. `DOCS-S-03/04/05` — cheat-sheet описывает несуществующий
  JSON `webhooks`-API, неверный путь/поля `keys/request`, enum без `passage_receipt`.

---

## 4. P3 — Низкий приоритет / полировка (39, выборка)

- `BE-LAY-10` / `BE-SEC-08` — wildcard CORS (`allow_origins=['*']`) безусловно в фабрике приложения.
- `BE-ASY-07` — синхронный Ed25519/sealed-box/bloom в async-хендлере без `run_in_executor` (bloom-seed
  до 100 итераций × 256 кандидатов на event-loop).
- `BE-ASY-09` — `WORKER_ID` из env игнорируется (захардкожен `hostname-pid`).
- `CRYPTO-05` — sealed-box использует **сырой** X25519-секрет как AEAD-ключ без KDF, при этом спека §2.4
  заявляет совместимость с `crypto_box_seal` (это не так). *Fix:* HKDF/BLAKE2b над секретом + поправить §2.4.
  🔧 **Док поправлен** (§2.4 честно описывает кастомную схему, НЕ libsodium). Безопасно в модели угроз
  (свежий ephemeral, курьер только переносит). **📋 Задача наименьшего приоритета** (отдельным коммитом):
  добавить KDF (key = `BLAKE2b(shared,32)`) в firmware-encrypt + backend-decrypt + golden round-trip с
  фикс. ephemeral. Android не трогается; conformance-векторы не меняются (sealed-box недетерминирован).
- `CRYPTO-07` — тест 16-байтности доменов пропускает `DOMAIN_PSG`/`DOMAIN_BLE` (drift теста). `CRYPTO-08` —
  `reader_time_echo` подписывается, но не валидируется.
- `BE-DAT-06` — все FK без `ON DELETE`-политики при soft-delete-семантике. `BE-DAT-09` — два понятия
  «текущего фильтра» (`is_current` vs `MAX(version)`), не-атомарный swap. `BE-DAT-10` — крупные
  фикс-блобы инлайн в горячих таблицах. `BE-DAT-11` — `user_group_id`/`actor_id` — bare UUID без FK
  (висячие ссылки; `user_groups` нет). `BE-DAT-12` — миграции — сырой `op.execute` (дубль источника схемы).
- `FW-ARC-10` — blacklist NVS: `clear()`+поэлементная перезапись на каждый append (износ flash, torn-state).
  `FW-ARC-11` — `whitelist_count` из header не клампится до malloc. `FW-ARC-12` — `session_seq` сбрасывается
  в 0 на буте → телефон не отличает ребут (вопреки спеке). `FW-ARC-13` — мёртвая константа
  `FILTER_CURRENT_FILE`.
- `ANDROID-07` — HCE/VM/UseCase инжектят DAO напрямую, минуя repository. `ANDROID-10` — watchdog-Job
  мутируется вне mutex. `ANDROID-11` — BLE-корреляция result позиционная (UNLIMITED-канал → рассинхрон).
  `ANDROID-12` — `HttpLoggingInterceptor` без `BuildConfig.DEBUG`-гейта, release без R8.
- `DESKTO-03` — `reader_group_id` пишется на устройство в Microsoft-GUID байт-порядке (mixed-endian),
  расходясь со спекой §1.4 (RFC-4122 big-endian) и с `provisioner.py`. `DESKTO-04` — serial без
  ACK/line-framing (magic-таймауты). `DESKTO-08/09/10` — нет валидации pubkey, HttpClient-per-call,
  `Thread.Sleep(2000)` синхронно.
- `REPO-H-06` — пустое вложенное дерево `Backend/Backend/...`. `REPO-H-08` — нет `.dockerignore`
  (`.pyc` копируются в образ). `REPO-H-10` — PlatformIO `^`-диапазоны, `platform=espressif32` без пина.
  `REPO-H-11` — version-catalog Android мёртв (инлайн-версии; BOM 2026.02.01 vs 2024.02.00).
- `TESTIN-07` — нет измерения покрытия; ruff/lint — `continue-on-error` (advisory).
- `OBSERV-11` — дубль `.pyc`/gitignore. `OBSERV-15` — нет SSRF-защиты у webhook-доставки (воркер внутри
  `scud_net` достаёт postgres/metadata). `DOCS-S-02` — `openapi.yaml` без passages/templates/webhooks.
  `DOCS-S-07` — §15.4 спеки противоречив (224 vs 225 B). `DOCS-S-09` — нет ADR/CONTRIBUTING.
  `DOCS-S-10` — мелкие расхождения чисел между доками.

---

## 5. Сквозные архитектурные темы (корневые причины)

### 5.1 Целостность репозитория — половина системы вне git
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; выбран монорепо — ESP32 вшит (`424fd2b`), Desktop закоммичен)
`REPO-H-01` (ESP32 — висячий gitlink) + `REPO-H-02` (Desktop вообще не закоммичен) означают, что свежий
клон собирает только Backend и Android. Для дипломного проекта, сдаваемого как единое целое, это
первоочередное. **Решение:** определиться монорепо vs submodules и привести в порядок (раздел 6, Спринт 0).

### 5.2 Тройная ручная реализация протокола без conformance (главный архитектурный риск)
Один байт-точный контракт реализован 3 раза вручную. Уже найдено **реальное расхождение**: окно
time-sync `5*days` (спека) vs `10*days` (прошивка) — `CRYPTO-04`; и расхождение sealed-box со спекой —
`CRYPTO-05`. Дисциплина «править все три» не масштабируется.
**Лучшее решение (предложение):** маленький IDL/схема структур (`YAML/JSON`: `{name, fields:[{offset,size,
type}], domain, total_len}`) как **единственный** источник → (а) кодогенерация трёх сериализаторов, или
минимум (б) генерируемый golden-корпус векторов, который грузят все три тест-сьюта. Это закрывает
`TESTIN-01`, `CRYPTO-02`, `DOCS-S-08` и предотвращает класс `CRYPTO-04`.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; реализован вариант (б) — golden-корпус `docs/test_vectors/protocol_v1.json` грузят все 3 impl + CI; закрывает `TESTIN-01`/`CRYPTO-02`. Само значение окна time-sync `CRYPTO-04` и sealed-box `CRYPTO-05` — отдельные открытые правки)

### 5.3 Управление секретами и учётками
`BE-DAT-01` (admin123 в проде), `BE-SEC-04` (дефолтный web-secret), `BE-SEC-01` (plaintext-токены),
`BE-SEC-02` (нет разделения привилегий), `OBSERV-10` (дублированный пароль) — единая тема: нет
fail-fast на секретах и нет привилегийной модели. **Решение:** обязательные секреты без дефолтов +
стартовая проверка + хэширование токенов + scopes на ключах.

### 5.4 Очередь задач: заявленная семантика ≠ реализованная
`BE-ASY-01` (claim ≠ atomic), `BE-ASY-02`/`OBSERV-04` (нет retry/backoff), `BE-ASY-04/05` (нет
per-reader-lock/реального debounce), `BE-ASY-08` (HOL-блокировка вебхуками). Док обещает «retry/backoff»,
код — терминальный fail. **Решение:** переписать lifecycle (lease + reclaim + backoff + dead-letter),
изолировать вебхуки от security-critical задач.

### 5.5 Двойной источник истины схемы
ORM-модели **и** сырые `op.execute`-миграции описывают схему независимо (`BE-DAT-12`); CHECK(length),
partial-индексы, триггер `trg_sync_is_active` есть только в SQL и не отражены в ORM → дрейф (`BE-DAT-04`).
**Решение:** выразить ограничения один раз в ORM (`CheckConstraint`, `Index`) и/или CI-проверка
round-trip ORM↔мигрированная БД.

### 5.6 Тесты на SQLite ≠ прод на Postgres
`TESTIN-03`/`TESTIN-08` + `BE-LAY-07`: самый конкуренто-критичный код (claim, дедуп, генерация фильтра,
JSONB) на SQLite не исполняется, а debounce даже имеет специальную SQLite-ветку, маскирующую ошибки.
**Решение:** добавить Postgres-тир в CI (services/testcontainers) для marked-интеграционных тестов.

### 5.7 `admin_web` как теневое приложение
`BE-LAY-01/04/06`, `DOCS-S-03`: web-панель дублирует auth/audit/бизнес-логику и обходит слои → расхождения
(в т.ч. webhooks существуют только в cookie-панели, но документированы как JSON-API). **Решение:**
свести `admin_web` к тонкому presentation-адаптеру над теми же domain/repository.

### 5.8 Fail-open vs fail-closed на ридере
`FW-ARC-03` (фильтр без реверификации на буте), `FW-ARC-06` (переполнение blacklist = fail-open) — отзыв
доступа деградирует «в пользу прохода». Для СКУД политика отказа должна быть явной и осознанной.
**Решение:** задокументировать и реализовать fail-closed для механизмов отзыва.

---

## 6. Дорожная карта

> Оценки — для одного разработчика, знающего код. Каждый пункт ссылается на ID.

### Спринт 0 — «Остановить кровь» (~1 день)
1. **Репозиторий:** решить ESP32 (submodule vs vendored) `REPO-H-01`; закоммитить `Desktop/` `REPO-H-02`;
   root + `Backend/.gitignore` + `.dockerignore`, `git rm --cached` всех `.pyc` `REPO-H-03/04/08`;
   удалить `Backend/Backend/` `REPO-H-06`; добавить `submodules:` в `firmware.yml` `REPO-H-05`.
2. **Прод-функционал:** `httpx` в основные зависимости + smoke-import `OBSERV-02`.
3. **Секреты:** вынести сид из миграций `BE-DAT-01`; fail-fast на `SCUD_WEB_SECRET`/паролях `BE-SEC-04`,
   `OBSERV-10`; `web_secret` в `Settings` + валидаторы `BE-LAY-05`.
4. **Привилегии:** `kind=='admin'` в admin-dependency `BE-SEC-02`.
5. **Android:** убрать `fallbackToDestructiveMigration` + Migration v1→v2 `ANDROID-01`.
6. **Прошивка:** клампить `chunk_len` в READ_CHUNK `FW-ARC-01`.

### Спринт 1 — Безопасность (2–4 дня)
Хэширование токенов `BE-SEC-01`; CSRF + `Secure`-cookie `BE-SEC-03/09`; one-time-plaintext не в URL/логах
`BE-SEC-05`; app-level rate-limit `BE-SEC-06`; закрыть `/metrics`,`/docs` в nginx `BE-SEC-07`/`OBSERV-12`;
гейтить CORS `BE-LAY-10`; Android: HTTPS+pinning `ANDROID-05`, `allowBackup=false` `ANDROID-06`; Desktop:
`SecureStorage` `DESKTO-06`.

### Спринт 2 — Устойчивость очереди + conformance (3–5 дней)
Переписать lifecycle задач (lease/reclaim/backoff/dead-letter) `BE-ASY-01/02`, `OBSERV-04`; per-reader-lock
+ реальный debounce `BE-ASY-04/05`; изоляция вебхуков `BE-ASY-08`; **golden-векторы + IDL протокола**
`TESTIN-01`/`CRYPTO-02`/`DOCS-S-08` (раздел 5.2); согласовать `CRYPTO-04`, `CRYPTO-05`.

### Спринт 3 — Прошивка + тесты (4–6 дней)
Watchdog + session-deadline `FW-ARC-04`; RNG-bootstrap `CRYPTO-01`; реверификация фильтра/ fail-closed
`FW-ARC-03`; политика переполнения blacklist `FW-ARC-06`; PlatformIO `[env:native]`+Unity `TESTIN-02`;
Postgres-тир в CI `TESTIN-03`; тесты worker/revoke `TESTIN-04`; Android CI запускает тесты `TESTIN-05`;
cap/backoff отчётов `ANDROID-04`.

### Спринт 4 — Слоистость, данные, Ops, доки (5–8 дней)
Иерархия исключений + unit-of-work `BE-LAY-02/03`; `admin_web` → тонкий адаптер `BE-LAY-01/04/06`;
партиционирование `passage_events` `BE-DAT-02`; `ON CONFLICT` дедуп `BE-DAT-03`; единый источник
`is_active` `BE-DAT-04`; ENUM/CHECK `BE-DAT-07`; prometheus multiproc `OBSERV-01`; структурные логи
`OBSERV-05`; автобэкап/PITR `OBSERV-08`; алертинг overlay `OBSERV-09`; openapi из приложения + contract-тест
`DOCS-S-01/02/04/05`; ADR/CONTRIBUTING `DOCS-S-09`; версионирование/PROTOCOL_VERSION `REPO-H-07`;
lock-файл зависимостей `REPO-H-09`; root README/justfile `REPO-H-12`.

---

## 7. Что проверено и отклонено (прозрачность)

Состязательная верификация **опровергла 11 находок** — приведены, чтобы не тратить на них усилия:

- `FW-ARC-02` — «A/B-swap не power-fail-атомарен (desync meta vs данные)»: опровергнуто — `load_filter_
  from_flash` перечитывает `m_bits/seed` из заголовка самого слота, так что заявленный desync невозможен
  (остаётся реальная `FW-ARC-03` — отсутствие реверификации). *(Это скорректировало и мою первоначальную
  гипотезу.)*
- `BE-DAT-05` — «коллизия serial при `MAX()+1`»: опровергнуто (единственный вызывающий путь сериализован).
- `OBSERV-03` — «периодические задачи самоуничтожаются при первом сбое»: опровергнуто — `seed_periodic_
  tasks` пере-сидит при рестарте; SPOF-аспект остаётся (учтён в `BE-ASY-01`).
- `OBSERV-06` — «`deploy.replicas` — Swarm-only, compose игнорирует и это баг»: опровергнуто как
  характеристика.
- `OBSERV-13` — «`--forwarded-allow-ips=*` → подмена IP»: факт конфига верен, но эксплойт-импакт
  сфабрикован. *(Скорректировало мою гипотезу.)*
- `OBSERV-07`, `BE-SEC-11`, `FW-ARC-05`, `FW-ARC-09`, `BE-LAY-11`, `DOCS-S-06` — опровергнуты на коде
  (детали в машинном выводе).

---

*Машинно-читаемый полный список (123 находки: локации, проблема, fix, effort, confidence) сгенерирован
аудитом и доступен в выводе воркфлоу. Этот документ — кураторская выжимка с приоритизацией и планом.*
