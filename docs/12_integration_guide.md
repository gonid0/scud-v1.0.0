# 12. Встраивание SCUD в существующую систему

Этот документ — для **интегратора**: вы уже эксплуатируете какую-то систему (HR/табельная,
CRM, 1С, BMS, корпоративный портал) и хотите, чтобы доступом через двери/турникеты управляла
SCUD, а ваша система — выдавала пропуска и получала события прохода.

Здесь — **контракт встраивания**: какую REST-поверхность дёргать, как ловить события через
webhooks, и — главное — как устроена **офлайн-синхронизация через телефон-курьер**, потому что
именно она ломает привычную модель «онлайн-СКУД» и определяет, что вы получаете из коробки, а что
обязаны учесть на своей стороне.

Связанные документы:
- [`06_api_cheatsheet.md`](06_api_cheatsheet.md) — готовые `curl` под каждый вызов.
- [`05_business_logic.md`](05_business_logic.md) — что эти вызовы значат для процесса.
- [`04_deployment.md`](04_deployment.md) — как поднять и куда встроить инфраструктурно.
- `Backend/openapi.yaml` + `/docs` (Swagger) — канонический контракт с типами.

---

## 1. Модель интеграции за 60 секунд

SCUD — **self-hosted офлайн-СКУД**. Ключевой инвариант: **ридер никогда не выходит в интернет**.
Между сервером и ридером всегда курьер — телефон сотрудника (data-mule).

```
   ВАША СИСТЕМА                          SCUD backend                  Физический мир
   ┌──────────┐   REST (X-Api-Key)      ┌──────────────┐
   │  HR/CRM/ │ ──── issue permit ────▶ │              │
   │   1С /   │ ──── issue key   ─────▶ │  PostgreSQL  │   filter ↓  ↑ receipt
   │  портал  │ ◀─── webhook ────────── │  + worker    │      (через телефон-курьер)
   └──────────┘   passage event push    └──────────────┘
        ▲                                                       ┌─────────┐   ┌──────┐
        │  CSV pull (учёт)                            NFC/BLE   │ ESP32   │──▶│ Lock │
        └──────────────────────────── телефон ◀──────tap──────▶│ reader  │   └──────┘
                                       (data-mule)             └─────────┘
```

Три канала, которые видит интегратор:

| Канал | Направление | Транспорт | Когда |
|---|---|---|---|
| **Admin REST** (`/api/v1/admin/*`) | ваша система → SCUD | HTTP, `X-Api-Key` | выдать/отозвать пропуск, прочитать события |
| **Webhooks** | SCUD → ваша система | HTTP POST, HMAC-подпись | проход случился (push) |
| **CSV-выгрузка** | ваша система ← SCUD | HTTP GET | пакетный учёт (pull, для табельных) |

App API (`/api/v1/app/*`) — это контракт **для телефона**, не для вашей серверной интеграции.
Вы его обычно НЕ дёргаете (телефон сотрудника делает это сам). Он описан здесь только чтобы вы
понимали поток данных.

---

## 2. Что вы получаете из коробки vs что строите сами

| | Из коробки (SCUD даёт) | Строите вы (интегратор) |
|---|---|---|
| **Идентичность** | User + login/пароль (Argon2id), UserDevice (Ed25519) | маппинг ваш_сотрудник ↔ SCUD `user_id` |
| **Выдача доступа** | Permit/IssuedKey, шаблоны (day/week/.../year) | *когда* и *кому* выдавать (ваша бизнес-логика) |
| **Решение о проходе** | офлайн на ридере (bloom + blacklist + crypto) | — |
| **Учёт проходов** | `passage_events`, REST-чтение, CSV, webhooks | приёмник webhook'ов / импорт CSV |
| **Отзыв** | три пути (server/phone/permit), двухфазный | триггер отзыва из вашей системы (HTTP вызов) |
| **Доставка фильтров на ридер** | DeliveryTask + courier API + телефон | — (если у вас НЕ свой app — см. §6) |
| **Синхронизация телефона** | весь App API + Android-приложение | ничего, если используете штатный app |
| **Подписи/крипто** | Ed25519/X25519/bloom, byte-exact протокол | ничего (не трогайте протокол) |

Главный вывод: **если вы используете штатный Android-app, вся офлайн-синхронизация уже решена.**
Ваша интеграция сводится к двум вещам: (1) выдавать/отзывать пропуска через Admin REST,
(2) принимать события прохода через webhook или CSV. Всё остальное — внутренняя механика SCUD.

---

## 3. Аутентификация интеграции

Создайте отдельный API-ключ под вашу систему (не используйте bootstrap/персональные admin-ключи):

