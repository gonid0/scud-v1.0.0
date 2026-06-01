# 05. Бизнес-логика SCUD

Этот документ — **полная карта бизнес-логики системы**. Здесь объясняется, *что* и *почему* делает каждый компонент, какие сущности и состояния существуют, как происходят основные процессы (онбординг, выдача ключа, проход, отзыв), и какие инварианты защищают целостность системы.

Технические детали байтовых форматов и крипто — в [`00_shared_protocol.md`](00_shared_protocol%20%281%29.md). Детали развёртывания — в [`04_deployment.md`](04_deployment.md). Здесь — **логика** и **процессы**.

---

## 1. Что такое SCUD и зачем

**SCUD** — система контроля и управления доступом (СКУД). Назначение: пускать (или не пускать) людей через физические преграды — двери, турникеты, шлагбаумы — на основании пропусков, выданных администратором.

### 1.1 Главное отличие от классических СКУД

Классическая СКУД: ридер постоянно онлайн, при каждом тапе обращается к серверу за решением «пускать / не пускать». Минусы — простой сервера или сети = заблокированные двери, нужна проводка в каждый ридер.

**SCUD-инвариант**: **ридер никогда не выходит в интернет**. Он работает офлайн с локальным белым/чёрным списком, обновляемым периодически. Связку «ридер ↔ сервер» обеспечивает телефон пользователя как `data-mule`:

```
[Server] ←HTTPS→ [Android] ←NFC tap / BLE→ [Reader] →GPIO→ [Lock]
```

Любой проход на ридер: ридер сам решает (есть ли ключ в bloom-фильтре, не отозван ли). Сервер постфактум получает квитанции через тех же телефонов.

### 1.2 Почему это полезно бизнесу

- **Отказоустойчивость**: проблемы на сервере / в сети не блокируют физический проход.
- **Простая инфраструктура**: ридеру нужно только питание (12 В / батарея). Не нужен Ethernet, Wi-Fi, GSM-модем.
- **Self-hosted**: вся БД у заказчика, ничего не уходит в сторонние SaaS.
- **Учёт посещений (passage_receipt)** автоматически: каждый проход — подписанная ридером квитанция, доставляется на сервер при следующей синхронизации телефона.

---

## 2. Акторы и роли

| Актор | Роль | Где живёт |
|---|---|---|
| **Сотрудник** | владелец ключа, пользователь Android-app | у пользователя |
| **Администратор** | выдаёт пропуска, отзывает, мониторит проходы | web-панель или JSON API |
| **Курьер** | роль того же сотрудника — переносит filter-обновления на ридер | свойство `phone` |
| **Authority** | владелец «time grant» — телефон, имеющий право переподписывать время ридеру | свойство `phone` (выдаётся отдельно) |
| **Ridader / Reader** | железо у двери, проверяет ACCESS, открывает замок | в стене / на турникете |
| **Backend** | хранит данные, выпускает ключи, валидирует квитанции | self-hosted сервер |
| **External system** | CRM, табельная, 1C — получает события через webhooks | за периметром |

Один телефон одного сотрудника одновременно может быть и владельцем ключа, и курьером, и time authority — это разные **права**, не разные **сущности**.

---

## 3. Доменные сущности

### 3.1 User (пользователь)

Сотрудник, имеющий доступ в систему. Поля:
- `user_id` (int, авто)
- `login` (уникален) + `password_hash` (Argon2id)
- `display_name` — человеческое имя
- `user_group_id` (UUID) — логическая группа (этаж, отдел) для bulk-операций
- `is_active` (bool) — деактивированные не могут логиниться

**Жизненный цикл**:
```
создан → активный ←→ деактивированный
                          ↓
                     (логически удалён — данные остаются для аудита)
```

### 3.2 UserDevice (устройство пользователя)

Один пользователь = много устройств (телефон + планшет + ...). Каждое имеет свою ed25519-пару (приватный ключ хранится в Android Keystore, не уходит наружу).

Поля: `device_id` (UUID v4), `user_id`, `phone_pubkey` (32 B), `device_label`, `registered_at`, `is_active`.

### 3.3 Reader (ридер)

