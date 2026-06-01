# 01. Backend — ТЗ для Python/FastAPI сервера

**Этот документ читается вместе с `00_shared_protocol.md`.** Форматы всех объектов, криптопримитивы, алгоритмы verification — там.

## 0. Задача

Собрать self-hosted backend для СКУД: REST API для Android-приложения и для админ-интеграций, PostgreSQL-хранилище, асинхронный воркер для генерации bloom-фильтров и обработки входящих отчётов от ридеров (через приложение).

## 1. Стек

| Компонент | Версия | Примечание |
|---|---|---|
| Python | 3.11+ | |
| FastAPI | 0.110+ | async endpoints |
| SQLAlchemy | 2.0+ | async |
| Alembic | 1.13+ | миграции |
| asyncpg | 0.29+ | PG driver |
| PyNaCl | 1.5+ | Ed25519, X25519, ChaCha20-Poly1305, BLAKE2b |
| argon2-cffi | 23.1+ | пароли |
| mmh3 | 4.1+ | MurmurHash3 для bloom |
| pydantic | 2.5+ | валидация |
| uvicorn | 0.27+ | ASGI |
| pytest + httpx | | тесты |

## 2. Структура проекта

```
backend/
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── migrations/
│   ├── env.py
│   └── versions/
├── src/
│   └── scud/
│       ├── __init__.py
│       ├── config.py                   # pydantic Settings
│       ├── main.py                     # FastAPI app factory
│       ├── worker.py                   # воркер entry
│       ├── crypto/
│       │   ├── __init__.py
│       │   ├── signing.py              # Ed25519 операции, подпись всех объектов
│       │   ├── sealed_box.py           # X25519 + ChaCha20-Poly1305
│       │   ├── bloom.py                # MurmurHash3 bloom
│       │   ├── key_id.py               # BLAKE2s key_id computation
│       │   └── serialization.py        # pack/unpack всех структур
│       ├── db/
│       │   ├── __init__.py
│       │   ├── session.py              # async engine + session factory
│       │   ├── models.py               # SQLAlchemy ORM
│       │   └── repositories/
│       │       ├── users.py
│       │       ├── devices.py
│       │       ├── permits.py
│       │       ├── keys.py
│       │       ├── grants.py
│       │       ├── readers.py
│       │       ├── filters.py
│       │       ├── reports.py
│       │       └── tasks.py
│       ├── domain/
│       │   ├── auth.py                 # login, refresh, register-device
│       │   ├── permits.py              # выпуск, отзыв
│       │   ├── keys.py                 # выпуск issued_key, отзыв
│       │   ├── grants.py               # автогенерация time_grant
│       │   ├── filters.py              # генерация filter_package
│       │   ├── reports.py              # обработка входящих отчётов
│       │   └── courier.py              # available, download
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py                 # DI: get_current_user, get_api_key
│       │   ├── app/                    # /api/v1/app/*
│       │   │   ├── auth.py
│       │   │   ├── my_data.py
│       │   │   ├── permits.py
│       │   │   ├── keys.py
│       │   │   ├── readers.py
│       │   │   ├── courier.py
│       │   │   └── reports.py
│       │   └── admin/                  # /api/v1/admin/*
│       │       ├── users.py
│       │       ├── reader_groups.py
│       │       ├── readers.py
│       │       ├── permits.py
│       │       ├── keys.py
│       │       ├── api_keys.py
│       │       └── observability.py
│       └── worker_handlers/
│           ├── generate_filter.py
│           ├── process_report.py
│           ├── expire_keys.py
│           └── cleanup.py
└── tests/
    ├── conftest.py
    ├── test_crypto.py
    ├── test_serialization.py
    ├── test_bloom.py
    ├── test_api_auth.py
    ├── test_api_keys.py
    ├── test_workflow_e2e.py        # выпуск → отзыв → генерация фильтра → receipt
    └── fixtures/
```

## 3. Конфигурация

`src/scud/config.py`:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    session_lifetime_hours: int = 24
    refresh_lifetime_days: int = 30
    bloom_fp_rate: float = 0.001
    whitelist_hard_cap: int = 256
    filter_generation_debounce_seconds: int = 5
    worker_poll_interval_seconds: int = 2
    log_level: str = "INFO"
    
    model_config = {"env_file": ".env"}

