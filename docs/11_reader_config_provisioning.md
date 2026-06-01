# 11. Reader Config & Provisioning — параметризация (✅ Phases 1–4 реализованы, compile-only)

> **Статус:** ✅ Phases 1–4 реализованы (compile-only) · **Компоненты:** firmware + backend + desktop · **Не трогает байт-точный протокол.**
> Firmware: таблица `reader_config` + извлечение параметров из `config.h`; backend: config-колонка; desktop (Phase 4):
> библиотека/редактор config-шаблонов + полная `SET-*` серия (24 CFG + `lock_duration_ms` + `ble_enabled`),
> dotnet build 0 ошибок. **Phase 1+ (структурированный серверный слой) — ✅ реализован (§9):**
> профили/overrides/bounds/резолвер + resolved `config-script`; `enroll` принимает
> `profile_id`/`hardware_class`/`overrides`; desktop тянет и проигрывает resolved-скрипт verbatim.
> Compile/host-proven (backend pytest 187 passed; dotnet build 0 ошибок); рантайм на железе не верифицирован.
>
> **Цель:** убрать compile-time хардкод операционных параметров. Всё, что зависит от
> установки / политики / типа железа, **присваивается при provisioning ридера**.
> Desktop-провижнер хранит **config-шаблоны по типам устройств** (`ESP32 base`, …),
> из которых берутся значения по умолчанию; оператор редактирует отдельные параметры
> и сохраняет как именованные конфиги; при регистрации READER эти параметры уходят
> **и в NVS ридера, и в backend-БД**.

## 1. Таксономия параметров (3 яруса)

| Ярус | Что | Где живёт | Кто задаёт |
|---|---|---|---|
| **A. Per-reader operational** | lock-импульс, таймауты, cooldown, clock-skew, nonce TTL, caps, `ble_enabled`, … | NVS ридера (`ReaderConfig`) + зеркало в БД | оператор при provisioning (поверх дефолта шаблона) |
| **B. Per-device-type hardware** | GPIO-пины, active-high, UART baud, I2C freq, lock-полярность | дефолт **config-шаблона** (по типу платы) | шаблон; редко правится per-reader |
| **C. Protocol-fixed / compile-time** | байт-offset'ы, размеры структур, опкоды, домены, `PROTOCOL_VERSION`, AID, framing-флаги | **код** (`config.h`/протокол) | НЕ параметризуется (байт-точно) |

> Конкретный каталог значений (что в какой ярус) — см. **§6**, заполняется из
> firmware-magic-number-аудита.

## 2. Firmware — `ReaderConfig` в NVS

- `struct ReaderConfig { uint16_t config_version; /* поля яруса A (+B) */ };` — **версионированный** (forward-compat: новая прошивка читает старый конфиг, дефолты для отсутствующих полей).
- NVS namespace `scud_cfg` (или расширение `scud_imm`); грузится на boot в `g_state.config` с **compile-time дефолтами** — текущие `#define` становятся fallback'ом, если NVS пуст → **обратная совместимость** (уже прошитые ридеры работают как раньше).
- Все use-сайты читают `g_state.config.X` вместо `#define X`.
- Provisioning: расширить текстовый `SET-*` CLI по параметру (консистентно с текущим `provisioning/serial_cmd.cpp`); `COMMIT` персистит весь блок. (Опц. bulk `SET-CONFIG <hex>` — атомарный versioned-blob — как оптимизация позже.)

## 3. Backend — колонки Reader + enroll

- Миграция: Reader-модель получает колонки яруса A (`lock_duration_ms`, `session_deadline_ms`, `idle_timeout_ms`, `clock_skew_s`, …) + `device_type`.
- `EnrollRequest` получает **опциональный** `config` (с серверными дефолтами); сохраняется в Reader-строке — это **зеркало** для управления / аудита / ре-провижна. **Runtime-authoritative — NVS ридера**; БД — копия.

## 4. Desktop — библиотека config-шаблонов ✅ СДЕЛАНО (Phase 4)

- `DeviceConfigTemplate { name, device_type, …все A+B параметры }`.
- **Библиотека шаблонов** (JSON в app-data; расширяет текущий `SettingsService.LockDurationMs` до полноценного списка): оператор выбирает шаблон → значения предзаполнены → правит отдельные → опц. сохраняет как новый именованный шаблон.
- `ProvisionFlow.RunAsync` шлёт **полную** `SET-*` серию из выбранного config (а не только `SET-LOCK-DURATION`) + `enroll` с теми же значениями.

