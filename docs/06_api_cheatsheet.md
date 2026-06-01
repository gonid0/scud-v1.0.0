# 06. API Cheat Sheet

Полная справка по REST API SCUD с готовыми `curl`-командами. Цель — за 5 минут понять, как сделать типовую операцию.

Канонический контракт с типами — в OpenAPI: `Backend/openapi.yaml` + автоген в `http://<host>/docs` (Swagger UI) и `/redoc`.

Этот документ — **рецепты**, не reference. Прямые examples под copy-paste.

---

## 0. Базовые правила

| Префикс | Назначение | Auth |
|---|---|---|
| `/api/v1/app/*` | App API (для Android-приложения) | `Authorization: Bearer <session_token>` |
| `/api/v1/admin/*` | Admin API (для скриптов и интеграций) | `X-Api-Key: <admin api key>` |
| `/admin/*` | Server-rendered web-панель | Cookie session (login через `/admin/login`) |
| `/health` | Healthcheck | без auth |
| `/metrics` | Prometheus exposition | без auth (закрывать на уровне сети) |

Все ответы — JSON (UTF-8), бинарные блобы — base64.

В примерах:
```bash
export H=http://127.0.0.1:8000           # ваш сервер
export K=sk_admin_REPLACE_WITH_YOUR_KEY  # admin API key
```

---

## 1. Health & metrics

```bash
curl -s $H/health
# → {"status":"ok"}

curl -s $H/metrics | head -30
# → # HELP scud_http_requests_total Number of HTTP requests received
#   # TYPE scud_http_requests_total counter
#   scud_http_requests_total{method="GET",path="/health",status="200"} 42
#   ...
#   # HELP scud_users_active Number of active users (is_active=true)
#   ...
```

В prod metrics обычно скрейпит Prometheus с internal-network IP. Снаружи закрыть на nginx (например, basic auth или allow только private CIDR).

---

## 2. Bootstrap admin API key

Первый ключ можно создать только напрямую в БД (нет «root» сценария для chicken-and-egg). После — все остальные через API.

```bash
# Bootstrap: руками вставить в Postgres.
docker compose exec postgres psql -U scud -c "
  INSERT INTO api_keys(key_hash, key_prefix, kind, name)
  VALUES (
    encode(sha256('sk_admin_BOOTSTRAP_DEV_KEY'::bytea), 'hex'),
    'sk_admin', 'admin', 'bootstrap'
  );
"
export K=sk_admin_BOOTSTRAP_DEV_KEY

# Затем — создавать новые ключи через API:
curl -s -X POST $H/api/v1/admin/api-keys \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{"name":"ops-laptop","kind":"admin"}'
# → {"api_key_id":"...","plaintext":"sk_admin_xxxxx","key_prefix":"sk_admin"}
```

`plaintext` показывается **один раз** в ответе и нигде не хранится — потерян = нужно создать новый.

---

## 3. Пользователи

```bash
# Создать
curl -s -X POST $H/api/v1/admin/users \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{"login":"ivanov","password":"strong-pw","display_name":"Иванов И.И.",
       "user_group_id":"00000000-0000-0000-0000-000000000001"}'

# Список (cursor pagination)
curl -s "$H/api/v1/admin/users?limit=20" -H "X-Api-Key: $K"

# Конкретный пользователь
curl -s $H/api/v1/admin/users/42 -H "X-Api-Key: $K"
```

---

## 4. Reader groups + Readers

```bash
# Создать группу
curl -s -X POST $H/api/v1/admin/reader-groups \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{"name":"Этаж 3","description":"Третий этаж главного корпуса"}'
# → {"group_id":"..."}

# Enroll нового ридера (после provisioning)
curl -s -X POST $H/api/v1/admin/readers/enroll \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{
    "reader_id":"0123456789abcdef0123456789abcdef",
    "reader_pubkey":"BASE64_32B_ED25519_PUBKEY",
    "reader_group_id":"GROUP-UUID",
    "display_name":"Турникет холла"
  }'
# → {"reader_id":"...","server_ed25519_pubkey":"...","server_x25519_pubkey":"...","reader_group_id":"..."}
# Эти server_*_pubkey прошить в NVS ридера через ScudProvisioner.
```

---

## 5. Permits

### 5.1 Создание вручную