settings = Settings()
```

`.env.example`:

```
DATABASE_URL=postgresql+asyncpg://scud:password@localhost:5432/scud
SESSION_LIFETIME_HOURS=24
REFRESH_LIFETIME_DAYS=30
BLOOM_FP_RATE=0.001
LOG_LEVEL=INFO
```

## 4. Схема БД (PostgreSQL)

Применяется через Alembic. Initial migration воспроизводит всю схему.

```sql
-- migration: 0001_initial

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Users
CREATE TABLE users (
    user_id          SERIAL PRIMARY KEY,
    login            VARCHAR(64) UNIQUE NOT NULL,
    password_hash    VARCHAR(255) NOT NULL,
    display_name     VARCHAR(128) NOT NULL,
    user_group_id    UUID NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_users_group ON users(user_group_id) WHERE is_active;

-- User devices
CREATE TABLE user_devices (
    device_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          INT NOT NULL REFERENCES users(user_id),
    phone_pubkey     BYTEA NOT NULL UNIQUE CHECK (length(phone_pubkey) = 32),
    device_label     VARCHAR(128),
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at   TIMESTAMPTZ
);
CREATE INDEX idx_devices_user ON user_devices(user_id) WHERE is_active;

-- Sessions
CREATE TABLE sessions (
    session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               INT NOT NULL REFERENCES users(user_id),
    device_id             UUID NOT NULL REFERENCES user_devices(device_id),
    session_token         VARCHAR(64) NOT NULL UNIQUE,
    refresh_token         VARCHAR(64) NOT NULL UNIQUE,
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_expires_at    TIMESTAMPTZ NOT NULL,
    refresh_expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at            TIMESTAMPTZ
);
CREATE INDEX idx_sessions_token ON sessions(session_token) WHERE revoked_at IS NULL;
CREATE INDEX idx_sessions_refresh ON sessions(refresh_token) WHERE revoked_at IS NULL;

-- API keys
CREATE TABLE api_keys (
    api_key_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash         VARCHAR(128) NOT NULL UNIQUE,
    key_prefix       VARCHAR(16) NOT NULL,
    kind             VARCHAR(32) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    created_by       INT REFERENCES users(user_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ,
    last_used_at     TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ
);
CREATE INDEX idx_api_keys_hash ON api_keys(key_hash) WHERE revoked_at IS NULL;

-- Reader groups
CREATE TABLE reader_groups (
    group_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             VARCHAR(128) NOT NULL,
    description      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Readers
CREATE TABLE readers (
    reader_id                     BYTEA PRIMARY KEY CHECK (length(reader_id) = 16),
    reader_group_id               UUID NOT NULL REFERENCES reader_groups(group_id),
    display_name                  VARCHAR(128) NOT NULL,
    description                   TEXT,
    reader_pubkey                 BYTEA NOT NULL CHECK (length(reader_pubkey) = 32),
    server_ed25519_privkey        BYTEA NOT NULL CHECK (length(server_ed25519_privkey) = 32),
    server_ed25519_pubkey         BYTEA NOT NULL CHECK (length(server_ed25519_pubkey) = 32),
    server_x25519_privkey         BYTEA NOT NULL CHECK (length(server_x25519_privkey) = 32),
    server_x25519_pubkey          BYTEA NOT NULL CHECK (length(server_x25519_pubkey) = 32),
    enrolled_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active                     BOOLEAN NOT NULL DEFAULT TRUE,
    last_applied_filter_version   BIGINT NOT NULL DEFAULT 0,
    last_contact_at               TIMESTAMPTZ,
    last_known_time               TIMESTAMPTZ
);
CREATE INDEX idx_readers_group ON readers(reader_group_id) WHERE is_active;

-- Permits
CREATE TABLE permits (
    permit_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  INT NOT NULL REFERENCES users(user_id),
    reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
    display_name             VARCHAR(128) NOT NULL,
    description              TEXT,
    valid_from               TIMESTAMPTZ NOT NULL,
    valid_until              TIMESTAMPTZ NOT NULL,
    n_parallel               INT NOT NULL CHECK (n_parallel > 0),
    max_token_ttl_seconds    INT NOT NULL CHECK (max_token_ttl_seconds > 0),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_api_key       UUID REFERENCES api_keys(api_key_id),
    revoked_at               TIMESTAMPTZ,
    CHECK (valid_until > valid_from)
);
CREATE INDEX idx_permits_user ON permits(user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_permits_reader ON permits(reader_id) WHERE revoked_at IS NULL;

-- Key status enum
CREATE TYPE key_status AS ENUM (
    'active', 'revoked_by_server', 'revoked_by_reader', 'revoked_in_bloom', 'expired'
);

-- Issued keys
CREATE TABLE issued_keys (
    key_id                   BYTEA PRIMARY KEY CHECK (length(key_id) = 16),
    permit_id                UUID NOT NULL REFERENCES permits(permit_id),
    reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
    phone_pubkey             BYTEA NOT NULL CHECK (length(phone_pubkey) = 32),
    device_id                UUID NOT NULL REFERENCES user_devices(device_id),
    issued_at                TIMESTAMPTZ NOT NULL,
    expires_at               TIMESTAMPTZ NOT NULL,
    serial                   INT NOT NULL,
    payload                  BYTEA NOT NULL DEFAULT '\x0000' CHECK (length(payload) = 2),
    status                   key_status NOT NULL DEFAULT 'active',
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    revoked_at               TIMESTAMPTZ,
    revoke_reason            SMALLINT,
    committed_filter_version BIGINT,
    full_key_bytes           BYTEA NOT NULL CHECK (length(full_key_bytes) = 151),
    CHECK (expires_at > issued_at)
);
CREATE INDEX idx_keys_permit_active ON issued_keys(permit_id) WHERE is_active;
CREATE INDEX idx_keys_reader_status ON issued_keys(reader_id, status);
CREATE INDEX idx_keys_device ON issued_keys(device_id) WHERE is_active;
CREATE INDEX idx_keys_expiry ON issued_keys(expires_at) WHERE is_active;
CREATE INDEX idx_keys_committed_version ON issued_keys(reader_id, committed_filter_version)
    WHERE committed_filter_version IS NOT NULL;

-- Triger для is_active
CREATE OR REPLACE FUNCTION sync_is_active() RETURNS TRIGGER AS $$
BEGIN
    NEW.is_active := NEW.status NOT IN ('revoked_by_reader', 'revoked_in_bloom', 'expired');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_is_active
    BEFORE INSERT OR UPDATE OF status ON issued_keys
    FOR EACH ROW EXECUTE FUNCTION sync_is_active();

-- Grant kind enum
CREATE TYPE grant_kind AS ENUM ('soft', 'hard');

-- Time grants
CREATE TABLE time_grants (
    grant_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    permit_id             UUID NOT NULL REFERENCES permits(permit_id),
    reader_id             BYTEA NOT NULL REFERENCES readers(reader_id),
    authority_user_id     INT NOT NULL REFERENCES users(user_id),
    authority_pubkey      BYTEA NOT NULL CHECK (length(authority_pubkey) = 32),
    kind                  grant_kind NOT NULL,
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ NOT NULL,
    full_grant_bytes      BYTEA NOT NULL CHECK (length(full_grant_bytes) = 148),
    revoked_at            TIMESTAMPTZ,
    UNIQUE (permit_id, authority_pubkey)
);
CREATE INDEX idx_grants_permit ON time_grants(permit_id) WHERE revoked_at IS NULL;

-- Filter packages
CREATE TABLE filter_packages (
    filter_version           BIGINT NOT NULL,
    reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
    generated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    m_bits                   INT NOT NULL,
    k_hashes                 SMALLINT NOT NULL,
    hash_seed                INT NOT NULL,
    filter_bytes_len         INT NOT NULL,
    whitelist_count          SMALLINT NOT NULL,
    blacklist_delta_count    SMALLINT NOT NULL,
    package_bytes            BYTEA NOT NULL,
    is_current               BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (reader_id, filter_version)
);
CREATE INDEX idx_filter_current ON filter_packages(reader_id) WHERE is_current;

-- Delivery tasks
CREATE TABLE delivery_tasks (
    task_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reader_id            BYTEA NOT NULL REFERENCES readers(reader_id),
    filter_version       BIGINT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,
    completion_source    VARCHAR(16),
    courier_user_id      INT REFERENCES users(user_id),
    FOREIGN KEY (reader_id, filter_version) REFERENCES filter_packages(reader_id, filter_version)
);
CREATE INDEX idx_delivery_open ON delivery_tasks(reader_id) WHERE completed_at IS NULL;

-- Reader reports
CREATE TABLE reader_reports (
    report_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type              VARCHAR(32) NOT NULL,
    reader_id                BYTEA NOT NULL REFERENCES readers(reader_id),
    received_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_by_user_id     INT REFERENCES users(user_id),
    raw_bytes                BYTEA NOT NULL,
    processed_at             TIMESTAMPTZ,
    processing_error         TEXT
);
CREATE INDEX idx_reports_unprocessed ON reader_reports(received_at) WHERE processed_at IS NULL;
CREATE INDEX idx_reports_reader ON reader_reports(reader_id, received_at);

-- Background tasks
CREATE TABLE background_tasks (
    task_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type            VARCHAR(32) NOT NULL,
    payload              JSONB NOT NULL,
    status               VARCHAR(16) NOT NULL DEFAULT 'pending',
    scheduled_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    error                TEXT,
    retry_count          INT NOT NULL DEFAULT 0,
    worker_id            VARCHAR(64)
);
CREATE INDEX idx_tasks_pending ON background_tasks(scheduled_at, task_type)
    WHERE status = 'pending';

-- Admin audit log
CREATE TABLE admin_audit_log (
    audit_id             BIGSERIAL PRIMARY KEY,
    occurred_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_type           VARCHAR(16) NOT NULL,
    actor_id             UUID NOT NULL,
    action               VARCHAR(64) NOT NULL,
    target_type          VARCHAR(32),
    target_id            TEXT,
    details              JSONB
);
CREATE INDEX idx_audit_actor ON admin_audit_log(actor_id, occurred_at);
CREATE INDEX idx_audit_target ON admin_audit_log(target_type, target_id, occurred_at);
```

## 5. API endpoints

**Префикс:** все под `/api/v1/`.

**Middleware:**
- `/app/*` — требует `Authorization: Bearer <session_token>`.
- `/admin/*` — требует `X-Api-Key: <plaintext_key>`.

Невалидная авторизация → 401.

### 5.1 Auth (user API)

#### POST /api/v1/app/auth/login

Открытый.

Request body (JSON):
```json
{
  "login": "string",
  "password": "string",
  "device_info": { "model": "string", "os": "string", "app_version": "string" }
}
```

Response 200:
```json
{
  "session_token": "string",
  "refresh_token": "string",
  "user_id": 0,
  "user_group_id": "uuid",
  "display_name": "string"
}
```

401: `{"error": "invalid_credentials"}`.
403: `{"error": "user_inactive"}`.

Логика:
1. Найти пользователя по login.
2. Проверить пароль через argon2id. Параметры фиксированы shared §2.6: `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)` — не полагаться на дефолты библиотеки.
3. Проверить is_active.
4. Генерировать session_token и refresh_token (secrets.token_urlsafe(48), чтобы получалось ~64 символа).
5. Создать session с session_expires_at = NOW + 24h, refresh_expires_at = NOW + 30d.
6. Вернуть токены.

Примечание: `device_id` в session на этом этапе — NULL или placeholder. Заполняется при `register-device`.

#### POST /api/v1/app/auth/refresh

Открытый.

Request: `{"refresh_token": "string"}`.

Response 200: как в login.

401: `{"error": "invalid_refresh_token"}`.

Логика:
1. SELECT session WHERE refresh_token=$1 AND revoked_at IS NULL AND refresh_expires_at > NOW.
2. Если нет — 401.
3. Инвалидировать старую session (revoked_at = NOW).
4. Создать новую session с теми же user_id, device_id (если был).
5. Вернуть новые токены.

#### POST /api/v1/app/auth/register-device

Bearer.

Request: `{"phone_pubkey": "base64", "device_label": "string"}`.

Response 200: `{"device_id": "uuid"}`.

Логика:
1. Проверить что phone_pubkey — ровно 32 байта после base64-decode.
2. Проверить уникальность phone_pubkey. Если занят — 409.
3. Деактивировать все активные devices пользователя: is_active=FALSE, deactivated_at=NOW.
4. Все sessions этих devices → revoked_at=NOW (кроме текущей, её обновим).
5. Создать user_devices с is_active=TRUE.
6. Обновить текущую session: SET device_id = new_device_id.
7. Вернуть device_id.

#### POST /api/v1/app/auth/logout

Bearer.

Response 200: `{"ok": true}`.

Логика: sessions.revoked_at = NOW для текущей сессии.

### 5.2 User data

#### GET /api/v1/app/my-data

Bearer.

Response 200:
```json
{
  "user": {"user_id": 0, "display_name": "string", "user_group_id": "uuid"},
  "permits": [
    {
      "permit_id": "uuid",
      "display_name": "string",
      "reader": {"reader_id": "hex", "display_name": "string", "group_id": "uuid"},
      "valid_from": "ISO-8601",
      "valid_until": "ISO-8601",
      "n_parallel": 0,
      "max_token_ttl_seconds": 0,
      "active_keys_count": 0,
      "has_grant_on_this_device": true
    }
  ],
  "devices": [
    {"device_id": "uuid", "device_label": "string", "registered_at": "ISO-8601", "is_current": true}
  ]
}
```

#### GET /api/v1/app/permits

Bearer. Только permits текущего пользователя с `revoked_at IS NULL AND valid_until > NOW`.

Response 200:
```json
{
  "items": [
    {
      "permit_id": "uuid",
      "display_name": "string",
      "description": "string",
      "reader_id": "hex",
      "reader_display_name": "string",
      "valid_from": "ISO-8601",
      "valid_until": "ISO-8601",
      "n_parallel": 0,
      "max_token_ttl_seconds": 0,
      "active_keys_count": 0,
      "has_grant_for_device": true
    }
  ]
}
```

`has_grant_for_device` — существует ли активный time_grant для (permit_id, current_device.phone_pubkey).

#### GET /api/v1/app/permits/{permit_id}/keys

Bearer. 403 если permit не принадлежит текущему user_id.

Response 200:
```json
{
  "items": [
    {
      "key_id": "hex",
      "device_id": "uuid",
      "device_label": "string",
      "is_current_device": true,
      "issued_at": "ISO-8601",
      "expires_at": "ISO-8601",
      "status": "active",
      "full_key_bytes": "base64"
    }
  ]
}
```

Возвращаются все ключи со статусом `active` (не только is_active).

#### POST /api/v1/app/permits/{permit_id}/revoke

Bearer. 403 если permit не принадлежит user.

Response 200: `{"ok": true}`.
Response 409: `{"error": "has_active_keys", "active_count": N}`.

Логика:
1. Проверить ownership.
2. active_count = COUNT(issued_keys WHERE permit_id=$1 AND is_active).
3. Если active_count > 0 → 409.
4. UPDATE permits SET revoked_at=NOW WHERE permit_id=$1.
5. UPDATE time_grants SET revoked_at=NOW WHERE permit_id=$1 AND revoked_at IS NULL.

### 5.3 Keys

#### POST /api/v1/app/keys/request

Bearer.

Request:
```json
{
  "permit_id": "uuid",
  "validity_seconds": 3600,
  "request_grant": true
}
```

Response 200:
```json
{
  "issued_key": {
    "key_id": "hex",
    "full_key_bytes": "base64",
    "issued_at": "ISO-8601",
    "expires_at": "ISO-8601"
  },
  "time_grant": {
    "grant_id": "uuid",
    "full_grant_bytes": "base64",
    "expires_at": "ISO-8601"
  }
}
```

`time_grant` — null если `request_grant=false` или если grant уже существовал (клиент его уже имеет из предыдущего запроса).

Ошибки:
- 400 `{"error": "ttl_too_long", "max": N}` если validity_seconds > permit.max_token_ttl_seconds.
- 400 `{"error": "ttl_invalid"}` если validity_seconds <= 0.
- 400 `{"error": "permit_expired"}`.
- 400 `{"error": "permit_not_started"}` (valid_from > NOW).
- 409 `{"error": "n_parallel_exceeded", "active_count": A, "max": N}`.
- 403 если permit не принадлежит user.
- 400 `{"error": "no_active_device"}` если у user нет активного user_devices.

Логика:

```
1. Lock permit (SELECT FOR UPDATE).
2. Validate permit: принадлежность, не revoked, valid_from ≤ NOW ≤ valid_until.
3. active_count = COUNT(issued_keys WHERE permit_id AND is_active).
4. Если active_count >= permit.n_parallel → 409.
5. Validate validity_seconds.
6. expires_at = MIN(NOW + validity_seconds, permit.valid_until).
7. Получить текущее user_devices (is_active=TRUE).
8. Вычислить next serial: SELECT COALESCE(MAX(serial), 0) + 1 FROM issued_keys WHERE permit_id.
9. issued_at = NOW (трункировано до секунд).
10. Построить 87 B header (см. shared §5.1).
11. key_id = BLAKE2s(reader_id || phone_pubkey || issued_at_8LE || serial_4LE, 16).
12. Подписать: signature = Ed25519_sign(reader.server_ed25519_privkey, domain_KEY || header).
13. full_key_bytes = header || signature (151 B).
14. INSERT issued_keys.
15. Если request_grant=True:
    a. SELECT time_grants WHERE permit_id AND authority_pubkey=device.phone_pubkey AND revoked_at IS NULL.
    b. Если есть — использовать существующий (в ответ НЕ включать, клиент уже имеет).
    c. Если нет — создать:
       - authority_id = random UUID v4 raw bytes.
       - grant_expires_at = permit.valid_until.
       - header = serialize(148 B - 64 B sig).
       - signature = Ed25519_sign(server_ed25519_privkey, domain_TGR || header).
       - INSERT time_grants.
       - Вернуть в ответе.
16. Создать background_task generate_filter для reader_id (дебаунсится).
17. Commit.
18. Вернуть response.
```

#### POST /api/v1/app/keys/{key_id}/revoke-on-server

Bearer.

Response 200: `{"ok": true}`.
Response 403: ключ не принадлежит user.
Response 409: `{"error": "not_active"}` если статус не active.

Логика:
1. SELECT issued_keys JOIN permits WHERE key_id=$1.
2. Если permits.user_id != current_user → 403.
3. Если status != 'active' → 409.
4. UPDATE status='revoked_by_server', revoke_reason=0, revoked_at=NOW.
5. Создать background_task generate_filter для reader_id.

### 5.4 Readers

#### GET /api/v1/app/readers/{reader_id}

Bearer. Available groups = union(permits.reader.group_id, user.user_group_id).

Response 200:
```json
{
  "reader_id": "hex",
  "display_name": "string",
  "description": "string",
  "reader_pubkey": "base64",
  "group_id": "uuid"
}
```

403: ридер не в accessible groups.
404: ридер не найден или не is_active.

`reader_id` в URL — hex строка (32 символа).

#### GET /api/v1/app/readers?group_id={uuid}

Bearer. 403 если group_id не в accessible.

Response 200: `{"items": [/* как single */]}`.

### 5.5 Courier

#### GET /api/v1/app/courier/available

Bearer.

Response 200:
```json
{
  "items": [
    {
      "delivery_id": "uuid",
      "target_reader_id": "hex",
      "target_reader_display_name": "string",
      "filter_version": 0,
      "package_size_bytes": 0,
      "created_at": "ISO-8601"
    }
  ]
}
```

SQL:
```sql
SELECT dt.task_id, r.reader_id, r.display_name, fp.filter_version, 
       octet_length(fp.package_bytes), dt.created_at
FROM delivery_tasks dt
JOIN readers r ON dt.reader_id = r.reader_id
JOIN filter_packages fp ON fp.reader_id = dt.reader_id AND fp.filter_version = dt.filter_version
WHERE dt.completed_at IS NULL
  AND r.reader_group_id = :current_user_group;
```

#### POST /api/v1/app/courier/download

Bearer.

Request: `{"delivery_id": "uuid"}`.

Response 200:
```json
{
  "delivery_id": "uuid",
  "target_reader_id": "hex",
  "filter_version": 0,
  "filter_package_bytes": "base64",
  "courier_id": "hex"
}
```

403: ридер не в user_group_id.
404: delivery не найдена или уже closed.

Логика:
1. SELECT dt, r, fp JOIN WHERE task_id=$1.
2. Проверка group.
3. Проверка dt.completed_at IS NULL.
4. courier_id = first_16_bytes(uuid5(namespace_scud, f"{user_id}:{device_id}")) (детерминированно; одна и та же пара всегда даёт один courier_id).
5. Вернуть package_bytes в base64.

Namespace UUID зафиксирован в константе: `NAMESPACE_COURIER = UUID('a1a11111-2222-3333-4444-555555555555')`.

### 5.6 Reports

#### POST /api/v1/app/reports/submit

Bearer.

Request:
```json
{
  "reports": [
    {
      "type": "delivery_receipt" | "filter_delivery_info" | "blacklist_report",
      "target_reader_id": "hex",
      "bytes": "base64"
    }
  ]
}
```

Response 200:
```json
{
  "accepted": ["uuid1", ...],
  "rejected": [{"index": 2, "reason": "string"}]
}
```

Логика:
- Для каждого report:
  - Валидация: type ∈ {известные}, reader_id существует и is_active, размер bytes осмысленный.
  - Если проблема — добавить в rejected.
  - Иначе: INSERT reader_reports с processed_at=NULL. Создать background_task process_report.
  - Добавить в accepted (report_id).

Обработка (верификация подписи, decrypt) — в воркере.

### 5.7 Admin API

Все admin endpoints требуют `X-Api-Key`. Middleware:
1. Считывает X-Api-Key header.
2. sha256 → key_hash.
3. SELECT api_keys WHERE key_hash=$1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW).
4. Если нет — 401.
5. UPDATE last_used_at = NOW.
6. Передаёт api_key_id в request.state.

Все admin действия пишут в admin_audit_log.

#### Users

`GET /api/v1/admin/users?cursor=1&limit=50` — пагинация.

`POST /api/v1/admin/users` — `{login, password, display_name, user_group_id}` → `{user_id}`.

`GET /api/v1/admin/users/{user_id}` — детали.

`PATCH /api/v1/admin/users/{user_id}` — опциональные `{display_name, user_group_id, is_active}`. При `is_active=false` → revoke всех sessions.

`POST /api/v1/admin/users/{user_id}/reset-password` — `{new_password}` → ok. Revoke sessions.

#### Reader groups

`GET /api/v1/admin/reader-groups?cursor=1&limit=50`
`POST /api/v1/admin/reader-groups` — `{name, description}` → `{group_id}`
`PATCH /api/v1/admin/reader-groups/{id}` 
`DELETE /api/v1/admin/reader-groups/{id}` — 409 если есть ридеры.

#### Readers

`GET /api/v1/admin/readers?cursor=1&limit=50&group_id=X`

`POST /api/v1/admin/readers/enroll`:

Request:
```json
{
  "reader_id": "hex (16 B)",
  "reader_pubkey": "base64",
  "reader_group_id": "uuid",
  "display_name": "string",
  "description": "string"
}
```

Response 200:
```json
{
  "reader_id": "hex",
  "server_ed25519_pubkey": "base64",
  "server_x25519_pubkey": "base64",
  "reader_group_id": "uuid"
}
```

Логика:
1. Проверить уникальность reader_id (409 если существует).
2. Генерировать server_ed25519 keypair через nacl.signing.SigningKey.generate().
3. Генерировать server_x25519 keypair через nacl.public.PrivateKey.generate().
4. INSERT readers со всеми ключами.
5. Вернуть только публичные части.

`GET /api/v1/admin/readers/{reader_id}` — детали: last_contact_at, last_known_time, last_applied_filter_version, open delivery tasks count.

`PATCH /api/v1/admin/readers/{reader_id}` — `{display_name, description, reader_group_id, is_active}`.

#### Permits

`GET /api/v1/admin/permits?cursor=1&limit=50&user_id=X&reader_id=Y`

`POST /api/v1/admin/permits`:

Request:
```json
{
  "user_id": 0,
  "reader_id": "hex",
  "display_name": "string",
  "description": "string",
  "valid_from": "ISO-8601",
  "valid_until": "ISO-8601",
  "n_parallel": 1,
  "max_token_ttl_seconds": 86400
}
```

Response: `{"permit_id": "uuid"}`.

`GET /api/v1/admin/permits/{permit_id}` — детали + все ключи.

`PATCH /api/v1/admin/permits/{permit_id}`.

`POST /api/v1/admin/permits/{permit_id}/revoke` — принудительный, auto-revoke всех активных ключей.

#### Keys

`GET /api/v1/admin/keys?cursor=1&limit=50&permit_id=X&user_id=Y&status=Z`

`GET /api/v1/admin/keys/{key_id}` — детали.

`POST /api/v1/admin/keys/{key_id}/revoke` — принудительный.

#### API keys

`GET /api/v1/admin/api-keys?cursor=1&limit=50`

`POST /api/v1/admin/api-keys`:

Request: `{name, kind: "admin"|"integration", expires_at?: ISO}`.

Response (plaintext показывается один раз):
```json
{
  "api_key_id": "uuid",
  "plaintext": "sk_admin_<48 random chars>",
  "key_prefix": "sk_admin",
  "name": "string",
  "expires_at": "ISO or null"
}
```

plaintext: `"sk_admin_" + secrets.token_urlsafe(36)` (или `sk_integration_`).

Хранится: key_hash = sha256(plaintext).hexdigest(), key_prefix = plaintext[:8].

`POST /api/v1/admin/api-keys/{id}/revoke` → ok.

#### Observability

`GET /api/v1/admin/readers/{reader_id}/delivery-status`:

Response:
```json
{
  "reader_id": "hex",
  "last_applied_filter_version": 0,
  "current_server_version": 0,
  "open_delivery_tasks": [
    {"task_id": "uuid", "filter_version": 0, "created_at": "ISO", "age_hours": 0}
  ],
  "last_contact_at": "ISO or null",
  "last_known_time": "ISO or null",
  "time_drift_seconds": 0
}
```

`GET /api/v1/admin/background-tasks?cursor=1&limit=50&status=failed`

`GET /api/v1/admin/audit-log?cursor=1&limit=50&actor_id=X&target_type=permit`

## 6. Воркер

Воркер запускается через `python -m scud.worker`. Бесконечный цикл:

```python
while True:
    task = await fetch_next_task()
    if task is None:
        await asyncio.sleep(settings.worker_poll_interval_seconds)
        continue
    try:
        await handle_task(task)
        await mark_done(task)
    except Exception as e:
        await mark_failed(task, str(e))
```

`fetch_next_task`:

```sql
WITH next AS (
  SELECT task_id FROM background_tasks
  WHERE status = 'pending' AND scheduled_at <= NOW()
  ORDER BY scheduled_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE background_tasks bt
SET status='processing', started_at=NOW(), worker_id=$1
FROM next
WHERE bt.task_id = next.task_id
RETURNING bt.*;
```

### 6.1 generate_filter handler

Payload: `{"reader_id": "hex"}`.

**Дебаунс:** перед созданием task проверяется:
```sql
SELECT task_id FROM background_tasks
WHERE task_type='generate_filter'
  AND status='pending'
  AND payload->>'reader_id' = $reader_id_hex;
```
Если есть — новая не создаётся.

Алгоритм:

```python
async def handle_generate_filter(payload):
    reader_id = bytes.fromhex(payload['reader_id'])
    
    async with db.begin() as tx:
        reader = await get_reader(tx, reader_id)
        
        # Вычислить следующий filter_version
        max_v = await tx.execute("""
            SELECT COALESCE(MAX(filter_version), 0) FROM filter_packages WHERE reader_id=$1
        """, reader_id).scalar()
        V = max_v + 1
        
        # Кандидаты в bloom
        candidates = await tx.execute("""
            SELECT key_id FROM issued_keys
            WHERE reader_id = $1
              AND expires_at > NOW()
              AND status IN ('revoked_by_server', 'revoked_by_reader', 'revoked_in_bloom')
        """, reader_id).scalars().all()
        
        # Активные (для whitelist check)
        active = await tx.execute("""
            SELECT key_id FROM issued_keys
            WHERE reader_id = $1 AND status = 'active' AND expires_at > NOW()
        """, reader_id).scalars().all()
        
        n = len(candidates)
        if n == 0:
            m_bits = 64  # минимальный фильтр
            k_hashes = 1
            hash_seed = 0
            bloom_bytes = bytes(8)
            whitelist = []
        else:
            # Параметры
            import math
            m_bits = math.ceil(-n * math.log(0.001) / (math.log(2) ** 2))
            m_bits = ((m_bits + 7) // 8) * 8  # кратно 8
            k_hashes = max(1, math.ceil(m_bits / n * math.log(2)))
            
            # Подобрать hash_seed под whitelist cap
            for seed in range(1, 100):
                bloom_bytes = build_bloom(candidates, m_bits, k_hashes, seed)
                whitelist = [(k, get_expires_at(k)) for k in active if bloom_check(bloom_bytes, k, m_bits, k_hashes, seed)]
                if len(whitelist) <= 256:
                    hash_seed = seed
                    break
            else:
                raise Exception("cannot fit whitelist under cap")
        
        # Commit filter_version
        await tx.execute("""
            UPDATE issued_keys
            SET committed_filter_version = $1
            WHERE key_id = ANY($2) AND committed_filter_version IS NULL
        """, V, list(candidates))
        
        # Вычислить blacklist_delta
        prev_version = await tx.execute("""
            SELECT COALESCE(last_applied_filter_version, 0) FROM readers WHERE reader_id = $1
        """, reader_id).scalar()
        
        bl_delta = await tx.execute("""
            SELECT key_id FROM issued_keys
            WHERE reader_id = $1
              AND status = 'revoked_by_reader'
              AND committed_filter_version BETWEEN $2 AND $3
        """, reader_id, prev_version + 1, V).scalars().all()
        bl_delta = bl_delta[:256]  # cap
        
        # Сериализовать filter_package
        whitelist.sort(key=lambda x: x[0])
        package_bytes = serialize_filter_package(
            reader_id, V, generated_at=now(),
            m_bits=m_bits, k_hashes=k_hashes, hash_seed=hash_seed,
            bloom_bytes=bloom_bytes,
            whitelist=whitelist,
            blacklist_delta=bl_delta,
            server_ed25519_privkey=reader.server_ed25519_privkey
        )
        
        # Insert filter_packages
        await tx.execute("""
            UPDATE filter_packages SET is_current=FALSE WHERE reader_id=$1
        """, reader_id)
        await tx.execute("""
            INSERT INTO filter_packages
            (filter_version, reader_id, generated_at, m_bits, k_hashes, hash_seed,
             filter_bytes_len, whitelist_count, blacklist_delta_count,
             package_bytes, is_current)
            VALUES ($1, $2, NOW(), $3, $4, $5, $6, $7, $8, $9, TRUE)
        """, V, reader_id, m_bits, k_hashes, hash_seed,
           len(bloom_bytes), len(whitelist), len(bl_delta), package_bytes)
        
        # Delivery task
        await tx.execute("""
            INSERT INTO delivery_tasks (reader_id, filter_version)
            VALUES ($1, $2)
        """, reader_id, V)
        
        # Закрыть устаревшие delivery tasks
        await tx.execute("""
            UPDATE delivery_tasks
            SET completed_at=NOW, completion_source='superseded'
            WHERE reader_id=$1 AND filter_version < $2 AND completed_at IS NULL
        """, reader_id, V)
```

### 6.2 process_report handler

Payload: `{"report_id": "uuid"}`.

Диспетчер по report_type.

#### process_delivery_receipt

```python
async def process_delivery_receipt(raw: bytes, reader: Reader):
    if len(raw) != 112:
        raise Exception("bad length")
    
    reader_id = raw[0:16]
    applied_filter_version = int.from_bytes(raw[16:24], 'little')
    applied_at = int.from_bytes(raw[24:32], 'little')
    courier_id = raw[32:48]
    signature = raw[48:112]
    
    if reader_id != reader.reader_id:
        raise Exception("reader_id mismatch")
    
    # Verify
    VerifyKey(reader.reader_pubkey).verify(DOMAIN_RCP + raw[:48], signature)
    
    # Find delivery task
    task = await find_open_delivery(reader_id, applied_filter_version)
    if task:
        courier_user_id = resolve_user_by_courier_id(courier_id)
        await tx.execute("""
            UPDATE delivery_tasks
            SET completed_at=NOW, completion_source='receipt', courier_user_id=$1
            WHERE task_id=$2
        """, courier_user_id, task.task_id)
    
    # Update reader state
    await tx.execute("""
        UPDATE readers
        SET last_applied_filter_version = GREATEST(last_applied_filter_version, $1),
            last_contact_at = NOW,
            last_known_time = to_timestamp($2)
        WHERE reader_id = $3
    """, applied_filter_version, applied_at, reader_id)
    
    # Promote keys to revoked_in_bloom
    await tx.execute("""
        UPDATE issued_keys
        SET status='revoked_in_bloom'
        WHERE reader_id = $1
          AND status IN ('revoked_by_server', 'revoked_by_reader')
          AND committed_filter_version IS NOT NULL
          AND committed_filter_version <= $2
    """, reader_id, applied_filter_version)
```

#### process_fdi_blob

Структура 241 B:
- парсить cleartext поля (offsets из shared §5.8).
- Verify reader_signature (bytes[0:145] c domain_FDI).
- Decrypt encrypted_courier_blob (bytes[33:137], 104 B).
- Проверить decrypted.reader_id == cleartext reader_id, decrypted.filter_version == cleartext filter_version.
- Аналогично receipt: закрыть delivery task, обновить reader state, promote keys.

#### process_blk_report

- Parse cleartext envelope.
- Verify signature.
- Decrypt blob.
- Проверить cleartext reader_id и reader_time совпадают с decrypted.
- Update reader.last_contact_at / last_known_time.
- Для каждого entry:
  ```sql
  UPDATE issued_keys
  SET status='revoked_by_reader', revoke_reason=1,
      revoked_at = COALESCE(revoked_at, to_timestamp($entry.revoked_at))
  WHERE key_id = $entry.key_id
    AND status IN ('active', 'revoked_by_server');
  ```
- Если хотя бы одна строка затронута — создать background_task generate_filter.

### 6.3 expire_keys handler

Периодическая задача. При завершении self-schedule'ится на NOW + 1 hour.

```sql
UPDATE issued_keys
SET status='expired', revoke_reason=2, revoked_at=expires_at
WHERE expires_at < NOW AND is_active;

UPDATE permits
SET revoked_at=valid_until
WHERE valid_until < NOW AND revoked_at IS NULL;

UPDATE time_grants
SET revoked_at=expires_at
WHERE expires_at < NOW AND revoked_at IS NULL;

DELETE FROM sessions
WHERE refresh_expires_at < NOW;
```

### 6.4 cleanup handler

Периодическая, раз в сутки:

```sql
DELETE FROM reader_reports WHERE processed_at < NOW - INTERVAL '30 days';
DELETE FROM background_tasks WHERE completed_at < NOW - INTERVAL '7 days';
DELETE FROM admin_audit_log WHERE occurred_at < NOW - INTERVAL '365 days';
```

### 6.5 Периодические задачи seeding

При старте воркера (если expire_keys / cleanup нет pending):

```sql
INSERT INTO background_tasks (task_type, payload, scheduled_at)
VALUES ('expire_keys', '{}', NOW())
ON CONFLICT DO NOTHING;

INSERT INTO background_tasks (task_type, payload, scheduled_at)
VALUES ('cleanup', '{}', NOW())
ON CONFLICT DO NOTHING;
```

После выполнения — handler создаёт следующую задачу с scheduled_at = NOW + interval.

## 7. Крипто-модуль

### 7.1 `scud/crypto/signing.py`

```python
from nacl.signing import SigningKey, VerifyKey
from nacl.bindings import crypto_sign_BYTES

DOMAIN_KEY = b"RDR-KEY-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_INF = b"RDR-INF-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_RSP = b"RDR-RSP-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_FLT = b"RDR-FLT-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_RCP = b"RDR-RCP-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_BLK = b"RDR-BLK-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_FDI = b"RDR-FDI-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_TGR = b"RDR-TGR-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_TIM = b"RDR-TIM-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_REV = b"RDR-REV-v1\x00\x00\x00\x00\x00\x00"

for d in [DOMAIN_KEY, DOMAIN_INF, DOMAIN_RSP, DOMAIN_FLT, DOMAIN_RCP,
          DOMAIN_BLK, DOMAIN_FDI, DOMAIN_TGR, DOMAIN_TIM, DOMAIN_REV]:
    assert len(d) == 16

def sign_detached(privkey_bytes: bytes, domain: bytes, payload: bytes) -> bytes:
    """Возвращает 64-байтную подпись."""
    sk = SigningKey(privkey_bytes)
    return sk.sign(domain + payload).signature

def verify_detached(pubkey_bytes: bytes, domain: bytes, payload: bytes, signature: bytes) -> bool:
    try:
        vk = VerifyKey(pubkey_bytes)
        vk.verify(domain + payload, signature)
        return True
    except Exception:
        return False
```

### 7.2 `scud/crypto/key_id.py`

Shared §5.1 требует **BLAKE2s-128**. Используем `hashlib.blake2s` — это единственная корректная реализация; `nacl.bindings.crypto_generichash` даёт BLAKE2b и для key_id **не подходит**.

```python
import hashlib

def compute_key_id(reader_id: bytes, phone_pubkey: bytes, issued_at: int, serial: int) -> bytes:
    data = reader_id + phone_pubkey + issued_at.to_bytes(8, 'little') + serial.to_bytes(4, 'little')
    return hashlib.blake2s(data, digest_size=16).digest()
```

Все три реализации (backend, firmware, android) ДОЛЖНЫ использовать BLAKE2s-128 идентично. BLAKE2b и BLAKE2s — разные алгоритмы и дают разные результаты.

### 7.3 `scud/crypto/sealed_box.py`

```python
from nacl.bindings import crypto_scalarmult, crypto_aead_chacha20poly1305_ietf_decrypt
from nacl.public import PrivateKey, PublicKey
import hashlib

def decrypt_sealed_blob(blob: bytes, server_x25519_priv: bytes, server_x25519_pub: bytes) -> bytes:
    if len(blob) < 48:
        raise ValueError("blob too short")
    
    ephemeral_pub = blob[:32]
    ct_and_tag = blob[32:]
    
    shared = crypto_scalarmult(server_x25519_priv, ephemeral_pub)
    
    nonce_24 = hashlib.blake2b(ephemeral_pub + server_x25519_pub, digest_size=24).digest()
    nonce_12 = nonce_24[:12]
    
    plaintext = crypto_aead_chacha20poly1305_ietf_decrypt(
        ct_and_tag, aad=None, nonce=nonce_12, key=shared
    )
    return plaintext
```

### 7.4 `scud/crypto/bloom.py`

```python
import mmh3

def build_bloom(key_ids: list[bytes], m_bits: int, k_hashes: int, seed: int) -> bytes:
    assert m_bits % 8 == 0
    bits = bytearray(m_bits // 8)
    for key_id in key_ids:
        for i in range(k_hashes):
            h = mmh3.hash(key_id, seed + i, signed=False) % m_bits
            bits[h // 8] |= (1 << (h % 8))
    return bytes(bits)

def bloom_contains(bits: bytes, key_id: bytes, m_bits: int, k_hashes: int, seed: int) -> bool:
    for i in range(k_hashes):
        h = mmh3.hash(key_id, seed + i, signed=False) % m_bits
        if not (bits[h // 8] & (1 << (h % 8))):
            return False
    return True
```

Важно: `mmh3.hash(data, seed, signed=False)` даёт unsigned 32-bit output. Эквивалент MurmurHash3_x86_32 из reference implementation.

### 7.5 `scud/crypto/serialization.py`

Паковщики всех 11 структур из shared §5. Пример для issued_key:

```python
def pack_issued_key_header(
    reader_id: bytes, phone_pubkey: bytes,
    issued_at: int, expires_at: int,
    permit_id: bytes, serial: int, payload: bytes
) -> bytes:
    assert len(reader_id) == 16
    assert len(phone_pubkey) == 32
    assert len(permit_id) == 16
    assert len(payload) == 2
    return (
        bytes([0x01])
        + reader_id
        + phone_pubkey
        + issued_at.to_bytes(8, 'little')
        + expires_at.to_bytes(8, 'little')
        + permit_id
        + serial.to_bytes(4, 'little')
        + payload
    )  # 87 B

def serialize_issued_key(header: bytes, server_privkey: bytes) -> bytes:
    from .signing import sign_detached, DOMAIN_KEY
    assert len(header) == 87
    sig = sign_detached(server_privkey, DOMAIN_KEY, header)
    return header + sig  # 151 B
```

Аналогично для time_grant, filter_package, delivery_receipt (при верификации), etc.

## 8. Dependencies (pyproject.toml)

```toml
[project]
name = "scud-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "sqlalchemy[asyncio]>=2.0",
    "alembic>=1.13",
    "asyncpg>=0.29",
    "pynacl>=1.5",
    "argon2-cffi>=23.1",
    "mmh3>=4.1",
    "pydantic>=2.5",
    "pydantic-settings>=2.1",
    "python-multipart>=0.0.9",
    "python-jose[cryptography]>=3.3",  # не нужен, токены собственные
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "aiosqlite>=0.20",  # для тестов, если нужно
]
```

## 9. Docker

`Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

CMD ["uvicorn", "scud.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: scud
      POSTGRES_PASSWORD: scud
      POSTGRES_DB: scud
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  migrate:
    build: .
    command: alembic upgrade head
    depends_on: [postgres]
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:scud@postgres/scud

  app:
    build: .
    command: uvicorn scud.main:app --host 0.0.0.0 --port 8000
    depends_on:
      migrate:
        condition: service_completed_successfully
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:scud@postgres/scud
    ports:
      - "8000:8000"

  worker:
    build: .
    command: python -m scud.worker
    depends_on:
      migrate:
        condition: service_completed_successfully
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:scud@postgres/scud

volumes:
  pgdata:
```

## 10. Тесты и критерии приёмки

Тесты через pytest + httpx.

Обязательные тестовые сценарии:

### 10.1 Крипто

- `test_compute_key_id`: детерминированность, 16 байт на выходе.
- `test_sign_verify_roundtrip`: подписать/проверить issued_key.
- `test_sealed_box_roundtrip`: зашифровать на X25519-паре, расшифровать.
- `test_bloom_no_false_negatives`: если ключ в bloom — bloom.contains всегда True.
- `test_bloom_fp_rate_near_target`: для n=1000, p=0.001 — FP на случайных ключах в пределах 0.001 × коэффициент.

### 10.2 E2E

Полный сценарий:

1. Create user (admin API).
2. Login user.
3. Register device.
4. Enroll reader (admin API).
5. Create permit (admin API).
6. Request key (user API).
7. Генерация filter_package в воркере (trigger'ится автоматически).
8. Получить filter_package через /courier/download.
9. Revoke-on-server.
10. Новая генерация фильтра в воркере.
11. Submit delivery_receipt (бинарь, сгенерированный в тесте как будто от ридера).
12. Проверить: статус ключа стал revoked_in_bloom.

### 10.3 Конкурентность

- test_n_parallel_exceeded: параллельно запросить 3 ключа на permit с n_parallel=2, один упадёт с 409.
- test_generate_filter_idempotent: запустить handler дважды — не создать два filter_package одной версии.

### 10.4 Инварианты

Для любого момента:
- `permits.user_id` = `permits.issued_keys.permit_id.user_id` (через связь permits.user_id).
- `COUNT(issued_keys WHERE is_active AND permit_id=$P) ≤ permits[$P].n_parallel`.
- Для каждого reader_id ровно один filter_packages с is_current=TRUE.
- `key_id` детерминированно совпадает с BLAKE2s(...) при заданных входах.

### 10.5 Критерий приёмки

- Все тесты из 10.1–10.4 проходят.
- `docker-compose up` поднимает всю систему с чистой БД, миграции применяются, сервис слушает на 8000.
- /api/v1/app/auth/login возвращает валидные токены для правильных credentials.
- Воркер стабильно пишет в stdout "processing task ..." и выполняет все типы задач.
- Из админского CLI (скрипт-хелпер `scud-admin create-user ...`) можно создать пользователя, ридера, permit — готово к работе с клиентом.

## 11. Ключевые замечания

- **Нельзя раскрывать server_ed25519_privkey / server_x25519_privkey ни в одном response.** Только при enroll возвращаются публичные части.
- **plaintext API-ключ возвращается ровно один раз** при создании. Дальше — только prefix.
- **full_key_bytes / full_grant_bytes хранятся в БД** как источник правды; их можно отдавать клиенту, они уже подписаны сервером.
- **courier_id детерминирован** от (user_id, device_id) через UUID v5.
- Все временные метки в БД — TIMESTAMPTZ. При сериализации в bytes — unix timestamp в секундах (LE).
- Валидация входных данных через pydantic: все bytes-поля приходят base64, валидатор проверяет длину после decode.
