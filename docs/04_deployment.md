# 04. Развёртывание и интеграция SCUD

Этот документ — операционный мануал. Кратко: где что крутится, как поднять с нуля и как встроить в инфраструктуру заказчика.

## 1. Топология

```
                            ┌────────────────────────────────┐
                            │   Сеть заказчика (LAN/VPN)     │
                            │                                │
              ┌─────────┐   │   ┌──────────┐   ┌──────────┐  │
   Интернет → │  nginx  │ ──┼──▶│  app x2  │ ─▶│ postgres │  │
              │ TLS+RL  │   │   │ uvicorn  │   │   15     │  │
              └─────────┘   │   └──────────┘   └──────────┘  │
                            │        ▲                        │
                            │        │ same network           │
                            │   ┌──────────┐                  │
                            │   │  worker  │                  │
                            │   │ (long-   │                  │
                            │   │  poller) │                  │
                            │   └──────────┘                  │
                            └────────────────────────────────┘

   Android app ◀─── HTTPS (опционально) ─────────────────┐
        │                                                 │
        │ NFC HCE (физический tap)         BLE (опц., §16)│
        ▼                                                 ▼
   ┌──────────┐                                     ┌──────────┐
   │ ESP32    │                                     │ ESP32    │
   │ reader   │  (battery, NFC only)                │ reader   │  (mains, NFC+BLE)
   │ door     │                                     │ barrier  │
   └──────────┘                                     └──────────┘
```

**Ключевая инвариантность:** Reader **никогда** не ходит в Интернет. Между ним и backend всегда курьер (Android-app). См. shared/00 §1 общую идею.

## 2. Быстрый старт (dev / staging)

```bash
cd Backend
cp .env.example .env          # отредактировать пароли, в т.ч. POSTGRES_PASSWORD

docker compose build
docker compose up -d          # поднимает postgres → migrate → app + worker

# проверки:
docker compose logs -f app    # должно быть "Application startup complete"
curl http://127.0.0.1:8000/health
```

`migrate` идёт как one-shot service (`restart: "no"`), завершается после `alembic upgrade head` и блокирует старт `app`/`worker` через `service_completed_successfully` (в compose).

> **Локальный порт Postgres.** В репозитории есть `docker-compose.override.yml`,
> публикующий Postgres на `127.0.0.1:5433` (если 5432 занят другим проектом).
> Compose подхватывает override автоматически. Внутри сети app/worker/migrate
> всё равно ходят на `postgres:5432` — это не влияет ни на что, кроме доступа с хоста.
> Если 5433 не нужен — удалите файл или замените на свой порт.

---

## 2A. Полное развёртывание с нуля (все четыре компонента)

Раздел §2 поднимает только backend. Ниже — **сквозной чек-лист** для всей системы:
backend+БД → bootstrap-ключ → прошивка ридера → сборка телефона → провижининг → smoke-тап.
Команды конкретные, под copy-paste.

### Шаг 0 — что нужно на машине деплоя

| Компонент | Чем собирается | Где запускается |
|---|---|---|
| Backend + Postgres | Docker + Docker Compose v2 | сервер (Linux/любой Docker-хост) |
| ESP32 firmware | PlatformIO Core (`pio`) | dev-машина с USB к плате |
| Android app | JDK 17 + Android SDK (Gradle wrapper в репо) | dev-машина |
| Провижнер — CLI `scud-provision` | **.NET 9 SDK** (любая ОС: Linux/macOS/Windows) | **офлайн** машина провижининга |
| Провижнер — GUI (опц.) | .NET 9 SDK + MAUI workload (только Windows) | то же, если нужен GUI |

Прошивка/провижининг идут **на отдельной офлайн-машине** — приватные ключи ридеров никогда не покидают этот хост (инвариант I1).

### Шаг 1 — backend + Postgres + миграции

```bash
cd Backend
cp .env.example .env                # отредактировать POSTGRES_PASSWORD, DATABASE_URL, SCUD_WEB_SECRET
docker compose build
docker compose up -d                # postgres → migrate (alembic upgrade head) → app + worker
curl -s http://127.0.0.1:8000/health   # → {"status":"ok"}
```

Миграции выполняет one-shot service `migrate` (см. §2). Актуальная цепочка ревизий:

```
0001_initial → 0002_seed_test_data → 0003_permit_revoke_initiated →
0004_passage_events → 0005_webhook_subscriptions → 0006_reader_config →
0007_reader_profiles → 0008_permit_max_total_issued → (head)
```

> **Только Postgres.** Миграции рассчитаны на PostgreSQL (`pgcrypto`/`gen_random_uuid`,
> JSONB, server-side default'ы). SQLite используется **только** в unit-тестах через
> `with_variant`-фолбэки — в проде это не поддерживаемая конфигурация.

### Шаг 2 — bootstrap admin API key

Chicken-and-egg: первый admin-ключ нельзя создать через API (для API нужен ключ). Вставляем напрямую в БД, дальше — только через API/панель.

```bash
docker compose exec postgres psql -U scud -c "
  INSERT INTO api_keys(key_hash, key_prefix, kind, name)
  VALUES (encode(sha256('sk_admin_BOOTSTRAP_CHANGE_ME'::bytea),'hex'),
          'sk_admin','admin','bootstrap');
"
export H=http://127.0.0.1:8000
export K=sk_admin_BOOTSTRAP_CHANGE_ME

# Сразу создать персональные ключи и отозвать bootstrap (см. чек-лист §7):
curl -s -X POST $H/api/v1/admin/api-keys -H "X-Api-Key: $K" \
  -H "Content-Type: application/json" -d '{"name":"ops-laptop","kind":"admin"}'
```

Тем же plaintext-ключом логинятся в web-панель: `http://127.0.0.1:8000/admin/` (cookie на 8 ч, подпись `SCUD_WEB_SECRET`).

### Шаг 3 — подготовить группу ридеров (нужна для enroll)

```bash
curl -s -X POST $H/api/v1/admin/reader-groups -H "X-Api-Key: $K" \
  -H "Content-Type: application/json" \
  -d '{"name":"Холл","description":"1 этаж"}'
# → {"group_id":"<GROUP-UUID>"}  ← понадобится при провижининге
```

### Шаг 4 — собрать и прошить firmware (обе сборки)

PlatformIO, два окружения. `esp32dev` — только NFC (в т.ч. battery-ридеры); `esp32dev_ble` — NFC+BLE (mains-powered шлагбаумы/турникеты, `-DSCUD_BLE_ENABLED=1`, линкует NimBLE).

```bash
cd ESP32/firmware
# разовая подготовка: положить Monocypher (см. lib/Monocypher/README.md),
# в lib/PN532/PN532.h поднять PN532_PACKBUFFSIZE до 300.

pio run -e esp32dev                       # NFC-only build
pio run -e esp32dev_ble                   # NFC+BLE build

pio run -e esp32dev --target upload       # прошить подключённую плату (по USB)
pio device monitor                        # → [PROVISIONING] device not provisioned.
```

Хост-тесты протокола (без железа): `pio test -e native` / см. `test_host/`.

### Шаг 5 — провижининг ридера (enroll + flash ключей)

Три пути. **A и B — это один и тот же код** (общая библиотека `ScudProvisioner.Core`,
оркестратор `ProvisionFlow`) и делают **полный** провижининг: `GEN-KEYPAIR` на плате →
`POST /admin/readers/enroll` → identity + **вся per-reader конфигурация** (серверный
профиль / resolved `config-script` либо локальный шаблон) → `SET-TIME` → `COMMIT`.
Путь C — минимальный Python-fallback без .NET (identity + lock-duration + time).

**A. Кросс-платформенный CLI `scud-provision` (.NET, любая ОС — рекомендуется):**
```bash
dotnet run --project Desktop/ScudProvisioner.Cli -- \
  --port /dev/ttyUSB0 \
  --server $H \
  --admin-api-key $K \
  --group-id <GROUP-UUID> \
  --display-name "Турникет холла" \
  [--profile-id <UUID>]            # серверный профиль; без него — локальный шаблон
# после COMMIT — нажать RST на плате → [READY]
```
Нужен только **.NET 9 SDK** (без MAUI-workload). Работает на Linux / macOS / Windows.

**B. Desktop GUI (.NET MAUI, Windows):**
```pwsh
dotnet workload install maui                       # разово
cd Desktop/ScudProvisioner
dotnet build -f net9.0-windows10.0.19041.0
dotnet run   -f net9.0-windows10.0.19041.0
# Settings → Backend URL + Admin API key → Provision → (опц. серверный профиль) → Load → порт/группа → Provision reader
```

**C. Python CLI `provisioner.py` (любая ОС, без .NET — минимальный):**
```bash
cd ESP32/firmware/tools
pip install pyserial requests
python provisioner.py \
  --port /dev/ttyUSB0 --server $H --admin-api-key $K \
  --group-id <GROUP-UUID> --display-name "Турникет холла"
# шлёт identity + lock-duration + time; полную per-reader конфигурацию даёт A/B
```

Приватный ключ ридера генерится **в** ESP32 и наружу не выводится; server-ключи для этого ридера генерит backend и хранит только в своей БД (инварианты I1, I3).

### Шаг 6 — собрать Android-app (data-mule)

```bash
cd AndroidApp
# указать адрес backend в local.properties / BuildConfig (см. data/remote/),
# затем:
./gradlew assembleDebug                   # APK → app/build/outputs/apk/debug/
./gradlew installDebug                    # установить на подключённый телефон (adb)
./gradlew testDebugUnitTest               # юнит + conformance-векторы протокола
```

(`gradlew.bat` — для Windows-хоста.)

### Шаг 7 — smoke-тест прохода (end-to-end)

```
1. В web-панели/через API: создать User, выдать Permit (user→reader) — §5.
2. На телефоне: логин → register-device → запросить ключ по permit'у.
3. Поднести телефон к ридеру (NFC tap).
4. Ридер: LED зелёный, замок щёлкает (GPIO26), на экране Tap — вердикт OK.
5. Телефон забирает passage_receipt, при следующей online-синхронизации
   POST /reports/submit → проход появляется в /admin/passages.
```

Проверка с сервера, что квитанция дошла:
```bash
curl -s "$H/api/v1/admin/passages?limit=5" -H "X-Api-Key: $K" | jq '.items[0]'
```

---

## 3. Production-развёртывание

### 3.1 Compose-overlay

```bash
cd Backend
cp .env.example .env.prod     # сильный POSTGRES_PASSWORD!
# положить fullchain.pem / privkey.pem в deploy/certs/

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d --build
```

Что меняет prod-overlay:
- 2 реплики `app` (uvicorn `--workers 4` каждый = 8 рабочих процессов суммарно).
- Postgres 5432 **не публикуется** наружу.
- bind-mount исходников отключён → код только из image.
- nginx side-car: TLS termination + rate-limit `/api/v1/app/auth` (10 req/min) + общий лимит (120 req/min).
- Resource limits (memory + cpu).
- `--proxy-headers --forwarded-allow-ips=*` для корректного логирования client-IP.

### 3.2 Бэкап Postgres

```bash
docker compose exec postgres \
  pg_dump -U scud -Fc scud > backup-$(date +%F).pgcustom
```

Восстановление:
```bash
docker compose exec -T postgres \
  pg_restore -U scud -d scud --clean --if-exists < backup-<date>.pgcustom
```

### 3.3 Миграции вне dev-цикла

```bash
docker compose run --rm migrate alembic upgrade head
# или конкретная ревизия:
docker compose run --rm migrate alembic upgrade 0004_passage_events
```

### 3.4 Версия 4 (passage_events) — апгрейд с 3

```bash
# просто:
docker compose run --rm migrate alembic upgrade head
# проверить:
docker compose exec postgres psql -U scud -c "\d passage_events"
```

Никаких бэкап-обязательств: миграция только создаёт новую таблицу + индексы, не трогает существующие.

## 3.5 Admin web-панель

После `docker compose up -d` админка доступна по адресу:

- dev: `http://127.0.0.1:8000/admin/`
- prod: `https://your-host/admin/`

**Логин**: вводится plaintext admin API key (с `kind=admin` из таблицы `api_keys`). Cookie выдаётся подписанной `SCUD_WEB_SECRET` сессией на 8 часов.

**Что покрыто в UI:**

| Раздел | Возможности |
|---|---|
| Dashboard | счётчики (users, readers, permits, ключи, проходы), последние passages, stale readers |
| Пользователи | список (с поиском), создание, деактивация/активация, список устройств |
| Группы ридеров | CRUD |
| Ридеры | enrollment (генерация server-side ключей), детальная карточка, toggle active |
| Permits | создание (форма с user/reader pickers), двухфазный revoke (`active → revoking → revoked`) |
| Issued keys | фильтрация по статусу, force-revoke |
| Проходы (passages) | фильтр (user/reader/период), пагинация, кнопка экспорта в CSV |
| API keys | создание (plaintext показывается **один раз**), revoke; самооотзыв заблокирован |
| Audit log | append-only, кто что сделал, с детализированными `details` JSON |

**Пагинация**: cursor-based на больших таблицах (passages, audit) — масштабируется без `OFFSET` proблем.

**Получение первого admin API key** (bootstrap):

```bash
docker compose exec postgres psql -U scud -c "
    INSERT INTO api_keys(key_hash, key_prefix, kind, name)
    VALUES (encode(sha256('your-plaintext-here'::bytea), 'hex'), 'sk_admin', 'admin', 'bootstrap');
"
# затем используйте 'your-plaintext-here' в /admin/login
```

Альтернативно — через тестовый seed-пользователь из migration 0002 + curl на `POST /api/v1/admin/api-keys`.

**Security notes:**

- Cookie `httpOnly + SameSite=Lax`. В prod-overlay nginx добавляет HSTS.
- `SCUD_WEB_SECRET` (env-var) подписывает cookie itsdangerous-сериализатором. В dev — дефолт `dev-only-secret-CHANGE-IN-PROD`, в prod ОБЯЗАТЕЛЬНО override (см. `.env.example`).
- Open-redirect защита: `?next=` принимается только если начинается с `/admin/`.
- API keys можно создавать с UI; plaintext показывается **только** на странице после создания (нигде в БД не хранится — только sha256 hash).
- Двухфазный revoke permit'ов отражён цветовой индикацией (`active` зелёный / `revoking` жёлтый / `revoked` серый).

**Что НЕ покрыто в текущей версии (todo v1.1):**

- График проходов (Chart.js встроить, данные уже есть через `/api/v1/admin/passages/stats`).
- Live-обновление dashboard через HTMX polling.
- Управление LDAP/AD-синком.

## 4. Интеграция в инфраструктуру заказчика

> Этот раздел — про **инфраструктурную** интеграцию (БД, reverse-proxy, выгрузка
> учёта). Про встраивание SCUD как **подсистемы доступа** в чужое приложение
> (выдавать permits/keys из вашего кода, ловить события прохода, понимать офлайн
> state-sync через телефон-курьер) — отдельный документ
> [`12_integration_guide.md`](12_integration_guide.md).

### 4.1 Поверх существующего Postgres

Если у заказчика уже есть managed Postgres (RDS / Cloud SQL / on-prem кластер):

```yaml
# docker-compose.override.yml
services:
  postgres:
    deploy:
      replicas: 0     # выключаем встроенный
  app:
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:***@db.internal:5432/scud
    depends_on: !override []
  worker:
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:***@db.internal:5432/scud
    depends_on: !override []
  migrate:
    environment:
      DATABASE_URL: postgresql+asyncpg://scud:***@db.internal:5432/scud
    depends_on: !override []
```

Минимум прав для роли `scud`: `CONNECT, TEMP` на БД + `USAGE, CREATE` на схеме + `pgcrypto` extension (для `gen_random_uuid`).

### 4.2 За корпоративным reverse proxy (Traefik / Caddy / готовый nginx)

`nginx` сервис в prod-overlay убрать (`replicas: 0` или `--scale nginx=0`), `app` опубликовать на 127.0.0.1:8000 (тот же `API_BIND` из .env), фронт-прокси проксирует к нему.

### 4.3 Интеграция учёта посещений (passage_events) с табельной системой

Два варианта:

**A. CSV-выгрузка (pull, для 1С/Битрикс24):**
```
GET /api/v1/admin/passages/export.csv?since=2026-05-01T00:00:00Z&until=2026-05-31T23:59:59Z
   Header: X-Api-Key: <admin api key>
```

**B. Real-time webhook (push):** — **реализовано.**
- Таблица `webhook_subscriptions` (migration `0005`), worker-task `notify_webhook` на каждый
  `passage_event`, управление через web-панель `/admin/webhooks`.
- HMAC-SHA256 подпись тела в заголовке `X-SCUD-Signature`; подписка авто-деактивируется после
  10 подряд неудачных доставок.
- Настройка и payload — см. [`06_api_cheatsheet.md`](06_api_cheatsheet.md) §8.

### 4.4 LDAP/AD-синхронизация пользователей

Текущая модель: `users` — локальная таблица с password_hash (Argon2id). Для AD/LDAP-сценария:

1. Заводится отдельный admin-скрипт `python -m scud.admin.sync_ldap` (не входит в текущий MVP).
2. Скрипт через `ldap3` тянет пользователей, создаёт/обновляет записи `users` (с `password_hash = "*disabled"`, чтобы пароль нельзя было использовать).
3. Auth через LDAP делается в обход `/auth/login` — отдельный endpoint `/auth/ldap` (TODO).

## 5. Наблюдаемость

### 5.1 Healthchecks

- `GET /health` — простой ok-marker.
- Docker healthcheck в app-контейнере → перезапуск при 3 подряд fail'ах (30s × 3 = 90s).

### 5.2 Логи

```bash
docker compose logs -f --tail=200 app worker
```

Логи структурированы (stdlib `logging`), в prod рекомендуется отдавать в Loki/CloudWatch через docker logging driver (например, `loki` driver).

### 5.3 Метрики

Доступен `GET /metrics` — Prometheus exposition (text/plain v0.0.4). Без auth: подразумевается, что endpoint скрейпит Prometheus-инстанс внутри scud-сети, наружу не выпускается nginx-overlay'ем.

Что экспортируется:

| Метрика | Тип | Назначение |
|---|---|---|
| `scud_http_requests_total{method,path,status}` | counter | RPS, error rate (5xx / 4xx) |
| `scud_http_request_duration_seconds_bucket{method,path,le}` | histogram | p50/p95/p99 latency через `histogram_quantile()` |
| `scud_users_active` / `scud_readers_active` / `scud_permits_active` | gauge | бизнес-снапшоты на момент scrape |
| `scud_issued_keys_active` | gauge | сколько ключей сейчас валидно |
| `scud_passages_total_now` | gauge | всего проходов за всё время |
| `scud_webhooks_active` / `scud_webhooks_failing` | gauge | здоровье интеграций |

Прокидывать в Grafana — стандартно: добавить scrape job в prometheus.yml:
```yaml
scrape_configs:
  - job_name: scud-backend
    scrape_interval: 30s
    static_configs:
      - targets: ['scud-app:8000']
```

Дашборд готов не поставляется, но из метрик легко собирается: RPS по endpoint'ам, p95 латенси, тренд проходов, алерт на `scud_webhooks_failing > 0`.

## 6. Безопасность

| Уровень | Защита |
|---|---|
| TLS | nginx с LE certs |
| API auth | session_token (Bearer) для пользователей, X-Api-Key для админ-API (shared §1.2 backend-spec) |
| BLE link | cleartext, но Ed25519/X25519 поверх (shared §16.8) |
| Passage receipt | Ed25519 reader_priv (shared §15) — нельзя подделать вне ридера |
| Filter package | Ed25519 server_priv — нельзя подделать вне сервера |
| DB at rest | заказчик контролирует (Postgres-уровневое шифрование тома) |
| Secrets | `.env*` через docker secrets или mount — не в image, не в репо |

## 7. Чек-лист перед запуском в prod

- [ ] Сильный `POSTGRES_PASSWORD` в `.env.prod`.
- [ ] TLS-сертификат и privkey в `deploy/certs/`.
- [ ] `API_BIND=127.0.0.1` (если nginx внутри compose-network → можно `0.0.0.0`, потому что наружу всё равно не выйдет).
- [ ] Backup-cron на `pg_dump`.
- [ ] Все админ-ключи — отдельные на каждого админа (через `POST /api/v1/admin/api-keys`), с `name=<кто>`.
- [ ] Тестовый seed-пользователь (см. migration 0002) **удалён** из prod БД.
- [ ] Provisioning утилита (Desktop/ScudProvisioner) — на отдельной офлайн-машине, ключи ридеров никогда не покидают этот хост.