```bash
curl -s -X POST $H/api/v1/admin/permits \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{
    "user_id":42,
    "reader_id":"0123456789abcdef0123456789abcdef",
    "display_name":"Иванов → Турникет холла",
    "valid_from":"2026-05-19T09:00:00Z",
    "valid_until":"2027-05-19T09:00:00Z",
    "n_parallel":1,
    "max_token_ttl_seconds":86400
  }'
```

### 5.2 Через шаблон (день/неделя/месяц/год/...)

```bash
# Посмотреть доступные шаблоны
curl -s $H/api/v1/admin/permits/templates -H "X-Api-Key: $K"

# Выдать «годовой» permit одной командой
curl -s -X POST $H/api/v1/admin/permits/issue-from-template \
  -H "X-Api-Key: $K" -H "Content-Type: application/json" \
  -d '{
    "user_id":42,
    "reader_id":"0123456789abcdef0123456789abcdef",
    "template_id":"year",
    "display_name":"Иванов И.И. — годовой"
  }'
# → {"permit_id":"...","valid_from":"...","valid_until":"...","template":"year"}
```

### 5.3 Двухфазный admin revoke

```bash
curl -s -X POST $H/api/v1/admin/permits/PERMIT-UUID/revoke -H "X-Api-Key: $K"
# → {"ok":true,"status":"revoking","revoke_initiated_at":"...","revoked_at":null}
# ... через какое-то время, после применения bloom на ридере:
curl -s $H/api/v1/admin/permits/PERMIT-UUID -H "X-Api-Key: $K"
# → {"status":"revoked","revoked_at":"..."}
```

---

## 6. Issued keys

```bash
# Список (фильтры опциональны)
curl -s "$H/api/v1/admin/keys?status=active&limit=50" -H "X-Api-Key: $K"

# Force-revoke конкретный ключ (через сервер → попадает в bloom)
curl -s -X POST $H/api/v1/admin/keys/KEY-ID-HEX/revoke -H "X-Api-Key: $K"
```

---

## 7. Проходы (passage_events) — учёт

```bash
# Список с фильтрами + cursor pagination
curl -s "$H/api/v1/admin/passages?user_id=42&limit=100" -H "X-Api-Key: $K"

# Агрегаты (для дашборда)
curl -s "$H/api/v1/admin/passages/stats?group_by=reader" -H "X-Api-Key: $K"
curl -s "$H/api/v1/admin/passages/stats?group_by=user" -H "X-Api-Key: $K"
curl -s "$H/api/v1/admin/passages/stats?group_by=day&since=2026-05-01T00:00:00Z" \
  -H "X-Api-Key: $K"

# CSV-выгрузка для табельной системы
curl -s "$H/api/v1/admin/passages/export.csv?since=2026-05-01T00:00:00Z" \
  -H "X-Api-Key: $K" -o passages.csv
```

---

## 8. Webhooks (интеграция с CRM/ERP)

Webhooks управляются **только через web-панель** (`/admin/webhooks`, cookie-сессия) —
отдельного JSON admin-API (`/api/v1/admin/webhooks`) для них нет. Создание — form-POST
из панели (поля `name`, `url`, `event_types`, `secret`):

```bash
# Создать подписку (form-POST, cookie-сессия админ-панели)
curl -s -X POST $H/admin/webhooks \
  -H "Cookie: scud_admin_session=..." \
  --data-urlencode "name=crm-passage-sync" \
  --data-urlencode "url=https://crm.example.com/scud/hook" \
  --data-urlencode "event_types=passage_event" \
  --data-urlencode "secret=shared-secret-for-hmac"

# Список — страница /admin/webhooks
# Test fire (smoke-проверка приёмника без реального прохода):
curl -s -X POST $H/admin/webhooks/WEBHOOK-UUID/test \
  -H "Cookie: scud_admin_session=..."
```