```bash
curl -s -X POST $H/api/v1/admin/api-keys -H "X-Api-Key: $K" \
  -H "Content-Type: application/json" \
  -d '{"name":"hr-integration","kind":"admin"}'
# → {"api_key_id":"...","plaintext":"sk_admin_xxxxx", ...}
#   plaintext показывается ОДИН раз — сохраните в свой secret-store.
```

- Все админ-вызовы: заголовок `X-Api-Key: <plaintext>`.
- Ключи хранятся как SHA256-hash; компрометация ключа → `POST /admin/api-keys/{id}/revoke` мгновенно его убивает.
- `kind` бывает `admin` (полный доступ) и `integration` (зарезервировано под урезанные права).
- Webhook'и управляются **только из web-панели** (`/admin/webhooks`, cookie-сессия) — отдельного
  JSON-API под них нет (см. §5).

---

## 4. REST-поверхность: выдать пропуск и прочитать события

Полные `curl` — в [`06_api_cheatsheet.md`](06_api_cheatsheet.md). Здесь — какие вызовы нужны
интегратору и в каком порядке.

### 4.1 Онбординг сотрудника

```
POST /api/v1/admin/users         { login, password, display_name, user_group_id }
   → user_id
```
Сотрудник дальше сам логинится в Android-app и регистрирует устройство (`/app/auth/*`) — это вы
не делаете. Сохраните у себя связь `ваш_employee_id ↔ SCUD user_id`.

### 4.2 Выдать пропуск (permit)

Permit = право пользователя на один конкретный ридер в окне времени. Два способа:

```
# вручную (полный контроль над окном)
POST /api/v1/admin/permits
   { user_id, reader_id, valid_from, valid_until, n_parallel, max_token_ttl_seconds }

# по шаблону (day/week/month/quarter/year/dual_device)
POST /api/v1/admin/permits/issue-from-template
   { user_id, reader_id, template_id:"year", display_name }
```

Поля, которые управляют поведением:
- `n_parallel` — сколько одновременно активных ключей (телефонов) на этот permit (обычно 1, для двух устройств — 2).
- `max_token_ttl_seconds` — потолок TTL каждого выпуска ключа (сервер клампит запрос телефона).
- `max_total_issued` (nullable) — квота на число ещё-не-истёкших ключей permit'а (`expires_at > now`, любой статус, NULL = без лимита). Защита bloom-фильтра отзыва от раздувания: ограничивает, сколько ключей permit'а одновременно находятся в окне валидности; истёкшие ключи слот освобождают (ридер отбрасывает их по сроку, в фильтр они не попадают). Имя историческое — лимит больше **не** пожизненный.

> Permit ≠ key. Permit — это **право**. Конкретный 151-байтовый подписанный ключ телефон
> запрашивает сам (`POST /app/keys/request`) — вам это дёргать не нужно.

### 4.3 Отозвать доступ

```
# отозвать весь permit (двухфазно: active → revoking → revoked)
POST /api/v1/admin/permits/{permit_id}/revoke

# отозвать один выпущенный ключ
POST /api/v1/admin/keys/{key_id}/revoke
```

**Критично для интегратора — окно отзыва.** Отзыв на сервере мгновенен в БД, но физически
включается только когда новый bloom-фильтр **доедет до ридера через телефон-курьера**. До тех пор
permit в статусе `revoking`, ключ — `revoked_by_server` (ридер его ещё принимает). Время реакции =
до следующего тапа любого курьера на этот ридер. Это не баг, а свойство офлайн-модели (см. §6 и
[`05_business_logic.md` §8.1](05_business_logic.md)). Если ваша система требует «отозвал → мгновенно
закрыто», офлайн-СКУД для этой двери не подходит — нужен онлайн-ридер или физический барьер.

### 4.4 Прочитать события прохода (pull)

```
GET /api/v1/admin/passages?user_id=42&since=...&limit=100      # cursor-пагинация
GET /api/v1/admin/passages/stats?group_by=reader|user|day      # агрегаты
GET /api/v1/admin/passages/export.csv?since=...&until=...      # CSV для табельной
```

`passage_event` приходит на сервер постфактум (телефон донёс квитанцию) — у него есть `passed_at`
(когда реально прошли) отдельно от момента доставки. Учёт надёжен, но **с задержкой**: проход
виден серверу только после online-синхронизации телефона.

### 4.5 Карта Admin REST (что есть)