> **Реализовано** в `Desktop/ScudProvisioner`: единый каталог `Models/ConfigParamCatalog.cs`
> (`ParamSpec` ×27 — байт-точное зеркало firmware `reader_config.cpp` CFG[] = 25 строк + `lock_duration_ms` + `ble_enabled`),
> `Models/DeviceConfigTemplate.cs` (built-in default «ESP32 base (door_mains)»),
> `Services/ConfigTemplateService.cs` (CRUD-библиотека шаблонов: JSON в `FileSystem.AppDataDirectory/config_templates.json`,
> active-имя в `Preferences`, seed built-in при первом запуске), редактор `Views/TemplatesPage.xaml`
> + `ViewModels/TemplatesViewModel.cs` (группировка по ярусу, client-side clamp + range-hint, Save / Save As… / Delete / Set active).
> `ProvisionFlow` (DI: `IConfigTemplateService`) на шаге [3/4] шлёт всю `SET-*` серию активного шаблона.
> **Зеркало config в backend-БД через enroll отложено в Phase 3-follow-up** (serial-путь кладёт всё в NVS authoritatively).

## 5. Обновлённый поток provisioning

```
1. GEN-KEYPAIR / SHOW-PUBKEY            (как сейчас; priv генерится в ESP32)
2. Оператор выбирает device-config-шаблон → правит параметры
3. POST /admin/readers/enroll {identity + config}
       → backend генерит server-ключи + сохраняет config-колонки, возвращает server-pub
4. SET-* серия по serial: identity + server-pub + ВСЕ config-параметры
5. COMMIT → атомарный flush NVS
```

## 6. Параметр-каталог (из firmware-magic-number-аудита)

> Аудит (7 агентов, выверено по исходникам): **25 per-reader tunables · 20 per-device-type ·
> 11 compile-time-fixed · 36 inline-литералов на извлечение · 73 protocol-fixed** (байт-точно — НЕ трогать).
> Сегодня провижнятся только **2** (`lock_duration_ms`, `ble_enabled`).