Физическое устройство — ESP32 + NFC PN532 + DS3231 RTC, опционально BLE. Поля:
- `reader_id` (16 B) — UUID v4, прошивается в NVS на provisioning
- `reader_group_id` — для bulk-выдачи permits на «этаж»
- `display_name` (например «Турникет холла»)
- `reader_pubkey` (32 B) — ed25519 публичный ключ ридера (verify-key)
- `server_ed25519_priv/pub`, `server_x25519_priv/pub` — server-side ключи для подписи / sealed-box обмена
- `is_active`, `last_contact_at`, `last_known_time`, `last_applied_filter_version`

**Provisioning** — отдельная процедура: Desktop/ScudProvisioner генерирует пару ключей ридера, прошивает в NVS, и регистрирует в backend через `POST /admin/readers/enroll`.

### 3.4 ReaderGroup (группа ридеров)

Логический контейнер: «этаж», «корпус», «парковка». У permit обязан быть один конкретный ридер; группа — для bulk-операций.

### 3.5 Permit (пропуск)

Право пользователя на доступ к одному конкретному ридеру в течение временного окна. Поля:
- `permit_id` (UUID v4)
- `user_id` + `reader_id`
- `display_name`, `description`
- `valid_from`, `valid_until` — окно действия (например «на год»)
- `n_parallel` — сколько одновременно активных ключей может иметь пользователь по этому permit (часто = 1, но для сотрудников с двумя устройствами = 2)
- `max_token_ttl_seconds` — лимит TTL для каждого отдельного выпуска ключа (например 24 часа)
- `revoke_initiated_at`, `revoked_at` — двухфазный revoke (см. §6.4)

**Статус**:
- `active` — `revoked_at` IS NULL и `revoke_initiated_at` IS NULL
- `revoking` — `revoke_initiated_at` IS NOT NULL, `revoked_at` IS NULL (ждём пока ридер применит фильтр)
- `revoked` — `revoked_at` IS NOT NULL

### 3.6 IssuedKey (выданный ключ)

Конкретный 151-байтовый подписанный сервером блоб, который phone предъявляет ридеру. Один permit может породить много ключей (за счёт повторных запросов и `n_parallel`).