| Группа | Эндпоинты (под `/api/v1/admin`) |
|---|---|
| Users | `POST/GET /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/reset-password` |
| Reader groups | `POST/GET /reader-groups`, `PATCH/DELETE /reader-groups/{id}` |
| Readers | `POST /readers/enroll`, `GET /readers`, `GET/PATCH /readers/{id}`, `GET /readers/{id}/config-script` |
| Reader profiles | `GET/POST /reader-profiles`, `GET/PATCH/DELETE /reader-profiles/{id}` (параметризация ридеров, docs/11) |
| Permits | `POST/GET /permits`, `GET/PATCH /permits/{id}`, `GET /permits/templates`, `POST /permits/issue-from-template`, `POST /permits/{id}/revoke` |
| Keys | `GET /keys`, `GET /keys/{id}`, `POST /keys/{id}/revoke` |
| API keys | `POST/GET /api-keys`, `POST /api-keys/{id}/revoke` |
| Passages (read-only) | `GET /passages`, `GET /passages/stats`, `GET /passages/export.csv` |
| Observability | `GET /readers/{id}/delivery-status`, `GET /background-tasks`, `GET /audit-log` |

---

## 5. Webhook-модель (push событий в вашу систему)

Вместо polling'а `/passages` — подпишитесь на события. SCUD пушит HTTP POST на ваш URL при
каждом `INSERT passage_events`.

### 5.1 Создание подписки

Webhook'и создаются **из web-панели** (`/admin/webhooks`, cookie-сессия админа) — отдельного
JSON-admin-API под них нет. Form-POST поля: `name`, `url`, `event_types`, `secret`:

```bash
curl -s -X POST $H/admin/webhooks \
  -H "Cookie: scud_admin_session=..." \
  --data-urlencode "name=hr-passage-sync" \
  --data-urlencode "url=https://hr.example.com/scud/hook" \
  --data-urlencode "event_types=passage_event" \
  --data-urlencode "secret=shared-hmac-secret"
# smoke-проверка приёмника без реального прохода:
#   POST /admin/webhooks/{id}/test
```

### 5.2 Payload, который получит ваш приёмник

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

### 5.3 Проверка HMAC-подписи (если задан secret)

Заголовок `X-SCUD-Signature: sha256=<hex>`; тело подписано HMAC-SHA256 общим секретом:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-SCUD-Signature"])
```

### 5.4 Контракт доставки (что обязан гарантировать приёмник)

- **Отвечайте 2xx быстро.** 2xx → `consecutive_failures=0`. Любой не-2xx или таймаут — failure.
- **10 подряд неудач → подписка авто-деактивируется** (`is_active=false`). Реактивация:
  `POST /admin/webhooks/{id}/toggle` после фикса приёмника.
- **Идемпотентность на вашей стороне.** Доставка at-least-once (worker retry/backoff). Дедуп — по
  `data.event_id` (он же уникален в SCUD через `(reader_id, receipt_nonce)`).
- **Порядок не гарантирован.** Опирайтесь на `passed_at`, не на порядок прихода.
- Сейчас тип события один — `passage_event`. Модель fan-out расширяема (`event_types` —
  список), но other-event-типы — задел на будущее.

---

## 6. ⚑ Офлайн state-sync через телефон-курьер (самое важное)

Это то, что отличает SCUD от классической онлайн-СКУД и что интегратор обязан понять, чтобы не
строить неверных ожиданий. **Ридер офлайн. Сервер и ридер никогда не связаны напрямую. Носитель
состояния в обе стороны — телефон сотрудника.**

### 6.1 Два потока через курьера

```
ВНИЗ (сервер → ридер): отзывы/фильтры
  1. Админ/интеграция отзывает ключ/permit → worker генерит filter_package v+1
     (подписан server_priv этого ридера), создаёт DeliveryTask.
  2. Телефон, будучи online, видит задачу:  GET  /app/courier/available
                                            POST /app/courier/download  → 151+ B пакет в Room.
  3. При следующем NFC/BLE-тапе телефон отдаёт пакет ридеру (FILTER_UPDATE).
     Ридер проверяет подпись + монотонность версии → атомарный swap фильтра.
  4. Ридер возвращает delivery_receipt → телефон донесёт его серверу (поток ВВЕРХ).
     Сервер: revoked_by_server → revoked_in_bloom (отзыв «закоммичен»).

ВВЕРХ (ридер → сервер): квитанции/отчёты
  1. На ACCESS=OK ридер кэширует passage_receipt (192 B, подпись reader_priv).
  2. Телефон забирает её в той же tap-сессии (GET_PASSAGE_RECEIPT).
  3. Накопленные блобы (passage_receipt / FDI / blacklist / delivery_receipt)
     уходят пакетом:  POST /app/reports/submit  (base64, retry до 5×).
  4. Worker (process_report): verify reader-подписи → sanity passed_at →
     dedup (reader_id, receipt_nonce) → INSERT passage_events → webhook fan-out.