### 🔴 Высший приоритет (находки)
- **`passage_direction` захардкожен `0x01`=entry** ([passage.cpp:110](../ESP32/firmware/src/ops/passage.cpp#L110)) — **функционально неверно для exit-ридеров** (комментарий сам это откладывает). Самый ценный gap → per-reader `enum {entry=0x01, exit=0x02}`.
- **Security-окна** (`clock_skew_seconds`, `nonce_ttl_ms`, `time_sync_drift_s_per_day`, `time_sync_bootstrap_window_s`) — **должны оставаться скоординированы с backend** (shared-protocol инвариант). Параметризуя их, валидировать против серверной политики.
- `time_sync_drift` (`10*days`) и `bootstrap` (`86400`) — **голые литералы, даже не названы** ([time_sync.cpp:99,105](../ESP32/firmware/src/ops/time_sync.cpp#L99)).
- **PN532/HCE interop-тайминги** (`pn532_passive_retries`, `rf_timing`, `field_reset_pause`, push-retries/delay, `fail_reinit_threshold`) — голые литералы, **дублированы между init и reinit** (`apdu.cpp:27/31` vs `:87`) → должны стать одним источником.
- **SET-LOCK-DURATION молча truncate'ит uint16 без clamp** ([serial_cmd.cpp:116](../ESP32/firmware/src/provisioning/serial_cmd.cpp#L116)) — добавить range-валидацию.

### ⚠️ Caveats реализации
- `local_blacklist_cap` (256) и `nonce_ring_size` (8) **размеряют статические массивы** в `ReaderState` (`blacklist[CAP]`, `nonce_ring[SIZE]`) → провижн требует перехода на **heap-аллокацию** из provisioned-cap. И `RESULT_BUF_CAP` (=8704, захардкожен в **двух** файлах) тогда надо **выводить** из `local_blacklist_cap`.
- `MAX_FILTER_BYTES` / `TRANSFER_BUFFER_CAP` — выводить из geometry flash-партиций (см. задачу `N2/B6-EXEC`).

### 6.1 Ярус A — per-reader (25; NVS `scud_imm` + DB-колонка + `SET-*` + desktop-editor)
| Параметр | Сейчас | Управляет | cmd / диапазон |
|---|---|---|---|
| `lock_duration_ms` *(есть)* | 3000 | импульс замка | SET-LOCK-DURATION · 500..10000, +clamp (firmware `LOCK_DURATION_MIN/MAX_MS`) |
| `ble_enabled` *(есть)* | true | BLE-радио | BLE-ENABLE/DISABLE · bool |
| `passage_direction` 🔴 | 0x01 entry | направление прохода | SET-PASSAGE-DIRECTION · enum entry/exit |
| `clock_skew_seconds` 🔒 | 60 | дрейф RTC vs токен | SET-CLOCK-SKEW · 5..600 |
| `nonce_ttl_ms` 🔒 | 10000 | окно anti-replay | SET-NONCE-TTL · 2000..60000 |
| `nonce_ring_size` ⚠️ | 8 | глубина nonce-кольца | SET-NONCE-RING · 4..64 (heap) |
| `local_blacklist_cap` ⚠️ | 256 | offline-отзывов | SET-BLACKLIST-CAP · 64..2048 (heap) |
| `time_sync_drift_s_per_day` 🔒 | 10 | SOFT-дрейф/день | SET-TSYNC-DRIFT · 1..60 |
| `time_sync_bootstrap_window_s` 🔒 | 86400 | первое SOFT-окно | SET-TSYNC-BOOTSTRAP · 3600..604800 |
| `nfc_session_deadline_ms` | 8000 | дедлайн tap-сессии | SET-NFC-DEADLINE · 3000..30000 |
| `ble_idle_timeout_ms` | 30000 | BLE idle-watchdog | SET-BLE-IDLE · 5000..300000 |
| `max_whitelist_count` | 256 | whitelist в пакете | SET-WHITELIST-MAX · 32..2048 |
| `max_bl_delta_count` | 256 | blacklist-delta в пакете | SET-BL-DELTA-MAX · 32..2048 |
| `cooldown_after_end_ms` | 3000 | cooldown после END | SET-COOLDOWN-END · 500..10000 |
| `cooldown_after_grant_ms` | 4500 (+1500 margin) | cooldown после GRANT | SET-COOLDOWN-GRANT · 1000..15000 |
| `pn532_passive_retries` | 0x10 | ретраи PN532 | SET-PN532-RETRY |
| `pn532_rf_timing` | 0x0F/0x0F | RF-тайминг PN532 | SET-RF-TIMING |
| `field_reset_pause_ms` | 80 | пауза сброса поля | SET-FIELD-PAUSE · 50..500 |
| `push_info_retries` | 3 | ретраи PUSH_INFO | SET-PUSH-RETRIES · 1..10 |
| `push_info_retry_delay_ms` | 40 | задержка ретрая | SET-PUSH-DELAY · 10..500 |
| `pn532_fail_reinit_threshold` | 3 | порог reinit PN532 | SET-REINIT-THRESHOLD · 1..20 |
| `nfc_detect_timeout_ms` | 100 | таймаут детекта таргета | SET-NFC-DETECT · 20..500 |
| `ble_requested_mtu` | 247 (pdu=mtu-7=240) | BLE MTU/PDU (связаны) | SET-BLE-MTU · 185..517 |
| `ble_info_defer_ms` | 50 | отложенный INFO-push | SET-BLE-INFO-DEFER · 10..500 |

🔒 = security-окно (координировать с backend) · ⚠️ = размеряет статический массив (нужна heap-аллокация).

### 6.2 Ярус B — per-device-type (20; шаблон платы, не per-reader)
GPIO-пины (PN532 UART2 16/17, I2C 21/22, LOCK 26, LED 2, btn 0), `*_ACTIVE_HIGH` (полярность замка/LED), UART baud 115200, I2C 400 кГц, `CONFIG_FREERTOS_HZ` 1000, monitor/upload baud, `SCUD_BLE_ENABLED` (build-env, = тип питания), 4 партиции (nvs/factory/filter/state), `MAX_FILTER_BYTES`/`TRANSFER_BUFFER_CAP` (вывести из geometry), RTC DS3231 @0x68. → рекомендуется `board_profiles/*.h`, генерируемые из desktop-шаблона.

### 6.3 Ярус C — compile-time-fixed (11, named)
`PROTOCOL_VERSION`, `MAX_APDU_DATA_SIZE` (240, HW-derived), filter-file-пути, `DEFAULT_LOCK_SIGNAL_DURATION_MS` (factory-дефолт для override), LED blink/RTOS task-sizing литералы, Ed25519 seed/nonce word-counts.

### 6.4 magic_to_name — 36 inline-литералов на извлечение (Фаза 2)
Главные: loop-delays (`main.cpp` 10/200/1000), 3 NVS-namespace строки + SPIFFS label/keys, time-sync `SECONDS_PER_DAY`/floor/RTC-dead sentinel, passage/revoke reason+flag байты, **дублированный 32-байт nonce-loop → один `fill_random_nonce()` (7 файлов)**, и latent-bug DRY: литерал `240` vs `MAX_APDU_DATA_SIZE` (`transfer.cpp:102,429`), сырые опкоды `0xC1-0xC5` vs `OPCODE_*`, `0x81` vs `MARK_ACCESS_VERDICT`, SELECT_AID vs `AID_BYTES`, `RESULT_BUF_CAP=8704` в двух файлах, `ble_frame pdu[256]` без `static_assert` (переполнится при росте `BLE_MAX_PDU`).

### 6.5 protocol_fixed (73) — НЕ параметризовать
Размеры req/resp (ACCESS 256, TIME_SYNC 289, REVOKE 407, FDI 241, PASSAGE 225, INFO 146, VERDICT 42), все field-offset'ы структур, опкоды 0x01-0x16, маркеры 0x81-0x97, kind SOFT/HARD, 16B id/key_id/домены, 32B nonce/ключи, 64B sig, sealed-box, AID, 5 BLE-UUID + `MANUFACTURER_ID 0xC0DE` + framing-флаги, крипто-константы. Любое изменение = синхронно backend+firmware+android.

## 7. Фазы реализации

| Фаза | Что | Проверяемо здесь |
|---|---|---|
| **2. firmware cleanup** ✅ **СДЕЛАНО** (`c2c8061`) | все compile-time литералы вынесены в `config.h` (~40 const, DRY-фиксы); оба env байт-идентичны, host-тест 0 fail | ✅ |
| **1. firmware: ReaderConfig** ✅ **СДЕЛАНО** (`42d585b`) | table-driven `ReaderConfig` (`state/reader_config.*`): 21 параметр в NVS `scud_imm`, дефолт = `config.h`-const (непровижиненный ридер идентичен); 22 `SET-*` + clamp + STATUS; 23 use-сайта свопнуты. Оба env собираются | ✅ |
| **1b. firmware: cap-параметры** ✅ **СДЕЛАНО** (`976c99b`) | `local_blacklist_cap`+`nonce_ring_size` → provisioned **soft-cap** ≤ static-MAX (default=MAX, поведение идентично); `SET-BLACKLIST-CAP`/`SET-NONCE-RING`; raise выше MAX = recompile. Оба env собираются | ✅ |
| **3. backend** ✅ **СДЕЛАНО** (`acc0d9b`) | Reader получил nullable JSONB `config` (миграция 0006, sqlite-совместимо); `enroll` принимает опц. config + хранит. pytest 144 passed | ✅ |
| **4. desktop** ✅ **СДЕЛАНО** | библиотека config-шаблонов (`ConfigParamCatalog` ×27 = зеркало firmware CFG[] 25 + lock_duration + ble_enabled; `ConfigTemplateService` JSON-CRUD в app-data + active в Preferences; редактор `TemplatesPage` с group-by-tier / clamp / Save·SaveAs·Delete·SetActive) + `ProvisionFlow` шлёт **полную** `SET-*` серию активного шаблона на шаге [3/4]. `dotnet build` = 0 errors. **Зеркало config в БД через enroll отложено в Phase 3-follow-up** (serial SET-* кладёт всё в NVS authoritatively). | ✅ собирается локально (MAUI net9.0-windows) |

## 8. Что НЕ трогаем
- Байт-точный протокол (ярус C) → **conformance-векторы те же**.
- Identity/ключи provisioning (уже есть: `SET-READER-ID/GROUP-ID/SERVER-*`).
- Связано с задачей `N2/B6-EXEC` (transport) лишь через `filter_max_bloom_bytes` — это **backend**-параметр, не reader-config.

---

## 9. Структурированная серверная параметризация (Phase 1+) ✅ реализовано (compile/host-proven)

> **Коммиты:** backend `210df69` (pytest 187 passed) · desktop `820dc23` (dotnet build 0 ошибок).
> Надстройка над flat `config`-колонкой (§3 не удалена — остаётся verbatim-зеркалом). Идея: **сервер**
> — источник истины для конфигурации (профиль + overrides + bounds + резолвер), а ридер просто
> исполняет resolved `SET-*`-скрипт. Firmware/wire-протокол/conformance-векторы **не тронуты**.

### 9.1 Схема БД (миграция `0007_reader_profiles`, down_revision `0006`)
Только nullable-add + новые таблицы → существующие ридеры не затронуты (`profile_id=NULL` ⇒ чистые
firmware-дефолты).

| Таблица | Назначение | Ключ |
|---|---|---|
| `reader_profile` | переиспользуемые именованные шаблоны | `profile_id` UUID; `name` uniq; `hardware_class` |
| `reader_profile_param` | разреженные параметры профиля | PK (`profile_id`, `param_key`); `value` BIGINT; FK CASCADE |
| `reader_param_override` | разреженные per-device переопределения | PK (`reader_id`, `param_key`); `value` BIGINT; FK CASCADE |
| `param_bounds` | firmware-class границы (read-only) | PK (`param_key`, `hardware_class`); min/max/default + `server_floor`/`server_ceiling` |
| `readers` (+2 кол.) | `profile_id` (nullable FK, SET NULL) + `hardware_class` (NOT NULL, default `esp32_mains_ble`) | |

### 9.2 Единый каталог — `scud/domain/reader_param_catalog.py`
**Один источник истины** (26 параметров = зеркало firmware `reader_config.cpp` CFG[] 24 + `lock_dur` +
`ble_en`), байт-сверен с `config.h`. Из него сидятся `param_bounds` (миграцией) **и** seed-помощник для
тестов — значения не разъезжаются (тот самый класс багов «дефолты разошлись»; ревью поймало
`lock_dur 100..60000` → исправлено на firmware `500..10000`). 2 hardware-класса: `esp32_mains_ble`,
`esp32_battery_nfc`. Security-параметры (`skew`/`nonce_ttl`/`ts_drift`/`ts_boot`) имеют tighter
`server_ceiling` (напр. `skew ≤ 300` при firmware-max 600).

### 9.3 Резолвер — `scud/domain/reader_config_resolver.py` (чистая ф-я, единственное место политики)
`resolve_reader_params(session, reader) → {params, script[], warnings[]}`:
1. base = `param_bounds.default_value` для `hardware_class`.
2. ← наложить `reader_profile_param` · 3. ← наложить `reader_param_override`.
4. кламп каждого к `[server_floor ?? min .. server_ceiling ?? max]`.
5. **direction-lock (override-with-audit):** даунгрейд security-гейта (`ho_req 1→0`, `ble_en 0→1`) при
   security-профиле — **игнорируется** + warning + запись в `AdminAuditLog` (на write-пути, не на чтении).
6. кросс-инварианты: `cd_grant ≥ lock_dur+1500`, `ble_mtu ≥ 243` (авто-подгон + warning).
7. **BLE-suppression:** для `esp32_battery_nfc` / `ble_en=0` BLE-строки выкидываются из скрипта (но
   `ble_en` эмитит `BLE-DISABLE`).
8. **идемпотентный** `SET-*`-скрипт в порядке каталога. Никогда не эмитит значение, которое firmware
   заклампит (всё провалидировано против `param_bounds`).

### 9.4 Сид-профили (4)
| Профиль | hardware_class | Ключевое |
|---|---|---|
| `entrance` | esp32_mains_ble | `psg_dir=1`, щедрые ёмкости, `ble_en=1` |
| `interior_room` | esp32_mains_ble | `psg_dir=2`, `lock_dur=1000`+снаппи cooldown'ы |
| `sensitive_room` | esp32_mains_ble | `ho_req=1`, `ble_en=0`, узкие `skew`/`nonce_ttl` |
| `battery_nfc_only` | esp32_battery_nfc | `ble_en=0`, длиннее `nfc_det`/`fld_pause` |

### 9.5 API + Desktop
- **Backend** (`api/admin/reader_profiles.py` + `readers.py`): CRUD `/admin/reader-profiles` (422 при
  выходе за `param_bounds`); `enroll`/`PATCH` ридера принимают `profile_id`+`hardware_class`+sparse
  `overrides` (валидация → 422); `GET /admin/readers/{id}/config-script` → resolved `{params, script, warnings}`
  (read-only; аудит даунгрейда — на PATCH).
- **Desktop** (`ProvisionFlow`/`BackendApi`/`ProvisionViewModel`): опциональный picker серверного
  профиля; при выборе — enroll с `profile_id` и **проигрывание resolved-скрипта verbatim** вместо
  локального шаблона (§4 остаётся fallback при пустом выборе). `arg==null` ⇒ команда-глагол
  (`BLE-ENABLE/DISABLE`), иначе `<cmd> <arg>`.

### 9.6 Оговорки
- Миграции проекта **Postgres-only** (`0001 CREATE EXTENSION pgcrypto`); тесты идут через `create_all`,
  поэтому 0007 рантайм-прогон возможен только на реальном Postgres (статически выверена + модели ↔
  миграция совпадают).
- 🔒-окна (`skew`/`nonce_ttl`/`ts_drift`/`ts_boot`) `server_ceiling` — это **политический knob**;
  координировать с backend-policy (shared-protocol инвариант).
- Серийный путь по-прежнему authoritative для NVS; БД-слой — управление/аудит/ре-провижн.