**Payload (для receiver'а):**
```json
{
  "event_type": "passage_event",
  "webhook_id": "...",
  "data": {
    "event_id": "uuid",
    "reader_id": "hex",
    "key_id": "hex",
    "permit_id": "uuid",
    "user_id": 42,
    "phone_pubkey": "hex",
    "passed_at": "2026-05-19T10:11:12+00:00",
    "direction": 1,
    "session_seq": 7
  }
}
```

**HMAC signature check** на стороне приёмника (если secret задан):
```python
import hmac, hashlib
expected = "sha256=" + hmac.new(
    secret.encode(), raw_body, hashlib.sha256
).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-SCUD-Signature"])
```

---

## 9. App API (для приложения)

### 9.1 Login + register device

```bash
curl -s -X POST $H/api/v1/app/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login":"ivanov","password":"strong-pw"}'
# → {"session_token":"...","refresh_token":"...","user":{...}}

export T="Authorization: Bearer SESSION_TOKEN"

curl -s -X POST $H/api/v1/app/auth/register-device \
  -H "$T" -H "Content-Type: application/json" \
  -d '{"phone_pubkey":"BASE64_32B","device_label":"Samsung S22"}'
# → {"device_id":"..."}
```

### 9.2 Запрос ключа

```bash
curl -s -X POST $H/api/v1/app/keys/request \
  -H "$T" -H "Content-Type: application/json" \
  -d '{
    "permit_id":"PERMIT-UUID",
    "validity_seconds":86400,
    "request_grant":false
  }'
# → {"issued_key":{"key_id":"...","full_key_bytes":"BASE64_151B","issued_at":"...","expires_at":"..."},
#    "time_grant":null}
```

- `validity_seconds` — желаемый TTL ключа (сервер кламнет по `max_token_ttl_seconds` permit'а).
- `request_grant:true` → в ответе появляется `time_grant` (подписанный `time_authority_grant`
  для оффлайн-синхронизации часов ридера): `{grant_id, full_grant_bytes, expires_at}`.
- `issued_at_ts` (опц., unix-секунды) — future-dated ключ (UI date-picker «start date»).

### 9.3 Отправить отчёты с тапа

```bash
# После tap-сессии phone накапливает delivery_receipt / FDI / BLK / passage_receipt
# и заливает их пакетом:
curl -s -X POST $H/api/v1/app/reports/submit \
  -H "$T" -H "Content-Type: application/json" \
  -d '{
    "reports":[
      {"type":"passage_receipt","target_reader_id":"hex","bytes":"BASE64_192B"},
      {"type":"filter_delivery_info","target_reader_id":"hex","bytes":"BASE64_241B"}
    ]
  }'
# → {"accepted":["report-uuid", ...], "rejected":[]}
```

---

## 10. Полезные one-liner'ы

```bash
# Активных permits на ридере (для backup-аудита):
curl -s "$H/api/v1/admin/permits?reader_id=HEX&limit=200" -H "X-Api-Key: $K" \
  | jq '.items[] | select(.status=="active") | {user_id, display_name, valid_until}'

# Топ-5 пользователей за неделю:
curl -s "$H/api/v1/admin/passages/stats?group_by=user&since=$(date -u -d '7 days ago' +%FT%TZ)" \
  -H "X-Api-Key: $K" | jq '.buckets[:5]'

# Все ridenders, которые не контактировали > 48 часов (через метрику стейл):
curl -s "$H/api/v1/admin/readers?limit=500" -H "X-Api-Key: $K" \
  | jq '.items[] | select(.last_contact_at == null or .last_contact_at < (now - 172800 | strftime("%Y-%m-%dT%H:%M:%SZ")))'

# Реактивировать deactivated webhook (после фикса receiver'а):
curl -s -X POST $H/admin/webhooks/UUID/toggle -H "Cookie: scud_admin_session=..."
```

---

## 11. Error reference

Все ошибки — JSON `{"detail":"machine_readable_code"}` + HTTP-статус. Типичные:

| HTTP | code | смысл |
|---|---|---|
| 401 | `missing_token` / `invalid_token` / `missing_api_key` / `invalid_api_key` | auth fail |
| 403 | (rare) | прав не хватает (например, integration key пытается admin-action) |
| 404 | `user_not_found` / `reader_not_found` / `permit_not_found` / `template_not_found:X` | нет такого ресурса |
| 409 | `login_taken` / `reader_id_exists` / `group_has_readers` | конфликт уникальности / FK |
| 422 | `invalid reader_id hex` / `invalid kind` | validation |

---

## 12. Где смотреть больше

- **Полный OpenAPI**: `http://<host>:8000/docs` (Swagger UI).
- **YAML спецификация**: `Backend/openapi.yaml`.
- **Бизнес-логика** (что эти endpoints значат для процесса): [`05_business_logic.md`](05_business_logic.md).
- **Развёртывание**: [`04_deployment.md`](04_deployment.md).
- **Протокол ридер↔телефон** (если вы пишете другую реализацию app): [`00_shared_protocol.md`](00_shared_protocol%20%281%29.md).