```

### 6.2 Что это значит для интегратора (следствия)

- **Согласованность — eventual, не мгновенная.** «Выдал/отозвал» в БД ≠ «применилось на двери».
  Применение наступает на следующем тапе курьера. Для отзыва это **окно риска** (§4.3).
- **Любой сотрудник — курьер.** Доставка фильтра не требует прав владельца ключа: чем чаще ходят
  люди, тем быстрее доезжают отзывы. Митигирует окно отзыва.
- **Учёт зависит от доброй воли владельца.** Только владелец ключа получает свою `passage_receipt`
  и только он решает донести её (приватность). Если нужен жёсткий учёт «вошёл/не вошёл» —
  нужен физический барьер (вертушка со счётчиком), SCUD его не заменяет.
- **Подделать ничего нельзя.** `filter_package` подписан server_priv, `passage_receipt` —
  reader_priv, ACCESS — phone_priv с одноразовым nonce. Курьер — тупой транспорт байтов, он
  ничего не может сфабриковать (инварианты I4–I8, I10).
- **Мониторинг «протухших» ридеров.** Ридер, на который давно никто не тапал, держит старый
  фильтр. Следите за `GET /admin/readers/{id}/delivery-status` и метрикой stale-readers — это
  ваш индикатор, что отзыв ещё не доехал.

### 6.3 Нужен ли вам свой курьер?

Почти никогда. Решающая развилка:

| Сценарий | Что делать |
|---|---|
| Сотрудники носят штатный **Android-app** | **Ничего.** Синхронизация уже работает из коробки. Ваша интеграция — только Admin REST + webhooks. |
| Свой телефонный клиент / другая ОС | Реализуйте App API (`/app/*`) + протокол ридера byte-exact по [`00_shared_protocol`](00_shared_protocol%20%281%29.md). Это большая работа (3 крипто-реализации сверяются golden-векторами). |
| Нужен «киоск-курьер» (выделенное устройство у двери) | Тот же App API, но устройство постоянно online и периодически тапает ридеры — ускоряет доставку отзывов. |

App API, который реализует курьер (для справки):
`POST /app/auth/login` · `POST /app/auth/register-device` · `POST /app/keys/request` ·
`GET /app/courier/available` · `POST /app/courier/download` · `POST /app/reports/submit`.

---

## 7. Инфраструктурная интеграция (коротко)

Детали — в [`04_deployment.md` §4](04_deployment.md). Кратко:

- **Свой Postgres** (RDS/Cloud SQL/on-prem): выключить встроенный сервис, задать `DATABASE_URL`
  для `app`/`worker`/`migrate`. Роли `scud` нужны `CONNECT/TEMP` + `USAGE/CREATE` на схеме +
  `pgcrypto`.
- **Свой reverse-proxy** (Traefik/Caddy/корпоративный nginx): убрать nginx side-car из prod-overlay,
  проксировать на `app:8000`.
- **Учёт в табельную**: CSV-pull (`/passages/export.csv`) или webhook-push (§5).
- **Метрики**: `GET /metrics` (Prometheus) — RPS/latency + бизнес-gauges (active users/readers/
  permits/keys, passages_total, webhooks active/failing). Закрывается на уровне сети.

---

## 8. Типовой рецепт интеграции (HR-система, end-to-end)

```
1. [разово] Создать integration API key (§3). Поднять группы ридеров под зоны/этажи.
2. [разово] Поднять webhook-подписку на passage_event → ваш /scud/hook (§5).
3. [на найм]   POST /admin/users               → сохранить ваш_emp_id ↔ user_id.
4. [на доступ] POST /admin/permits/issue-from-template (year) на нужный reader_id.
5. [сотрудник] ставит Android-app, логинится, регистрирует телефон, качает ключ — сам.
6. [проход]    телефон ↔ ридер (NFC/BLE) → замок; квитанция доедет до сервера позже.
7. [учёт]      ваш приёмник ловит webhook passage_event (или ночной CSV-pull).
8. [увольнение] POST /admin/permits/{id}/revoke → фильтр доедет до ридеров с курьерами.
                (для «мгновенно» — помните про окно отзвыва §4.3 / §6.2.)
```

---

## 9. Чек-лист готовности интеграции

- [ ] Отдельный `integration` API key, plaintext в secret-store, bootstrap-ключ отозван.
- [ ] Webhook-приёмник: проверяет `X-SCUD-Signature`, идемпотентен по `event_id`, отвечает 2xx быстро.
- [ ] Маппинг `ваш_employee_id ↔ SCUD user_id` хранится у вас.
- [ ] Бизнес-логика учитывает **окно отзыва** (eventual, не мгновенно) — §4.3/§6.2.
- [ ] Мониторинг stale-ридеров (`/readers/{id}/delivery-status`) — индикатор недоставленных отзывов.
- [ ] Решено: штатный Android-app (ничего не строим) vs свой курьер (реализуем App API+протокол).
- [ ] Если важен жёсткий учёт присутствия — есть физический барьер; SCUD-passage_receipt опциональна по природе.