Поля:
- `key_id` (16 B) — `BLAKE2s(reader_id ‖ phone_pubkey ‖ issued_at_LE ‖ serial_LE)` — детерминистический
- `permit_id`, `reader_id`, `device_id`
- `phone_pubkey` (32 B) — устройство, для которого выпущен
- `issued_at`, `expires_at`
- `serial` — счётчик в рамках permit (для unique key_id'ов)
- `status` — `active | revoked_by_server | revoked_by_reader | revoked_in_bloom | expired`
- `is_active` (bool, derived) — `status NOT IN (revoked_by_reader, revoked_in_bloom, expired)`
- `full_key_bytes` (151 B) — полный сериализованный + подписанный ключ

**Жизненный цикл**: см. §6.

### 3.7 FilterPackage (bloom-фильтр отзывов)

Подписанный сервером пакет, который ридер применяет, чтобы быстро решать «отозван ли key_id»:
- bloom-фильтр, размер которого выбирается **per-reader** под популяцию отзывов конкретного ридера (target FP rate = 0.001), с верхним потолком `filter_max_bloom_bytes` (по умолчанию ~100 KB; §3.4)
- whitelist (до 256 записей) — false-positive «искупители»
- blacklist_delta — список ключей, гарантированно отозванных в этом окне

Параметры:
- `filter_version` — монотонно растёт. Ридер принимает только новее текущей.
- `m_bits`, `k_hashes`, `hash_seed` — задаются сервером
- `generated_at` — timestamp генерации (для аудита)
- Подписан `server_ed25519_R_priv` (отдельный для каждого ридера)

### 3.8 DeliveryTask (задача доставки фильтра)

Внутренняя task для отслеживания «сгенерирован filter_version vN для ридера R, кто-то должен его донести». Закрывается приходом `delivery_receipt` или `FDI` для соответствующей версии.

### 3.9 TimeGrant + TimeSyncStatement (синхронизация времени)

Ридер не имеет интернета → его DS3231 RTC дрейфует. Синхронизация:
- Сервер выпускает `time_authority_grant` для конкретного устройства (telephone становится «time authority» этого ридера).
- При большом drift авторизованный phone подписывает `time_sync_statement` со свежим UTC временем, ридер принимает и обновляет RTC.

Бывает `soft` (только небольшие коррекции) и `hard` (любое значение — для bootstrap после полного reset).

### 3.10 PassageEvent (квитанция о проходе)

Подписанная ридером 192-байтовая квитанция — выдаётся phone'у в ту же tap-сессию, в которой произошёл успешный ACCESS. Phone доставляет её на сервер при следующей синхронизации.

Поля: `event_id`, `reader_id`, `receipt_nonce` (dedup), `key_id`, `permit_id`, `user_id` (резолвится из permit), `passed_at`, `direction` (entry/exit/unknown), `session_seq`, `verdict`, `flags`, `raw_receipt` (192 B blob).

Уникальность: `(reader_id, receipt_nonce)` — реплей одной квитанции с нескольких устройств идемпотентен.

### 3.11 ReaderReport (входящий отчёт от ридера через courier)

Все байтовые блобы, доставленные phone'ом на `/app/reports/submit`, складываются в эту таблицу и асинхронно процессятся worker'ом:
- `delivery_receipt` (112 B) — ридер подтвердил применение фильтра
- `filter_delivery_info` (241 B, FDI) — ридер докладывает «у меня filter_v=X»
- `blacklist_report` (variable) — список ключей в local_blacklist
- `passage_receipt` (192 B) — квитанция о проходе

### 3.12 WebhookSubscription

Подписка внешней системы (CRM, табельная) на события. POST на URL с опциональной HMAC-SHA256 подписью. После 10 подряд неудач автоматически деактивируется.

### 3.13 ApiKey / Session

- `ApiKey` — длинный токен (`sk_admin_*`, `sk_integration_*`) для скриптов / интеграций / admin web-панели.
- `Session` — короткий cookie/bearer для пользовательского app, выдаётся после `/auth/login`.

### 3.14 AdminAuditLog

Append-only журнал админ-действий. Никогда не редактируется; используется для расследований и compliance.

---

## 4. Жизненные циклы (state machines)

### 4.1 IssuedKey

```
                  ┌──── (key_id попал в bloom через filter v ≥ committed_version)
                  ↓
        ┌──→ revoked_by_server ───┐
        │                          ├──→ revoked_in_bloom (терминальное)
        │                          │
   active                          │
        │   ┌──→ revoked_by_reader ┘  (приход BLK с этим key_id)
        │   │
        └───┴──→ expired  (now > expires_at, periodic job)
```

| Из | В | Триггер |
|---|---|---|
| — | `active` | выпуск ключа (`/app/keys`) |
| `active` | `revoked_by_server` | `/app/keys/{id}/revoke-on-server` или admin-revoke permit |
| `active` или `revoked_by_server` | `revoked_by_reader` | приход BLK с этим key_id (ридер сам решил отозвать) |
| `revoked_by_server` или `revoked_by_reader` | `revoked_in_bloom` | приход `delivery_receipt` или `FDI` с `applied_filter_version ≥ committed_filter_version` |
| любой не-expired | `expired` | periodic worker `expire_keys` |

**Инвариант**: `committed_filter_version` устанавливается один раз и больше не меняется.

**`is_active` derived**:
- `true` для `active`, `revoked_by_server` — потому что ридер ЕЩЁ принимает этот ключ до доставки нового фильтра. Это влияет на счётчик `n_parallel` permit'а (нельзя выпустить новый поверх).
- `false` для `revoked_by_reader`, `revoked_in_bloom`, `expired`.

### 4.2 Permit (двухфазный admin-revoke)

```
active ──(POST /permits/{id}/revoke)──→ revoking ──(все ключи is_active=false)──→ revoked
```

**Почему двухфазно**: admin может «отозвать» permit мгновенно (`revoke_initiated_at = now`), но реальная защита включится только когда ридер применит новый bloom-фильтр. До тех пор permit в состоянии `revoking`, и ключи в `revoked_by_server` — то есть всё ещё валидны на ридере.

В web-панели это отображается жёлтым «revoking…» бейджем, чтобы админ не подумал что система зависла.

### 4.3 DeliveryTask (доставка фильтра)

```
created → (один из путей):
   ├── completion_source="receipt" → delivery_receipt от ридера
   ├── completion_source="fdi"     → ридер сам сказал что у него v=X (через FDI envelope)
   └── (никогда не закрывается, если ридер навсегда офлайн — повод для алёрта в админке)
```

---

## 5. Основные процессы (use cases)

### 5.1 Регистрация и онбординг сотрудника

```
1. Админ создаёт User в web-панели (login + temp password).
2. Сотрудник скачивает Android-app.
3. Логинится: app получает session_token + refresh_token.
4. App генерирует ed25519-пару в Android Keystore, регистрирует
   pubkey через POST /auth/register-device. Backend создаёт UserDevice.
5. App готов к запросу ключей. Permits ещё нет — нужно дождаться выдачи.
```

### 5.2 Provisioning ридера (одноразовая процедура)

```
1. Админ запускает Desktop/ScudProvisioner на офлайн-машине.
2. Утилита генерирует reader_ed25519 пару (priv хранится только в NVS ридера,
   pub передаётся в backend).
3. Утилита прошивает в ESP32 NVS: reader_id (UUID), reader_priv, плюс
   server_pubkey (получает от backend).
4. POST /admin/readers/enroll: backend генерирует server_ed25519 + server_x25519
   пары для этого ридера, сохраняет, возвращает pub-части.
5. Утилита прошивает их во вторую часть NVS.
6. Ридер собирается и устанавливается в физическое место.
```

После этой процедуры приватные ключи ридера не покидают NVS, а приватные ключи сервера для этого ридера живут только в БД сервера.

### 5.3 Выдача permit (админ → пользователь)

```
1. Админ открывает /admin/permits/new (или /bulk).
2. Выбирает: пользователя, ридер, окно действия, n_parallel, max_token_ttl.
3. POST /admin/permits. Сервер вставляет Permit row.
4. С этого момента пользователь видит этот permit в Android-app
   на экране «Пропуска» и может запросить ключ.
```

### 5.4 Запрос ключа phone'ом

```
Phone   →  POST /app/keys  { permit_id, ttl_seconds, valid_from }
Server  →  1. Проверяет permit принадлежит этому user_id
           2. Проверяет n_parallel: count(active keys for permit) < permit.n_parallel
           3. Проверяет valid_from/until в окне permit'а
           4. Проверяет ttl_seconds <= permit.max_token_ttl_seconds
           5. Берёт следующий serial для этого permit'а
           6. Генерирует issued_key 151 B, подписывает server_ed25519_R_priv
           7. Сохраняет в issued_keys таблицу
           8. Возвращает 151 B blob
Phone   →  Сохраняет в локальную Room БД (issued_keys)
           С этого момента готов к ACCESS-тапу.
```

### 5.5 Проход (NFC tap)

Самая критичная часть. Полный сценарий — в [`00_shared_protocol.md` §4](00_shared_protocol%20%281%29.md), здесь только бизнес-логика:

```
1. Сотрудник прикладывает телефон к ридеру.
2. Reader (PN532 initiator) делает SELECT AID → PUSH_INFO (146 B) с fresh_nonce.
3. Phone парсит INFO, проверяет reader_signature через свой кэш reader_pubkey.
4. Phone строит operations queue (TapDecisionTree):
     - Если есть pending filter — FILTER_UPDATE
     - Если drift времени > 15s и есть TimeGrant — TIME_SYNC
     - Всегда — GET_FILTER_DELIVERY_INFO (FDI)
     - Если blacklist_count > 0 — GET_BLACKLIST
     - Если есть pending revoke_intent — REVOKE_KEY
     - Если есть valid issued_key для этого reader_id — ACCESS
6. Цикл FETCH/READ_CHUNK/PUSH_CHUNK по операциям.
7. На ACCESS=OK:
     - Reader открывает замок (GPIO high, длительность из config).
     - Reader кэширует passage_receipt в RAM (см. §5.7).
8. Phone посылает GET_PASSAGE_RECEIPT (0x16), забирает квитанцию.
9. END.
10. Phone накопил FDI/BLK/passage_receipt и т.п. в outgoing_reports queue.
    При следующей online-синхронизации (фоновая или ручная)
    POST /app/reports/submit заливает их пакетно на сервер.
```

### 5.6 Проход (BLE — для шлагбаумов/турникетов)

Параллельный канал. Идентичная семантика операций, другой wire:

```
1. Phone сканирует BLE → видит SCUD service UUID + short_reader_id.
2. User кликает в UI на нужный.
3. Phone connect → exchangeMtu(247) → enable notify on INFO + RESULT.
4. Reader push'ит INFO. Phone тот же декодер использует.
5. Phone пишет ACCESS-операцию в OP_WRITE (chunked frames).
6. Reader notify'ит результат в RESULT_NOTIFY.
7. Phone сразу следом — GET_PASSAGE_RECEIPT той же командой.
8. Phone → CONTROL=END → disconnect.
```

BLE-канал доступен только на mains-powered ридерах (build `esp32dev_ble`).

### 5.7 Учёт проходов (PASSAGE_RECEIPT)

Это новый класс данных — «условный контроль посещаемости»:

```
Reader (после ACCESS=OK):
  1. Кэширует {key_id, phone_pubkey, permit_id, passed_at=now} в RAM.
  2. На GET_PASSAGE_RECEIPT — формирует 192 B receipt, подписывает
     reader_ed_priv, добавляет случайный receipt_nonce для backend-dedup.
  3. Возвращает 1+192+32 = 225 B envelope, очищает кэш.

Phone:
  Сохраняет receipt в outgoing_reports как type="passage_receipt".

Backend (worker process_report):
  1. parse 192 B → verify reader_signature.
  2. Sanity check passed_at (не старше 30 дней, не дальше 1 дня в будущее).
  3. Dedup по (reader_id, receipt_nonce).
  4. Резолвит user_id через permit_id (NULL если permit удалён — всё равно сохраняем).
  5. INSERT passage_events.
  6. Webhook fan-out: для всех активных подписок на passage_event ставится notify_webhook task.
```

**Свойства**:
- Только владелец ключа может получить квитанцию (без ACCESS=OK кэш пуст).
- Только владелец может доставить (или не доставить) её на сервер — в его интересах, если ему важен учёт.
- Подделать невозможно: подпись приватным ключом ридера, который живёт только в NVS.
- Реплей бесполезен: backend дедупит на (reader_id, receipt_nonce).
- Сервер видит только то, что доставлено — это право пользователя на приватность.

### 5.8 Отзыв доступа

Три пути:

**A. Phone-инициированный (REVOKE_KEY)** — пользователь хочет отозвать свой собственный ключ (например, потерял второй телефон):
```
Phone     →  локальный pending revoke-intent (на телефоне, Room PendingRevokeIntent —
             серверного эндпоинта для этого нет; reader-side revoke оффлайновый).
При следующем тапе на ридер этого permit:
Phone     →  REVOKE_KEY operation (signed) — ридер кладёт key_id в local_blacklist.
На следующем GET_BLACKLIST:
Reader    →  BLK report с этим key_id.
Backend   →  переводит ключ active → revoked_by_reader.
```

**B. Server-инициированный (admin revoke key)** — админ через `/admin/keys/{id}/revoke`:
```
Backend   →  переводит status active → revoked_by_server, committed_filter_version = next.
Backend   →  enqueue generate_filter (debounced).
Worker    →  генерирует filter v+1 с key_id в bloom.
Когда первый Phone делает FILTER_UPDATE на ридер:
Reader    →  применяет фильтр, отдаёт delivery_receipt.
Backend   →  переводит status revoked_by_server → revoked_in_bloom.
```

**C. Admin revoke permit (двухфазный)** — отзывает ВСЕ ключи permit'а сразу:
```
Backend   →  permit.revoke_initiated_at = now (status="revoking")
             все active keys → revoked_by_server
             enqueue generate_filter.
... ждём applied_filter_version ≥ committed на ридере ...
Backend   →  finalize: permit.revoked_at = now (status="revoked").
```

Web-панель показывает permit в статусе `revoking` в течение этого окна, чтобы админ понимал что revoke в процессе.

### 5.9 Жизненный цикл filter_package

Фильтр генерируется автоматически worker'ом по триггерам:
- Создан/удалён issued_key
- Изменился статус issued_key (active ↔ revoked_by_*)
- Истёк ключ (periodic)

Логика:
1. Worker берёт все `active`-ключи для reader_id, сортирует по expires_at.
2. Берёт все `revoked_by_server`/`revoked_by_reader` ключи которые ещё не в bloom.
3. Подбирает `hash_seed` так, чтобы whitelist (false positives) поместился в 256 записей (`shared §8`).
4. Подписывает, сохраняет в `filter_packages` с новой `filter_version`.
5. Создаёт `DeliveryTask` для каждой пары `(reader_id, filter_version)`.
6. Phone'ы, заходя на `/app/courier/available`, видят эти задачи и могут «скачать» pending package.
7. При следующем тапе доставляют как FILTER_UPDATE.

### 5.10 Webhook fan-out

Любой `INSERT passage_events` (а в v1.1+ — и другие события) триггерит:
```
1. SELECT активных webhook_subscriptions с event_type IN tags.
2. Для каждой — INSERT BackgroundTask(type="notify_webhook", payload={webhook_id, event_type, data}).
3. Worker берёт task, делает HTTP POST на subscription.url с JSON-payload'ом.
   Если есть secret — HMAC-SHA256 в X-SCUD-Signature.
4. 2xx — last_success_at=now, consecutive_failures=0.
   Иначе — last_failure_at=now, failures++, и при ≥10 подряд — is_active=false.
```

Это превращает SCUD в источник событий для внешних систем (CRM, табельных, BI dashboards) без необходимости им polling'ить API.

---

## 6. Инварианты безопасности

| # | Инвариант | Защита |
|---|---|---|
| I1 | **Reader.priv никогда не покидает NVS** | Provisioning утилита генерирует *в* ESP32; ключ не выводится через серийный порт после генерации |
| I2 | **Phone.priv никогда не покидает Android Keystore** | Используется system Keystore; ключ генерируется на устройстве, не экспортируется |
| I3 | **Server.priv для каждого ридера хранится только в БД сервера** | Не отдаётся ни phone, ни ридеру, ни admin |
| I4 | **ACCESS не пройдёт без знания phone.priv** | Phone подписывает ACCESS включая reader.fresh_nonce — реплей не работает, чужой телефон без ключа не подпишет |
| I5 | **passage_receipt подделать вне ридера невозможно** | Подписана reader.priv, который недоступен извне |
| I6 | **filter_package подделать вне сервера невозможно** | Подписана server.priv, который только в БД сервера |
| I7 | **Двойной проход одной квитанцией не учтётся** | Backend дедупит на (reader_id, receipt_nonce) |
| I8 | **Реплей INFO/ACCESS между ридерами невозможен** | reader_id входит в подписанные домены и payload; reader.fresh_nonce одноразовый |
| I9 | **Истёкший ключ не пройдёт** | Ридер проверяет expires_at локально по RTC, до crypto-проверок |
| I10 | **Cross-domain подпись невозможна** | Все подписи — `Ed25519(priv, domain_tag || payload)`; domain tags разные для разных типов |
| I11 | **Замена ридера не воссоздаёт его identity** | reader_id + reader_pubkey должны совпасть с записью в БД — иначе backend не примет |
| I12 | **Admin web-сессии не имеют root-доступа** | Cookie подписана `SCUD_WEB_SECRET`, имеет TTL 8ч; revoke api_key моментально инвалидирует |

---

## 7. Time-sync политика

Ридер офлайн → его RTC может дрейфовать (DS3231 точен, но не идеален; после долгого отключения питания нужен bootstrap).

| Случай | Триггер | Право | Окно дрейфа |
|---|---|---|---|
| **Bootstrap** | `time_sync.last_sync_at_local == 0` | Только `hard` grant | любое значение |
| **Soft sync** | drift > 15s, есть `soft` grant | OK | `± (5 × days_since_last_sync)` секунд |
| **Hard sync** | drift любой, есть `hard` grant | OK | без проверки окна |
| **Refusal** | `now > grant.expires_at` | NOT_AUTHORIZED | — |

Логика в коде: [`docs/00_shared_protocol.md` §10](00_shared_protocol%20%281%29.md).

---

## 8. Ограничения и компромиссы

Это не идеальная система. Понимание компромиссов важно:

### 8.1 Что не защищено

- **Деактивированный сотрудник может проходить, пока не доставится новый фильтр.** Время реакции = время до следующего тапа любого курьера на этот ридер. Mitigation: каждый сотрудник может быть курьером, не только владелец ключа.
- **BLE-link не шифруется.** Полагается на E2E ed25519/x25519. Eavesdropper увидит байты operations/results — но не сможет ничего подделать. Это **сознательный** компромисс ради UX (без bonding).
- **Скрытие проходов от учёта** — пользователь может не передавать passage_receipt серверу. Это его право (приватность), но и его проблема (нет учёта). Если важен жёсткий учёт — нужно дополнительное физическое решение (вертушка с подсчётом).

### 8.2 Что не масштабируется бесконечно

- **Bloom-фильтр** размерится **per-reader** под число отзывов конкретного ридера (§3.4), с конфигурируемым потолком `filter_max_bloom_bytes` (по умолчанию ~100 KB); выше потолка FP-rate растёт мягко (whitelist поглощает FP активных ключей). Очень большой ридер (десятки тысяч отзывов) всё равно потребует смены подхода — delta-only или пер-группа фильтры.
- **PostgreSQL** — одна нода без шардинга. Hundreds-of-millions проходов в год — ок (passage_events ~200 байт/строка → ~20 GB/год при 1 млн проходов), но если миллиарды — нужна стратегия партиционирования по `passed_at`.

### 8.3 Что зависит от внешних факторов

- **Точность RTC** ридера → дрейф до 2 мин/месяц без sync. При активном использовании sync приходит при каждом тапе.
- **Bluetooth-стек Android** известен капризностью между вендорами. UI обрабатывает большинство известных кейсов (отказ permissions, выключенный bluetooth, MTU < 247), но stress-test на флагман-Samsung'ах и low-end Xiaomi пока not done.

---

## 9. Где что искать в коде

| Тема | Файлы |
|---|---|
| Байтовые форматы, опкоды, домены | `docs/00_shared_protocol (1).md` |
| ORM-модели | `Backend/src/scud/db/models.py` |
| Миграции | `Backend/migrations/versions/000*.py` |
| Crypto / parsers | `Backend/src/scud/crypto/{signing,serialization,sealed_box}.py` |
| Бизнес-логика выпуска ключей | `Backend/src/scud/domain/{keys,permits,grants}.py` |
| Обработка входящих отчётов | `Backend/src/scud/domain/reports.py` |
| Worker-задачи | `Backend/src/scud/worker_handlers/*.py` |
| Admin JSON API | `Backend/src/scud/api/admin/*.py` |
| Admin web-панель | `Backend/src/scud/api/admin_web/*.py` |
| Firmware: операции | `ESP32/firmware/src/ops/*.cpp` |
| Firmware: транспорт (NFC) | `ESP32/firmware/src/transport/*.cpp` |
| Firmware: BLE-канал | `ESP32/firmware/src/ble/*.cpp` |
| Firmware: state (NVS) | `ESP32/firmware/src/state/*.cpp` |
| Android: HCE-логика | `AndroidApp/app/src/main/java/com/vkrauth/app/hce/*.kt` |
| Android: BLE-логика | `AndroidApp/app/src/main/java/com/vkrauth/app/ble/*.kt` |
| Android: бизнес-репозитории | `AndroidApp/app/src/main/java/com/vkrauth/app/data/repository/*.kt` |
| Provisioning утилита | `Desktop/ScudProvisioner/` |

---

## 10. Глоссарий

- **AID** — Application ID, идентификатор HCE-приложения на телефоне для NFC SELECT AID.
- **APDU** — Application Protocol Data Unit, единица обмена ISO 7816-4.
- **Bloom filter** — вероятностная структура «вероятно содержит» с заданной FP rate.
- **BLE** — Bluetooth Low Energy.
- **Bootstrap** — первичная синхронизация RTC ридера через hard grant.
- **Committed filter version** — версия фильтра, в которой ключ гарантированно учтён.
- **Courier** — phone, переносящий filter_package с сервера на ридер.
- **Domain tag** — 16-байтовый префикс для подписи, предотвращающий cross-protocol attacks.
- **HCE** — Host Card Emulation, режим Android для эмуляции NFC-карты.
- **NVS** — Non-Volatile Storage, ESP32-овое key-value хранилище в flash.
- **Permit** — пропуск (логическое право), не путать с **key** (физический подписанный блоб).
- **Sealed box** — libsodium-конвенция шифрования с эфемерным X25519-ключом.
- **Tap session** — один цикл NFC-обмена между ридером и телефоном.
- **Time grant** — server-issued право конкретного телефона переподписывать время для конкретного ридера.

---

## 11. См. также

- [`00_shared_protocol (1).md`](00_shared_protocol%20%281%29.md) — байтовые форматы, опкоды, крипто
- [`01_backend_spec.md`](../Backend/01_backend_spec.md) — ТЗ на бэкенд
- [`03_android_spec.md`](03_android_spec.md) — ТЗ на Android-app
- [`04_deployment.md`](04_deployment.md) — развёртывание и интеграция
- [`README.md`](README.md) — обзор репозитория
