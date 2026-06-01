# SCUD — Готовность прошивки ESP32: параметризация, энергопотребление, портируемость HAL и UI-дефекты

> Дата: 2026-06-01 · Ветка: `feature/transport-compile-only`
> Метод: независимое чтение кода + мульти-агентный синтез шести областных исследований
> (`ui-revoked-keys`, `ui-top-insets`, `firmware-params`, `power-economy`, `rtc-nfc-portability`,
> `readiness-checklist`) с указанием `file:line` на каждую находку.
> Тон и структура — по образцу `docs/07_architecture_review.md` и `docs/11_reader_config_provisioning.md`.

ID-области (напр. `PWR-`, `HAL-`, `UI-`) сквозные — их можно искать по этому документу.

---

## 0. Резюме и scope

Этот документ закрывает три задачи, поставленные по ВКР-ридеру: **(a)** довести параметризацию ESP-прошивки до состояния «закрыто»; **(b)** спланировать батарейный / автономный режим; **(c)** сделать RTC и NFC-frontend (PN532 → CLRC663) сменными, и зафиксировать готовность к полевой верификации.

**Краткий вердикт по каждой оси.**

- **Параметризация (раздел 2).** Прошивка уже существенно параметризована: **27** per-reader (Tier A) параметров живут в NVS `scud_imm` (25 строк табличного `ReaderConfig` CFG[] + типизированные `lock_duration_ms` и `ble_enabled`). Незакрытый остаток — это **Tier B** (пины, полярность, baud, I2C-freq — раздел всё ещё «сырые» `#define` без board-profile абстракции; именно это блокирует свопы), плюс несколько Tier-A-несостыковок (`COOLDOWN_GRANT_MARGIN_MS`, `MAIN_LOOP_DELAY_MS`, недеривированный `RESULT_BUF_CAP`).
- **Энергопотребление (раздел 3).** Ридер работает **на полной мощности 24/7**: `loop()` опрашивает PN532 (RF-поле активно при каждом poll) каждые 10 мс, BLE рекламируется непрерывно, **ни одного** `esp_light_sleep`/`esp_deep_sleep` нет. Доминируют RF-frontend PN532 и радио ESP32. Все токи в разделе 3 — **datasheet-ОЦЕНКИ, НЕ измерения** (мультиметра/PPK не было); приведён способ измерить.
- **Портируемость (раздел 4).** И RTC, и NFC-frontend **уже за чистым тонким seam** свободных функций (`rtc_init/now/set`; `apdu_init/detect/exchange/field_off/reset/reinit`) — grep подтверждает 0 утечек типов DS3231/PN532 за пределы `rtc.cpp`/`apdu.cpp`. Своп RTC тривиален; своп PN532→CLRC663 — умеренный по объёму, главный риск — ISO-DEP/T=CL framing (PN532 прячет его внутри `InDataExchange`, CLRC663 — frontend-only IC без встроенного стека).
- **UI-дефекты (раздел 5).** Два диагностированы до `file:line`: (UI-1) «0/1, но выпустить нельзя» — рассинхрон **определения счётчика**, не sync; (UI-2) увеличенный верхний «козырёк» — **двойной учёт** status-bar inset двумя вложенными Scaffold.
- **Готовность (раздел 6).** NFC-набор hardware-verified end-to-end; BLE проверен на железе только для ACCESS; вся transport-compile-only волна (handover, op_seq-корреляция, X2, flash-стриминг больших фильтров, H1-унификация) — **host-proven, рантайм на ESP32+телефоне ещё не пройден**. Power/autonomy и HAL-абстракция — **0% старта** (greenfield).

---

## 1. Карта статусов (одним взглядом)

| Capability / сценарий | Компонент(ы) | Статус | Как реализовано (1 строка) | Gap / риск |
|---|---|---|---|---|
| NFC ops end-to-end (ACCESS/FDI/TIME_SYNC/REVOKE/BLACKLIST/FILTER) | firmware `ops/*` + `transfer.cpp` + `apdu.cpp`; phone HCE | done + hw-verified | Reader-pull APDU-loop (FETCH/READ_CHUNK/PUSH_CHUNK) → `dispatch_op`-handlers | PUSH_CHUNK пинён к 146 B до HW-свипа (`transfer.cpp:120`) |
| BLE ops end-to-end | firmware `ble_channel.cpp`; phone BLE central | done + hw-verified (кроме TIME_SYNC) | NimBLE peripheral, chunked OP_WRITE/RESULT_NOTIFY, `op_seq`-корреляция, single-central | По словам оператора BLE-канал работает полностью; на железе не прогонялся только TIME_SYNC по BLE |
| NFC↔BLE handover | firmware `ops/handover.cpp`; Android HandoverOrchestrator | done + compile-only (host-proven) | 167 B reader-signed token, привязан к `tap_nonce`; ISSUE(NFC)/PRESENT(BLE) гейтят FILTER | Two-radio rendezvous не пройден на железе |
| Provisioning / SET-* CLI | firmware `serial_cmd` + `reader_config`; desktop ScudProvisioner | done + compile-only | Table-driven CFG[] в NVS `scud_imm`, 25 SET-* + clamp + STATUS; desktop: локальный шаблон ИЛИ серверный профиль | enroll теперь принимает `profile_id`/`hardware_class`/`overrides` (docs/11 §9) |
| Per-reader config (Phase 1) | firmware `reader_config.*`; backend профили/overrides/bounds/резолвер | done + compile/host-proven (структурный слой) | Серверный резолвер → resolved `config-script`, desktop играет verbatim; flat `config`-колонка = зеркало (docs/11 §9, commits `210df69`/`820dc23`) | Рантайм на устройстве не верифицирован; миграция 0007 — Postgres-only |
| Filter / blacklist sync | firmware `filter_update` + `authoritative.cpp` + `op_sink` | done + compile-only (host-proven); small-filter path hw-verified | Flash `op_sink` стримит большой фильтр в неактивный A/B-слот, two-pass verify, atomic swap | Реальный SPIFFS/NimBLE-flash I/O + ~100 KB активный bloom-malloc не проверены |
| Passage receipts | firmware `ops/passage.cpp` + `access.cpp` | done + hw-verified (NFC) | H3-fold-in: 192 B reader-signed receipt в хвост ACCESS_VERDICT (resp 234 B) | BLE-path receipt только compile-only |
| Time sync / clock trust | firmware `time_sync.cpp` + `rtc.cpp` + `access.cpp` | done + hw-verified | DS3231; SOFT(drift)/HARD grant+statement sigs; dead-RTC fail-closed EXPIRED | RTC-drift/dead-on-battery через sleep не тестирован |
| Transport policy (ACCESS routing) | firmware `op_dispatch.cpp`; Android TransportRouter | done + compile-only (H1-ревизия) | H1: ВСЕ ops едут по ОБОИМ транспортам; прежняя NFC-only-граница снята, relay-риск принят | BLE relay/wormhole на ACCESS осознанно принят; митигирует только Android confirm-UX |
| Key issuance / revocation | backend + Android + firmware REVOKE; desktop/Android UI | partial (revoke hw-verified; UI-баги открыты) | Two-phase revoke → local blacklist или filter; phone-signed REVOKE 407 B | 2 известных UI-бага (раздел 5) |
| **Power / autonomous** | firmware `main.cpp` / `platformio.ini` | **not-started** | Always-on: 10 мс PN532-poll (RF active) + непрерывный BLE-adv; нет sleep, нет task-WDT | Нет duty-cycling, нет light/deep sleep, нет батарейного профиля; крупнейший gap |
| **RTC portability (DS3231→other)** | firmware `hw/rtc.{h,cpp}` | **not-started** (интерфейс чистый, impl single-vendor) | 3-fn API оборачивает hardwired `static RTC_DS3231` | Нет HAL/build-flag-выбора backend; своп = single-file port |
| **NFC frontend portability (PN532→CLRC663)** | firmware `transport/apdu.cpp` | **not-started** (интерфейс чистый, impl single-vendor) | `apdu_*` оборачивает hardwired `static PN532(HSU)` | PN532-quirks текут в `transfer.cpp`/`config.h`; нет HAL-абстракции |
| Session-start robustness (0x0B/SESSION_LOST) | firmware `transfer.cpp` + `apdu.cpp` | partial (митигирован, не устранён) | PUSH_INFO-retry + SESSION_LOST re-push, bounded 8 s deadline | Интермиттентно; нет `esp_task_wdt`-backstop (FW-ARC-04 открыт) |
| UI: счётчик слотов / выпуск ключа | Android KeysScreen + backend `n_parallel`-гейт | partial (баг открыт) | Локальный `activeCount` ≠ серверный `count_active_keys` | UI-1 (раздел 5.1) |
| UI: верхний inset («козырёк») | Android MainActivity + AppScaffold | partial (баг открыт) | Двойной Scaffold консумит status-bar inset дважды | UI-2 (раздел 5.2) |

---

## 2. Параметризация прошивки

### 2.1 Текущее состояние

Прошивка уже **существенно параметризована**: табличный `ReaderConfig` (`state/reader_config.cpp`, `CFG[]` = 25 строк) плюс два типизированных поля `lock_duration_ms` и `ble_enabled` (`immutable.cpp`) дают **27 per-reader (Tier A)** параметров в NVS `scud_imm`. Каждый дефолтится своим `config.h`-`#define`, поэтому непровиженный/уже-прошитый ридер байт-идентичен. Эти Tier-A-поля уже зеркалятся в backend `config`-колонку через enroll (`docs/11` §3, миграция 0006) и в desktop config-templates (`ConfigParamCatalog` ×27).

**Чтобы «закрыть» прошивку, остаются четыре gap-класса:**

1. **Tier B (hardware-class) — главный блокер свопов, 0% сделано.** Все GPIO-пины, полярность (`*_ACTIVE_HIGH`), UART-baud, I2C-freq — это сырые compile-time `#define` в `config.h:8-30` без какой-либо board-profile абстракции; use-site читают макрос напрямую (`apdu.cpp:14`, `main.cpp:50/53/79`, `led.cpp:9/13`, `lock.cpp:13/18/26/27`). Ни `g_state`-поля, ни NVS, ни `SET-*`-команды для пинов нет.
2. **Power/loop-тайминги.** `MAIN_LOOP_DELAY_MS=10` (cadence loop()) — **главный энерго-knob** для батарейной работы — всё ещё compile-time `#define` (`config.h:99`, потребляется `main.cpp:118`), не provisionable. (Poll-таймаут `nfc_detect` уже per-reader.)
3. **Static-array caps.** `LOCAL_BLACKLIST_CAP`, `NONCE_RING_SIZE` — это ЖЁСТКИЕ compile-time максимумы; per-reader soft-caps могут только **уменьшать** использование. `RESULT_BUF_CAP=8704` дублирован по двум файлам (5 use-site в `ble_channel.cpp` + 3 в `transfer.cpp`) и **не выводится** из `blacklist_cap` → подъём `bl_cap` выше 256 молча недосайзит буфер (требует рекомпиляции).
4. **`COOLDOWN_GRANT_MARGIN_MS=1500`** всё ещё хардкод inline в `transfer.cpp:325`, хотя cooldown, который он правит (`cd_grant`), — per-reader. Несостыковка: margin над `lock_duration` нельзя тюнить per-reader.

### 2.2 Инвентаризация параметров

> Колонка «Should be» отражает целевой тир: **per-reader** (NVS/CFG[]), **per-hardware-class** (board-profile), **compile-time (Tier C)** — протокольно-фиксированный, *никогда* не параметризуется (сохранность conformance-векторов).

| Параметр | Текущее место | Scope сегодня | Должно быть | Bounds/units | Обоснование |
|---|---|---|---|---|---|
| `lock_duration_ms` | `g_state.lock_duration_ms` (`immutable.cpp:33`) | per-reader-NVS | per-reader | 500..10000 ms | Door-specific импульс замка; уже provisioned + clamped. |
| `nfc_detect` (poll timeout) | `g_state.cfg.nfc_detect` (CFG[]:44) | per-reader-NVS | per-reader | 20..500 ms | Отзывчивость tap vs RF-on duty; default = `NFC_DETECT_TIMEOUT_MS`. |
| `MAIN_LOOP_DELAY_MS` | `config.h:99` `#define` | **compile-time** | **per-reader (power knob)** | 10 ms (предл. 5..1000) | Loop-cadence = главный батарейный/duty-cycle knob; хардкод `main.cpp:118`, НЕ provisionable. |
| `ble_mtu` | `g_state.cfg.ble_mtu` (CFG[]:47) | per-reader-NVS | per-reader | 243..517 (floor ↔ `BLE_MAX_PDU=240`) | Negotiated MTU тюнится; on-wire PDU остаётся compile-time. |
| `BLE_MAX_PDU` | `config.h:168` `#define` | compile-time | **compile-time (Tier C framing)** | 240 B | Protocol-framing chunk; floor `ble_mtu` зависит от него. |
| `rf_atr` / `rf_retry` | `g_state.cfg.rf_atr/rf_retry` (CFG[]:38-39) | per-reader-NVS | **per-hardware-class** | 10..15 (PN532 reg) | PN532-specific RF-timing; в PN532-профиль (CLRC663 иной). |
| `pn532_retries` | `g_state.cfg.pn532_retries` (CFG[]:37) | per-reader-NVS | **per-hardware-class** | 1..255 | PN532 passive-activation retries; frontend-specific. |
| `field_pause` (RF reset) | `g_state.cfg.field_pause` (CFG[]:40) | per-reader-NVS | per-reader | 50..500 ms (min HW-derived) | RF off→on settle. |
| `reinit_thr` (PN532 reinit) | `g_state.cfg.reinit_thr` (CFG[]:43) | per-reader-NVS | per-reader | 1..20 | Порог reinit по подряд-сбоям. |
| `cd_end` (cooldown после END) | `g_state.cfg.cd_end` (CFG[]:35) | per-reader-NVS | per-reader | 500..10000 ms | Anti-double-tap. |
| `cd_grant` (cooldown после GRANT) | `g_state.cfg.cd_grant` (CFG[]:36) | per-reader-NVS | per-reader | 1000..15000 ms | OK. |
| `COOLDOWN_GRANT_MARGIN_MS` | `config.h:164` `#define` | **хардкод inline (`transfer.cpp:325`)** | **per-reader** | 1500 ms | Margin над `lock_duration`; хардкод, тогда как sibling `cd_grant` per-reader — несостыковка. |
| `bl_cap` (blacklist soft-cap) | `g_state.cfg.bl_cap` (CFG[]:54) | per-reader-NVS (soft) | per-reader, hard-max per-hw-class | 16..256 (=`LOCAL_BLACKLIST_CAP`) | Soft-cap только уменьшает; static-array на MAX → подъём = heap + recompile. |
| `LOCAL_BLACKLIST_CAP` | `config.h:36` `#define` | compile-time (hard max) | **per-hardware-class** | 256 | Сайзит `blacklist[]` (`reader_state.h:119`) и `RESULT_BUF_CAP`; RAM-bound. |
| `nonce_ring` (soft-cap) | `g_state.cfg.nonce_ring` (CFG[]:55) | per-reader-NVS (soft) | per-reader | 2..8 (=`NONCE_RING_SIZE`) | Soft-cap. |
| `NONCE_RING_SIZE` | `config.h:34` `#define` | compile-time (hard max) | **per-hardware-class** | 8 | Сайзит `nonce_ring[]` (`reader_state.h:122`). |
| `RESULT_BUF_CAP` | `config.h:159` `#define` | **compile-time, дублирован** | **derived из `bl_cap`** | 8704 B | Worst-case BLK-буфер; `ble_channel.cpp` ×5 + `transfer.cpp` ×3; не derived → латентный under-size. |
| `wl_max` / `bld_max` | `g_state.cfg.wl_max/bld_max` (CFG[]:33-34) | per-reader-NVS | per-reader | 32..2048 | Per-update whitelist / bl-delta caps. |
| `MAX_FILTER_BYTES` | `config.h:61` `#define` | compile-time (arith literal) | **per-hardware-class (flash geometry)** | 128*1024-1024 B | Bloom-flash ceiling; выводить из partition-geometry. |
| `TRANSFER_BUFFER_CAP` | `config.h:33` `#define` | compile-time | **per-hardware-class** | 16384 B | Non-filter transfer cap; RAM/flash-bound. |
| `clock_skew` | `g_state.cfg.clock_skew` (CFG[]:26) | per-reader-NVS | per-reader (coord. с backend) | 5..600 s | Security-window; match server policy. |
| `nonce_ttl_ms` | `g_state.cfg.nonce_ttl_ms` (CFG[]:27) | per-reader-NVS | per-reader (coord.) | 2000..60000 ms | Anti-replay; security. |
| `ts_drift` / `ts_boot` | `g_state.cfg.ts_drift/ts_boot` (CFG[]:30-31) | per-reader-NVS | per-reader (coord.) | 1..60 s/day; 3600..604800 s | TIME_SYNC SOFT-policy; security. |
| `nfc_deadline_ms` | `g_state.cfg.nfc_deadline_ms` (CFG[]:28) | per-reader-NVS | per-reader | 3000..30000 ms | Hard deadline tap-session. |
| `ble_idle_ms` | `g_state.cfg.ble_idle_ms` (CFG[]:29) | per-reader-NVS | per-reader | 5000..300000 ms | BLE idle-watchdog. |
| `passage_dir` | `g_state.cfg.passage_dir` (CFG[]:32) | per-reader-NVS | per-reader | 1=entry / 2=exit | Entry/exit-роль; был топ-gap, теперь provisioned (дефолт entry). |
| `push_retries` / `push_delay` | `g_state.cfg....` (CFG[]:41-42) | per-reader-NVS | per-reader | 1..10; 10..500 ms | PUSH_INFO retry tuning. |
| `ble_info_defer` | `g_state.cfg.ble_info_defer` (CFG[]:48) | per-reader-NVS | per-reader | 10..500 ms | Deferred INFO push после connect. |
| `handover_required` | `g_state.cfg.handover_required` (CFG[]:58) | per-reader-NVS | per-reader | 0/1 | T3 fail-closed гейт для BLE FILTER_UPDATE. |
| `ble_enabled` | `g_state.ble_enabled` (`immutable.cpp:38`) | per-reader-NVS | per-reader (= power type) | bool | BLE-radio gate; ↔ mains-vs-battery build (`SCUD_BLE_ENABLED`). |
| Пины: PN532 UART 16/17, I2C 21/22, LOCK 26, LED 2, BTN 0 | `config.h:9-30` `#define` | **хардкод compile-time** | **per-hardware-class** | GPIO | Board-wiring; нет `g_state`/NVS/SET-* — блокирует своп frontend. Нужны `board_profiles/*.h`. |
| `*_ACTIVE_HIGH` (lock/LED) | `config.h:22,27` `#define` | **хардкод compile-time** | **per-hardware-class** | bool | Полярность strike/LED; board-profile. |
| `PN532_UART_BAUD` / `I2C_FREQ` | `config.h:11,18` `#define` | **хардкод compile-time** | **per-hardware-class** | 115200 / 400000 Hz | Bus-tuning; frontend/board-specific. |
| `MAX_APDU_DATA_SIZE` | `config.h:58` `#define` | compile-time (HW-derived) | **compile-time (Tier C)** | 240 B | PN532 FSC=256 + uint8 responseLength; protocol-fixed. |
| `PROTOCOL_VERSION` / AID | `config.h:54,86` `#define` | compile-time | **compile-time (Tier C)** | 1 / 6-байт AID | Protocol identity; никогда не параметризуется. |

### 2.3 План «закрытия» параметризации

Работать тиры в этом порядке.

**TIER B (hardware-class — блокер свопов, делать первым).**
1. Создать `ESP32/firmware/src/board_profiles/` с одним header на board/frontend-класс (напр. `esp32_pn532.h`, `esp32_clrc663.h`), определяющим набор пин/полярность/baud/I2C-freq из `config.h:8-30`; выбор через PlatformIO build-flag (`-D BOARD_PROFILE_*`), зеркаля существующий `SCUD_BLE_ENABLED`-сплит.
2. Ввести тонкий HAL-seam, чтобы NFC-frontend был сменным (детали — раздел 4): вынести `PN532_RF_TIMING_*` и `MAX_APDU_DATA_SIZE` в PN532-профиль (у CLRC663 иные FSC/timing).
3. Аналогично RTC: держать DS3231 @0x68 за `hw/rtc.h`, адрес/драйвер — board-profile-выбор.
4. Генерировать профили из desktop per-device-type-template (`docs/11` §6.2).

**TIER A follow-ups (консистентность/корректность).**
5. Перенести `COOLDOWN_GRANT_MARGIN_MS` в CFG[] как per-reader (`cd_grant_margin`, ~500..5000).
6. Добавить `MAIN_LOOP_DELAY_MS` как per-reader (`loop_delay_ms`) — первичный duty-cycle knob; спарить с будущим light-sleep-гейтом в `loop()`.
7. Вывести `RESULT_BUF_CAP` из активного `blacklist_cap` + `static_assert`, привязанный к `LOCAL_BLACKLIST_CAP`; дедупнуть до одной header-константы.
8. Для настоящего heap-backed подъёма caps — конвертировать `reader_state.h:119/122` static-массивы в heap-аллокацию по provisioned cap на буте (`docs/11` §6 caveat).
9. Вывести `MAX_FILTER_BYTES` / `TRANSFER_BUFFER_CAP` из flash-partition-geometry per board-profile.

**TIER C (НЕ трогать).** `PROTOCOL_VERSION`, `MAX_APDU_DATA_SIZE`, AID, все offsets/opcodes, `BLE_MAX_PDU`-framing, filter-file-пути — compile-time ради conformance-векторов. Единственная уборка — латентный DRY-баг «литерал 240 vs `MAX_APDU_DATA_SIZE`» (`docs/11` §6.4, `transfer.cpp:102,429`) как no-behavior-cleanup.

**DB/override.** Расширить `ConfigParamCatalog` строками `cd_grant_margin` / `loop_delay_ms` (каталог сегодня — точное ×27-зеркало CFG[]); добавить отдельный per-device-type-template, эмитящий `board_profiles`-header для Tier-B.

> **Инвариант общего протокола.** Любое Tier-A security-окно (`clock_skew`, `nonce_ttl`, `ts_drift`, `ts_boot`), provisioned на ридере, должно валидироваться против backend-policy при enroll — они координируются с сервером (`docs/11` §6.1, MEMORY shared-protocol).

---

## 3. Энергопотребление и автономный режим

### 3.1 Где сейчас уходит ток

`main.cpp:109-119` `loop()` = `apdu_detect_target()` + `ble_loop_tick()` + cleanups + `delay(10)`. `apdu_detect_target` (`apdu.cpp:37-41`) вызывает `readPassiveTargetID(..., timeout=100ms)`, который держит **RF-поле PN532 включённым** на время poll. Поле выключается **только** в cooldown-окне после tap (`transfer.cpp:707-708 if (cooldown) apdu_field_off()`) и снова энергизуется следующим poll. То есть в idle ридер **field-ON ~100% времени**, а не field-OFF.

- **MCU-sleep отсутствует полностью.** Grep `esp_sleep|light_sleep|deep_sleep|modem_sleep` по `ESP32/firmware/src` → 0 совпадений. ESP32 всегда в active-mode на 240 MHz.
- **BLE рекламирует непрерывно** на дефолтном fast-интервале NimBLE (~20–40 мс): `setMinInterval/setMaxInterval` нигде не вызываются (grep → 0). `ble_channel.cpp:736 startAdvertising()` без предварительной установки интервала; `onDisconnect` (`:497`) рестартит рекламу.
- **Замок** (`lock.cpp:10-23`) — one-shot per grant: энергизует пин, `vTaskDelay(duration_ms)`, де-энергизует. Средний вклад мал (если только это не fail-secure strike / maglock, держащийся под током для блокировки — тогда непрерывная нагрузка).
- **DS3231** на VCC-питании (I2C) — ~0.1–0.2 мА timekeeping; <0.2 % бюджета. Знаменитые ~3 мкА — только VBAT-backup-путь при снятом VCC.

**Конфликт «BLE-always-on» vs «MCU-sleep».** Deep-sleep сносит NimBLE-стек и RAM-state (`g_inbound`) и ресетит → несовместим с персистентным BLE-коннектом. Поэтому BLE-always-on и MCU-deep-sleep **взаимоисключающи**; `platformio.ini:36-37` уже фиксирует этот сплит («BLE env never used on battery — BLE radio kills autonomy»).

### 3.2 Бюджет мощности — DATASHEET-ОЦЕНКИ (НЕ измерено)

> **Все токи ниже — типовые datasheet/vendor-цифры на указанной шине. Это планировочные оценки: мультиметра/PPK не было.** Реальный ток зависит от напряжения шины, регуляторов платы (red-board PN532: AMS1117 + level-shifters добавляют quiescent), настройки антенны и нагрузки tag. Перед любым тезисным утверждением — ИЗМЕРИТЬ (см. §3.4).

#### Профиль A — СЕГОДНЯ (always-on, как реализовано)

| Компонент | Состояние | Est. ток (datasheet) | Примечания |
|---|---|---|---|
| ESP32 (BLE-build) | Active CPU + BLE adv непрерывно (~20–40 мс) | ~95–130 мА avg (пики ~240 мА на TX) | env `esp32dev_ble`; не тюнен. DS: active 95–240 мА; BLE TX@0dBm ~130 мА, RX ~95–100 мА |
| ESP32 (NFC-only build) | Active CPU, радио off | ~40–70 мА | env `esp32dev`; sleep всё равно нет, `loop()` крутится на 240 MHz |
| PN532 RF-frontend | Поле энергизовано на каждом 100 мс poll, ~100 % idle-duty | ~100–150 мА | UM0701-02 / community ~100 мА typ, до ~140–160 мА на read. Off только в cooldown |
| DS3231 RTC | Timekeeping на VCC (I2C) | ~0.1–0.2 мА | DS: active ~200 мкА, timekeeping 110–170 мкА. (VBAT-only ~3 мкА — не эта разводка) |
| Электрозамок / реле | Энергизован только в grant (one-shot `lock_duration_ms`, def 3 s) | ~0.5 A while open, ~0 avg | one-shot. 12V strike ~0.51 A; maglock ~0.5 A continuous-hold. Avg≈0 если не fail-secure/maglock |
| **ИТОГО (BLE, idle, lock avg≈0)** | **always-on** | **≈200–280 мА непрерывно** | **Доминируют PN532-field + ESP32-BLE. ~24 ч на 5000 mAh.** |
| **ИТОГО (NFC-only, idle)** | **always-on** | **≈140–220 мА непрерывно** | Отказ от BLE экономит ~60–130 мА, но PN532-field всё равно доминирует |

#### Профиль B — OPTIMIZED (duty-cycle / LPCD-frontend / sleep)

| Компонент | Состояние | Est. ток (datasheet) | Примечания |
|---|---|---|---|
| ESP32 | Light-sleep между событиями, modem duty-cycled | ~0.8 мА sleep + короткие active → ~2–10 мА avg | light-sleep ~0.8 мА, <1 мс wake. Wake on NFC-IRQ (GPIO) / timer |
| ESP32 | Deep-sleep, wake on NFC-IRQ / RTC | ~10 мкА (timer) / ~50–100 мкА (ext0 GPIO) | Теряет BLE-стек + RAM на wake — только NFC-only, infrequent-use |
| NFC-frontend (PN532, partial) | Hard-powerdown между polls, field-on только poll | ~1–22 мкА sleep, ~100 мА на poll-burst | InAutoPoll/powerdown режет idle, но ВСЁ РАВНО энергизует поле для детекта → частичная экономия |
| NFC-frontend (CLRC663/PN5180, true LPCD) | Поле OFF, capacitance/threshold wake | CLRC663 ~3–6 мкА; PN5180 ~15–35 мкА | TRUE presence-wake с полем off — требует свопа frontend + IRQ-wake переписки `apdu.cpp` |
| BLE | Intermittent adv (AirTag-style, ~1000–2000 мс) | ~1–5 мА avg (vs ~60–130 мА непрерывно) | `setMinInterval/setMaxInterval` в 0.625 мс единицах; trade-off = latency обнаружения телефоном |
| DS3231 RTC | Timekeeping (без изменений) | ~0.1–0.2 мА | Уже negligible; VBAT-only ~3 мкА |
| **ИТОГО (optimized, idle)** | **duty-cycled** | **≈0.05–10 мА idle (зависит от frontend+BLE-стратегии)** | **20–1000× ниже idle, чем Профиль A** |

### 3.3 Roadmap экономии — три оси

#### Ось (a) BLE — непрерывная → intermittent (AirTag-style)
Сейчас `ble_channel.cpp:736 startAdvertising()` на дефолтном fast-интервале (~20–40 мс), не тюнен. **Fix (LOW effort, MEDIUM saving):** перед `startAdvertising()` в `ble_init()` и в рестарт-пути `onDisconnect` (`:497`) задать большой интервал:
```cpp
g_adv->setMinInterval(1600);  // 1600 * 0.625ms = 1000 ms
g_adv->setMaxInterval(2400);  // 2400 * 0.625ms = 1500 ms
```
(NimBLE-Arduino 1.4.x: единицы 0.625 мс; 0 = default; `setAdvertisingInterval(uint16_t)` ставит оба.) Вынести в `config.h` как `BLE_ADV_MIN_UNITS`/`BLE_ADV_MAX_UNITS` рядом с `BLE_*`, чтобы стали provisionable как `ble_mtu`/`ble_idle_ms`.
**Экономия:** средняя мощность радио ~линейна по adv-duty; переход ~30 мс → ~1000–1500 мс режет adv-события ~30–50×, опуская BLE-вклад с ~60–130 мА к нескольким мА avg.
**Trade-off:** скан телефона дольше обнаруживает ридер (до ~1.5 с). Для tap-to-enter UX — borderline. **Рекомендация:** two-tier — SLOW (1–2 с) когда нет недавнего tap, и на NFC-tap (или issue handover_token, `ops/handover.cpp`) кратко переключаться в FAST (~100–300 мс) на короткое окно. Это стыкуется с существующим NFC→BLE handover (`BLE_CAP_HANDOVER` уже в mfg-data, `ble_channel.cpp:717`).

#### Ось (b) NFC — убрать непрерывное поле
Сейчас field-on polling; PN532 **не имеет** настоящего LPCD.
- **Option B1 (LOW-MED effort, PARTIAL saving) — PN532 powerdown duty-cycle:** между polls слать PN532 PowerDown (soft ~22 мкА) и будить перед poll; или InAutoPoll (self-poll). Оба ВСЁ РАВНО энергизуют поле для реального детекта → экономится только inter-poll idle, не сам poll. Добавить `apdu_powerdown()`/`apdu_wake()` в `apdu.cpp` по образцу `apdu_field_off()`. Текущее железо.
- **Option B2 (HIGH effort, LARGE saving) — своп frontend на true-LPCD:** CLRC663 plus (~3–6 мкА LPCD, целевой), PN5180 (~15–35 мкА) или ST25R3916. Будят по capacitance/threshold с **полем OFF** и поднимают IRQ. `apdu.cpp` уже чисто абстрагирован (6 функций), `main.cpp` зовёт только `apdu_detect_target` + `run_tap_session` → polling-модель сменна. Шаги: (1) NFC-HAL-интерфейс под 6 функций; (2) CLRC663-backend (SPI; PN532-HSU-пины освобождаются); (3) заменить poll в `loop()` на IRQ-driven wake. Crypto/op-слои frontend-agnostic — изменений НЕ требуют (сидят на `apdu_exchange`). Главный риск — Android-HCE timing (сверить T=CL-параметры с текущим `setRfTimings`, `apdu.cpp:32`, `fATR_RES=0x0F` ~3.28 s для медленных HCE-телефонов). Boards: NXP CLEV6630B (CLRC663 plus eval), PN5180-модули, ST25R3916 discovery.

#### Ось (c) MCU — sleep между событиями
Сейчас sleep'а нет; `loop()` крутится на 240 MHz с `delay(10)`.
- **C1 (MED effort, MED saving, BLE-compatible):** автоматический light-sleep (`esp_pm_config_t` с `light_sleep_enable=true`, или `esp_light_sleep_start()` в idle-ветке `loop()`). CPU → ~0.8 мА между событиями; wake по timer. Комбинировать с осью (a).
- **C2 (MED effort, LARGE saving, NFC-only):** deep-sleep, wake on NFC-frontend IRQ (ext0 на LPCD-IRQ GPIO из оси b) или timer; DS3231 INT/SQW (сейчас не подключён, только I2C) тоже может служить RTC-alarm-wake. На wake: `setup()` → один detect → handle → sleep. ~10–100 мкА sleep-floor.
- **Конфликт для документации:** нельзя иметь ОДНОВРЕМЕННО always-on BLE И MCU-deep-sleep — выбор per-install. `platformio.ini` уже кодирует сплит. **Рекомендация:** формализовать два power-профиля в `config.h`, управляемых `SCUD_BLE_ENABLED`.

#### Упорядоченный список оптимизаций (effort vs saving)
1. **BLE intermittent advertising** (ось a) — LOW effort, MED saving. ~3 строки + 2 конст. **Делать первым.**
2. **PN532 powerdown duty-cycle** (ось b, B1) — LOW-MED, PARTIAL. Переиспользует `apdu.cpp`; без нового железа.
3. **ESP32 auto light-sleep** (ось c, C1) — MED, MED. BLE-compatible; парится с #1.
4. **ESP32 deep-sleep + RTC/NFC-IRQ wake** (ось c, C2) — MED, LARGE, но BLE-incompatible.
5. **Своп на CLRC663/PN5180 true-LPCD** (ось b, B2) — HIGH (новый HAL + плата), LARGEST. Единственный путь к настоящему field-off idle; `apdu.cpp` — уже правильный seam.

### 3.4 Как ИЗМЕРИТЬ (заменить оценки)

| Метод | Что захватывает | Примечания |
|---|---|---|
| USB inline power meter (USB-C тестер) | Whole-board 5V, грубо | Быстрый sanity-check; ~10 мА разрешение, без transient |
| Bench DMM в разрыве 5V (мкА–A) | Steady-state per-profile | Burden-voltage + range-switch теряет быстрые пики; для idle/field-on avg |
| Current-sense shunt + scope, или Nordic PPK2 / Joulescope / Otii Arc | Transient: poll-bursts, BLE-adv-пики, lock-inrush, sleep-floor | Обязателен для duty-cycled профилей; интегрировать в mAh/день. Мерить каждую шину отдельно (ESP32 3V3, PN532 5V, lock 12V) |

> **Перед любым тезисным заявлением** замените оценки реальными измерениями: PPK2/Joulescope/Otii по per-rail transients, интеграл в mAh/день. Datasheet-числа здесь — планировочные.

---

## 4. Портируемость RTC и NFC (PN532 → CLRC663)

### 4.1 Что уже есть

И RTC, и NFC-frontend **уже за тонким seam свободных функций**: `rtc.h` (`rtc_init/rtc_now/rtc_set`); `apdu.h` (`apdu_init/apdu_detect_target/apdu_exchange/apdu_field_off/apdu_reset_field/apdu_reinit_pn532`). Полный grep подтверждает **0 утечек** типов DS3231/RTClib/PN532/`nfc.` за пределы `rtc.cpp`/`apdu.cpp`: все 14 `nfc.`-обращений — внутри `apdu.cpp`; все 17 time-call-site (`access.cpp:63`, `time_sync.cpp:59`, `fdi.cpp`, `handover.cpp`, `passage.cpp`...) зовут только `rtc_now/rtc_set`. BLE-транспорт полностью развязан с frontend (grep `apdu_/nfc./PN532` по `ble_channel.cpp` → 0 NFC) — своп frontend **не может** регрессировать BLE.

Две реальные утечки — косметические/структурные: `Wire.begin()` живёт в `main.cpp:52-53` («I2C for DS3231 only»), а не в `rtc_init()`; и сам surface `apdu.h` назван по чипу (`apdu_reinit_pn532`, «PN532» в комментариях/логах/config-ключах).

### 4.2 Матрица портируемости

| Concern | DS3231/PN532 сегодня | Coupling | HAL-метод | CLRC663/other delta | Effort |
|---|---|---|---|---|---|
| Time read | `rtc_now()` → `RTC_DS3231.now().unixtime()` (`rtc.cpp:23-27`) | Loose (free-fn, 17 call-site, без утечки) | `IRtc::now()->uint64` (0=unknown) | Любой RTC: вернуть unix-сек, чтить 0-sentinel (`config.h:125`) | Low |
| Time set | `rtc_set()` → `rtc.adjust` (`rtc.cpp:29-33`) | Loose | `IRtc::set(epoch)` | Тривиально per chip | Low |
| RTC init / bus | `rtc_init()` + `Wire.begin` в `main.cpp:53` | Loose API, tight bus (Wire в main) | `IRtc::init()` владеет своей шиной | I2C→SPI (напр. RV-3028) → перенести bus-init в backend | Low |
| NFC init / link | `apdu_init`: HSU UART2 + PN532 (`apdu.cpp:9-35`) | Loose API / link в init | `INfcFrontend::init()` | HSU→SPI; новый transport; `config.h`-пины | Med |
| Detect + activate | `apdu_detect_target`: readPassiveTargetID + SELECT-AID (`apdu.cpp:37-56`) | Loose (caller только `main.cpp:110`) | `INfcFrontend::detectTargetAndSelect()` | PN532 прячет anticoll/RATS; CLRC663 нужен явный REQA/anticoll/RATS/PPS | Med-High |
| APDU exchange (ISO-DEP) | `apdu_exchange` → `nfc.inDataExchange` 0x40 (`apdu.cpp:58-62`; `PN532.cpp:805`) | Loose API; framing спрятан в PN532-fw | `INfcFrontend::exchange(cmd,resp)` | **САМОЕ КРУПНОЕ:** у CLRC663 нет T=CL-стека — host сам делает I-block chaining/CRC/timeouts | **High** |
| Field off / reset | setRFField off / off+pause+on (`apdu.cpp:64-75`) | Loose | `fieldOff()` / `fieldReset()` | Map в CLRC663 TxControl-регистры; держать ≥50 мс pause-контракт | Med |
| RF tuning | setRfTimings / setPassiveActivationRetries (`apdu.cpp:28-32`) | Loose API; PN532 RFConfiguration | (init-params) | Нет RFConfiguration-команды; map в timer/threshold-регистры; `rf_atr/rf_retry/pn532_retries` теряют смысл | Med |
| Reinit / recovery | `apdu_reinit_pn532` (`apdu.cpp:77-91`) | Loose; chip-имя в public-символе | `INfcFrontend::reinit()` | Переименовать; CLRC663 = soft-reset + reconfig | Low |
| Power-down / LPCD | нет (always-on poll, `main.cpp:110`, поле активно) | **Отсутствует в HAL** | `powerDown()` / `wakeOnCard()` (new) | CLRC663 имеет реальный LPCD wake-on-card; PN532 только power-down/InAutoPoll — ключ к батарее | **High** |
| APDU size limits | 240B APDU / 146B PUSH_CHUNK (`transfer.cpp:107-121`, `config.h:58`) | Tight к PN532-FSC, эмпирично | n/a (protocol-конст) | Перемерить на CLRC663 FSD/FSC; hardware-only, ветка compile-only | Med (HW) |
| BLE transport | `dispatch_op` only, без NFC (`ble_channel.cpp:234-245`) | Decoupled | n/a | Не затрагивается свопом | None |

### 4.3 Предлагаемые HAL-интерфейсы и план

**Шаг 0 (решение).** Seam уже чистая граница — два эквивалентных варианта: **(A)** оставить C-style seam, добавить второй `.cpp`-impl по build-flag (минимальный churn, рекомендуется для ВКР); **(B)** поднять до C++-интерфейсов `INfcFrontend`/`IRtc` (учебниковая HAL-диаграмма). Оба оставляют все call-site нетронутыми.

**RTC-seam (low risk, ~1–2 файла).**
1. Вынести bus-bring-up из `main.cpp:53` в `rtc_init()` (guard против double-begin, шину могут делить другие I2C-peers). Убирает coupling-комментарий «DS3231 only».
2. (A) `hw/rtc_ds3231.cpp` (текущее тело) + sibling `hw/rtc_<other>.cpp`, выбор `-D RTC_BACKEND`. (B) `IRtc { virtual bool init(); virtual uint64_t now(); virtual bool set(uint64_t); }`, три свободные функции — тонкие forwarder'ы к `g_rtc`.
3. Задокументировать контракт sentinel: `rtc_now()==0` (`config.h:125 RTC_DEAD_SENTINEL`, потребляется `access.cpp:63-64`) — любой backend обязан вернуть 0, когда время неизвестно.

**NFC-HAL-seam (moderate, ~2–4 файла, без изменения поведения).**
4. Убрать chip-specific имя из public-surface: `apdu_reinit_pn532()` → `apdu_reinit()` (`apdu.h`, `apdu.cpp:77`, единственный caller `transfer.cpp:362`). «PN532» — в internal log/detail.
5. (B) Выразить surface как `INfcFrontend { init(); detectTargetAndSelect(timeout); exchange(cmd,len,resp,cap)->len; fieldOff(); fieldReset(); reinit(); powerDown(); wakeOnCard()/LPCD }`. `Pn532Frontend` (текущее тело `apdu.cpp`) за интерфейсом; свободные функции форвардят к `g_nfc`.
6. **Добавить power/LPCD-методы в интерфейс СЕЙЧАС**, даже если PN532 реализует их no-op'ами (`powerDown()` → setRFField off + InPowerDown; `wakeOnCard()` → return false). Это резервирует seam под батарейную работу и избегает второго API-churn.

**CLRC663-драйвер (тяжёлый пункт, ~4–8 новых файлов, риск framing).**
7. **Bus-слой:** SPI-transport для CLRC663 (`config.h` получает `CLRC663_SPI_*`-пины; заменить PN532-HSU-construction в init нового frontend).
8. **T=CL — make-or-break.** Либо (a) портировать/vendor'ить NXP NFC Reader Library iso14443p3+p4 (T=CL) и звать из `exchange()`, либо (b) hand-roll REQA/anticoll/RATS/PPS + I-block chaining + CRC поверх register-level TX/RX. Map `apdu_detect_target` → REQA/anticoll/RATS затем SELECT-AID; `apdu_exchange` → один T=CL I-block round-trip с chaining. Сверять с существующей последовательностью в `transfer.cpp` (PUSH_INFO/FETCH/READ_CHUNK/PUSH_CHUNK) — эмпирический 240-байт APDU-ceiling и 146-байт PUSH_CHUNK (`transfer.cpp:107-121`) суть **PN532-FSC-артефакты** и должны быть перехарактеризованы на FSD/FSC CLRC663.
9. **Field-control:** `fieldOff/fieldReset` → CLRC663 TxControl/DrvMode-регистры (off then on, сохраняя ≥50 мс pause-контракт `apdu.cpp:67-69`, дающий Android-HCE увидеть деактивацию).
10. **Tuning:** транслировать семантику `rf_atr/rf_retry/pn532_retries` в CLRC663 timer/threshold-регистры, либо deprecate эти ключи для CLRC663-build.
11. **LPCD:** реализовать `wakeOnCard()`/`powerDown()` по-настоящему на CLRC663, переструктурировав `main.cpp:110` из busy-poll в LPCD-interrupt-driven wake — прямая связь с батарейной задачей.

**Оценка усилий.** RTC-своп — **Low** (single-file port). NFC-HAL-seam (переименование + интерфейсы) — **Low-Med**, без изменения поведения. CLRC663-драйвер — **High**: highest risk = T=CL-корректность и per-platform APDU-лимиты (hardware-only verifiable, ветка compile-only); medium = LPCD-timing vs Android-HCE re-activation; low = SPI-bring-up. **Гейтить CLRC663-build за отдельным platformio-env**, чтобы проверенный PN532-path оставался default.

---

## 5. UI-дефекты

### 5.1 UI-1 — «0/1, но выпустить нельзя» (рассинхрон определения счётчика, НЕ sync-сбой)

**Диагноз.** Симптом — не sync-сбой: revocation-state **синхронизируется** (`revokeOnServer()` ставит локальный ключ в `REVOKED_BY_SERVER`, `KeysRepository.kt:101`; `refreshForPermit()` мапит серверный статус обратно, `:79-86`; row-badge `keys_status_revoked_server` рендерится, `KeysScreen.kt:524`). Проблема — **три разных определения счётчика слотов**:

| Слой | Источник счётчика | Считает `revoked_by_server`? | Значение после server-revoke (n_parallel=1) | File:line |
|---|---|---|---|---|
| KeysScreen карточка «0/1» | local `state.activeCount` (`isUsable`) | **Нет** | 0 / 1 (выглядит свободно) | `KeysViewModel.kt:74,93`; `KeysScreen.kt:174` |
| Request-button gate | `activeCount < nParallel` | **Нет** | enabled (0<1) — но сервер отклоняет | `KeysScreen.kt:187` |
| Backend issue-gate | `count_active_keys` (`is_active`) | **Да** | 1 ≥ 1 → HTTP 409 `n_parallel_exceeded` | `domain/keys.py:92-94`; `permits.py:148-163` |
| PermitsScreen/Detail | server `permit.activeKeysCount` | **Да** | 1 / 1 (корректно) | `PermitsScreen.kt:300`; `PermitDetailScreen.kt:64` |
| `revoke_key.is_active`-флаг | status not in (reader,bloom,expired) | True для `revoked_by_server` | `is_active=True` | `repositories/keys.py:67` |

`KeysScreen` считает свой `activeCount` локально через `isUsable()`, которое **исключает** `revoked_by_server` (`IssuedKeyEntity.kt:31`), → карточка «0/1» и кнопка enabled. Но backend-gate `count_active_keys()` считает `is_active=True`, который `revoked_by_server` **включает by design** (server-revoked ключ намеренно занимает слот, пока ридер не применит новый bloom и worker не флипнет его в `revoked_in_bloom`) → `issue_key()` бросает 409. Держать server-revoked в active-count **сознательно** (офлайн-ридер физически принимает ключ до прихода bloom — `permits.py:149-157` docstring). Поэтому fix — **на стороне UI**, не ослабление гейта (ослабление пустило бы `n_parallel+1` физически-валидных ключей на офлайн-ридер — security-регресс).

**План fix (минимально-корректный = STEP 1–3).**
- **STEP 1** — в `IssuedKeyEntity.kt:29-31` добавить рядом с `isUsable()`:
  ```kotlin
  fun occupiesSlot(): Boolean =
      status == IssuedKeyStatus.ACTIVE || status == IssuedKeyStatus.REVOKED_BY_SERVER
  ```
  (зеркалит backend `is_active`: всё кроме reader/bloom/expired). `isUsable()` оставить — tap-логика НЕ должна использовать revoked-ключ.
- **STEP 2** — в `KeysViewModel.kt` добавить `val slotCount: Int = 0` в `KeysUiState`; в combine-блоке (`:70-95`) `val slotUsed = allKeys.count { it.occupiesSlot() }`, set `slotCount = slotUsed`. `activeCount = active.size` оставить (драйвит «usable for tap» filter-chips).
- **STEP 3** — в `KeysScreen.kt`: `:174` → использовать `state.slotCount` (карточка читает «1/1»); `:187` → `enabled = state.slotCount < permit.nParallel` (кнопка корректно DISABLED, пока server-revoked держит слот). Filter-chips (`:201,:206`) могут оставить `activeCount` («usable now» — легитимно иной концепт).
- **STEP 4 (UX, рекомендуется)** — под disabled-кнопкой показать, ПОЧЕМУ слот занят: новая строка `keys_slot_pending_bloom` («Отозванный ключ занимает слот, пока ридер не обновит bloom-фильтр»), переиспользуя формулировку `keys_dialog_revoke_server_subtitle` (`strings.xml:107`). Добавить в `values/` и `values-ru/`.
- **STEP 5 (defense-in-depth)** — в `RequestKeyUseCase.invoke` (`:42-48`) / `KeysViewModel.requestKey onFailure` (`:142-144`) мапить `detail.error == "n_parallel_exceeded"` в ту же дружелюбную slot-pending-формулировку (покрывает race, когда слот заполняется между refresh и tap).
- **STEP 6 (опц. cleanup)** — `PermitDao.recomputeActiveCounts` (`:21-30`) использует ТРЕТЬЕ определение (`expiresAtMs > now`, игнорируя status); если оно что-то гейтит — выровнять к `occupiesSlot`, иначе полагаться на серверный `activeKeysCount`.

**Нетто после STEP 1–3:** KeysScreen показывает «1/1» с disabled-кнопкой, пока server-revoked pending bloom, и авто-re-enable в «0/1» только после применения bloom и флипа в `revoked_in_bloom`. Убирает «выглядит свободно, но выпустить нельзя» без over-issuance-дыры на офлайн-ридере.

### 5.2 UI-2 — увеличенный верхний «козырёк» (двойной учёт status-bar inset)

**Диагноз.** Top «brow» вызван **двойным применением** status-bar (top) WindowInset двумя вложенными inset-consuming Scaffold:

| Слой | File:line | Консумит top status-bar inset? | Эффект |
|---|---|---|---|
| Activity edge-to-edge | `MainActivity.kt:38` | enables draw за status bar | needed |
| Outer Scaffold | `MainActivity.kt:41,44` | **ДА** (padding применён) | +1× top inset (корректно) |
| Inner AppScaffold Scaffold | `AppScaffold.kt:103,106` | **ДА** (padding применён снова) | +1× top inset (**БАГ: удвоено**) |
| Inner screen TopAppBar | `PermitsScreen.kt:93` и др. | НЕТ (`WindowInsets(0,0,0,0)`) | корректно предполагает, что родитель уже заинсетил один раз |
| HomeScreen (нет Scaffold) | `HomeScreen.kt:97` | НЕТ | показывает весь удвоенный gap как пустое место |

`MainActivity` зовёт `enableEdgeToEdge()` (`:38`) и оборачивает всё во внешний Scaffold, чей `contentPadding` уже включает top-inset, применяя его через `Modifier.padding(padding)` (`:41,44`). Контент — `ScudNavHost`, который сразу оборачивает NavHost во ВТОРОЙ Scaffold (`AppScaffold`, `:103,106`), тоже репортящий top-inset и применяющий его снова. Доказательство double-count: inner per-screen Scaffolds **намеренно** ставят `TopAppBar windowInsets = WindowInsets(0,0,0,0)` (`PermitsScreen.kt:93`, `KeysScreen.kt:109`, `TasksScreen.kt:68`, `SettingsScreen.kt:70`) — т.е. авторы рассчитывали, что родитель консумит inset ровно один раз. Симптом ярче всего на `HomeScreen` (нет inner Scaffold/TopAppBar поглотить gap). Тема не виновата (`themes.xml:4` — стандартный NoActionBar, edge-to-edge из кода); нет `fitsSystemWindows`/`windowSoftInput`-мисконфига (grep чист).

**План fix — выбрать ОДИН.**
- **OPTION A (рекомендуется, 1 строка + 1 import).** Сказать inner Scaffold не консумить system-bar insets, т.к. родитель уже это сделал. В `AppScaffold.kt:103`:
  ```kotlin
  Scaffold(
      contentWindowInsets = WindowInsets(0, 0, 0, 0),
      bottomBar = { if (showBar) BottomNavBar(navController) },
  ) { padding ->
  ```
  + `import androidx.compose.foundation.layout.WindowInsets`. После: inner Scaffold вносит 0 system-bar inset; единственный top-inset — из MainActivity-Scaffold. «Brow» сжимается до одной status-bar-высоты. **Watch-out:** проверить нижний край — outer Scaffold применяет nav-bar inset, а `NavigationBar` self-insets; убедиться, что bottom-nav не double-padded на gesture-nav.
- **OPTION B (чище архитектурно).** Сделать MainActivity-Scaffold НЕ консумящим (`contentWindowInsets = WindowInsets(0,0,0,0)` на `:41`, или вообще убрать внешний Scaffold и звать `ScudRoot` напрямую с `Modifier.fillMaxSize()`), оставив `AppScaffold` единственным inset-consumer. Тидийнее (один Scaffold владеет top+bottom), но трогает структуру MainActivity.

**НЕ менять** четыре inner-экрана (`PermitsScreen.kt:93` и др.) — их `WindowInsets(0,0,0,0)` корректны при обоих вариантах. **Верификация:** Home — greeting сразу под status-bar без широкой пустой полосы; Permits/Settings — title app-bar сразу под status-bar; проверить на устройстве с notch, что top-inset применён один раз.

> **Рекомендация на будущее:** стандартизировать ОДИН inset-owning Scaffold (дух Option B), задокументировать конвенцию, чтобы новые экраны держали `TopAppBar windowInsets=WindowInsets(0,0,0,0)`. Рассмотреть screenshot-тест верхнего spacing'а HomeScreen против регрессий.

---

## 6. Чек-лист готовности и сценарии проверки

> Каждый пункт: ДЕЙСТВИЕ → ОЖИДАЕМОЕ. Цель — реальный ESP32 + телефон + backend.

**GROUP 0 — SMOKE.**
- **S1** Прошить `esp32dev` и `esp32dev_ble`, power-on → serial: `[BOOT]` reset-reason, `[PN532]` fw, `[BLE]` up/off, `[READY]`.
- **S2** Непровиженный ридер → `provisioning_loop`, `[PROVISIONING]` (`main.cpp:74-77`), LED-blink.
- **S3** Полная SET-* серия из desktop-template + COMMIT; STATUS read-back показывает каждый cfg-ключ.
- **S4** Hold boot-button на power-on → forced provisioning (`main.cpp:79-82`).

**GROUP 1 — NFC FUNCTIONAL (регрессия, уже HW-proven).**
- **N-1** Валидный tap → ACCESS GRANTED, замок на `lock_duration_ms`, VERDICT=OK (42 B) + 192 B receipt (resp 234 B, `access.cpp:110-117`).
- **N-2** `passage_receipt` (`response[42:234]`) аплоадится, backend верифицирует под DOMAIN_PSG.
- **N-3** FDI tap → 241 B envelope, sealed-box courier-blob дешифруется на сервере.
- **N-4** TIME_SYNC valid grant+statement → RTC скорректирован, serial old→new delta (`transfer.cpp:643-648`).
- **N-5** REVOKE_KEY (407 B) → ключ в local blacklist, далее ACCESS=REVOKED_BLACKLIST.
- **N-6** GET_BLACKLIST → PUSH_CHUNK-серия (131 B) собирается на сервере.
- **N-7** FILTER_UPDATE small (<16 KB) RAM-path → A/B swap, delivery_receipt 112 B, новый `filter_version`.

**GROUP 2 — BLE FUNCTIONAL (только ACCESS HW-proven; остальное — первый рантайм-pass).**
- **B-1** Scan → ридер виден по Service UUID; mfg-data `0xC0DE` несёт `short_reader_id` + caps `BLE_CAP_BULK|BLE_CAP_HANDOVER` (`ble_channel.cpp:711-718`).
- **B-2** Connect, enable RESULT_NOTIFY → INFO push один раз на subscribe (146 B, `:624-631`).
- **B-3** BLE ACCESS → VERDICT, замок, cooldown via `apply_access_result` (регрессия).
- **B-4** op_seq-корреляция: два ops подряд, каждый RESULT_NOTIFY первым байтом эхоит `op_seq` (`ble_channel.cpp:196-202,260-261`).
- **B-5** Второй central отклоняется при активном (`:468-473`).
- **B-6** Idle >30 s → disconnect (`ble_loop_tick:767-771`).
- **B-7** H1-проверка: FDI/TIME_SYNC/REVOKE_KEY по BLE → теперь ACCEPTED (нет `RES_NOT_AUTHORIZED`), байты совпадают с NFC.

**GROUP 3 — HANDOVER (compile-only — полный первый рантайм-pass).**
- **H-1** NFC tap → ACCESS → INNER_HANDOVER_ISSUE(0x17, phone_pubkey) → 167 B token (marker 0x99); проверить reader-sig над `DOMAIN_BLE‖bytes[0:103]` (`handover.cpp:34-72`).
- **H-2** `connectGatt` к `reader_ble_addr` из token → INNER_HANDOVER_PRESENT(0x18) → reader верифицирует binding (tap_nonce+phone_pubkey+expiry) → RES_OK, `handover_authorized=true` (`:75-125`).
- **H-3** При `handover_required=1`, BLE FILTER_UPDATE до PRESENT → RES_NOT_AUTHORIZED (`op_dispatch.cpp:57-63`).
- **H-4** Expired token (>60 s) → RES_EXPIRED. **H-5** Present token по NFC → reject (`op_dispatch.cpp:84-89`).

**GROUP 4 — ROBUSTNESS / RF (целит известный 0x0B/SESSION_LOST).**
- **R-1** 50× быстрых tap → счёт PUSH_INFO-retries (`transfer.cpp:92-101`) и 0x0B; retry восстанавливает, без hang.
- **R-2** Induce SESSION_LOST (kill/relaunch HCE mid-session) → reader re-push INFO, continue (`:433-439`).
- **R-3** Замороженный телефон → abort на 8 s deadline (`:391-395`), BLE-tick возобновляется.
- **R-4** 3 подряд PUSH_INFO-fail → PN532 reinit (`:360-363`).
- **R-5** Withdraw mid-READ_CHUNK → per-chunk retry ×3 затем clean abort+field-cycle (`:594-609`).
- **R-6** Добавить `esp_task_wdt` вокруг tap-loop (FW-ARC-04) и проверить TASK_WDT reset-reason recovery.

**GROUP 5 — LARGE FILTER / FLASH STREAMING (host-proven; реальный SPIFFS/NimBLE — первый pass).**
- **F-1** NFC FILTER_UPDATE >16 KB → стрим в inactive-slot, two-pass verify, swap, delivery_receipt (`transfer.cpp:481-555`).
- **F-2** BLE FILTER_UPDATE ~100 KB → SPSC-ring дренится 8 PDU/tick в slot, verify+swap (`ble_channel.cpp:385-459`); heap bounded, без full-file RAM-копии.
- **F-3** Tamper 1 body-байт → RES_BAD_SIGNATURE, активный фильтр не тронут (`commit_filter_from_flash:315`).
- **F-4** Reboot после swap → boot-reverify passes (`authoritative.cpp:157`).
- **F-5** Fill SPIFFS → graceful reject, без swap. **F-6** `blacklist_delta` удаляет absorbed local-keys (`:319-324`).

**GROUP 6 — NEGATIVE / SECURITY.**
- **SEC-1** Expired key → RES_EXPIRED. **SEC-2** Revoked-in-filter не в whitelist → RES_REVOKED_FILTER (`access.cpp:77-87`).
- **SEC-3** Dead RTC (вынуть DS3231) → все ACCESS=EXPIRED fail-closed (`access.cpp:64-67`); SOFT time-sync rejected.
- **SEC-4** Replay used_nonce → RES_BAD_NONCE. **SEC-5** Wrong reader_id → RES_WRONG_READER. **SEC-6** Forged sig → RES_BAD_SIGNATURE.
- **SEC-7** TIME_SYNC SOFT за drift-окном → RES_TIME_REGRESSION (`time_sync.cpp:109`).
- **SEC-8** H1 relay-тест: relay BLE ACCESS с расстояния → УСПЕХ by design (документировать как принятый риск; единственный гейт — Android confirm-UX).
- **SEC-9** blacklist overflow → evict-expired-then-fail-closed (FW-ARC-06).

**GROUP 7 — POWER / AUTONOMY (НЕ реализовано — сначала дизайн, потом тест).**
- **P-DESIGN** Реализовать: (a) duty-cycle PN532-detect (light-sleep между polls / PN532 low-power wake-on-field) вместо 10 мс busy-poll (`main.cpp:110`); (b) гейтить BLE-adv/интервал на `device_mode=battery` (расширить `ble_enabled` до power-профиля); (c) `esp_light_sleep_start` в idle с timer/GPIO(NFC-IRQ)-wake; (d) brownout-safe RTC-backed wake.
- **P-1** Измерить baseline-ток (idle, polling, BLE-adv, tap) на always-on build (квантифицировать gap).
- **P-2** После duty-cycle — измерить idle-ток и wake-latency на tap → tap успевает в deadline.
- **P-3** Battery discharge curve / projected autonomy. **P-4** DS3231 держит время через deep-sleep/brownout, ACCESS fail-closes на его потере.

**GROUP 8 — PORTABILITY (НЕ абстрагировано — сначала HAL-дизайн).**
- **HAL-DESIGN** Извлечь `INfcFrontend` (init/detect/exchange/field_on/field_off/reinit) и `IRtc` (почти готов: 3 fn) в `hw/` с per-vendor `.cpp` по build-flag; перенести PN532-конст (PUSH_CHUNK 146 B `transfer.cpp:120`, MAX_APDU_DATA_SIZE 240, rf-timings) за интерфейс. Tier-B GPIO уже каталогизированы (`docs/11:112`, `board_profiles/*.h`).
- **HAL-1** CLRC663-backend, прогнать GROUP 1 без изменений → идентичные wire-байты. **HAL-2** Своп RTC-vendor, прогнать SEC-3/N-4 → идентичное поведение.

**UI-track.**
- **UI-1/UI-2** Воспроизвести и пофиксить два известных UI-бага (раздел 5); верифицировать против backend enroll + SET-* round-trip.

---

## 7. Открытые вопросы и риски

1. **Compile-only-волна не верифицирована на железе.** Handover, BLE op_seq, X2-routing, large-filter flash-streaming, H1-both-transports — меняют wire-поведение, и их seam'ы (two-radio rendezvous, реальный SPIFFS/NimBLE-flash-I/O, op↔result-корреляция по радио) — именно то, что host-тесты не покрывают. **Прогонять GROUP 2,3,5 вместе на одной паре устройств** прежде, чем доверять любому из них.

2. **Power/autonomy — крупнейший gap, 0% старта.** В коде буквально нет sleep/duty-cycling. До заявления о батарейном варианте: реализовать power-профиль (расширить `ble_enabled` до `device_mode`), duty-cycle PN532-poll, добавить `esp_light_sleep`; **затем измерить**. `esp32dev` (non-BLE) сегодня лишь выключает BLE, не экономит энергию.

3. **Все токи в разделе 3 — datasheet-оценки, НЕ измерения** (мультиметра/PPK не было). Заменить per-rail-измерениями (PPK2/Joulescope/Otii) до любого тезисного числа.

4. **`esp_task_wdt`-backstop (FW-ARC-04) отсутствует.** 8 s session-deadline гейтит tap-loop, но зависший `apdu_exchange` или NimBLE host-task не имеют watchdog. Дёшево и прямо де-рискует класс 0x0B/SESSION_LOST.

5. **CLRC663 T=CL — гейтящий риск свопа.** Решить рано: vendor'ить NXP NFC Reader Library T=CL vs hand-roll RATS/PPS/I-block chaining. 240-байт APDU и 146-байт PUSH_CHUNK — PN532-FSC-артефакты, не протокол-лимиты, и ветка compile-only → остаются неверифицированными.

6. **`passage_direction` шипится hardcoded entry** (config-дефолт) и функционально неверен для exit-ридеров; он уже provisionable cfg-поле — задать per-reader в desktop-template и добавить exit-reader-тест в GROUP 1.

7. **H1-ревизия осознанно ПРИНИМАЕТ BLE relay/wormhole-риск для всех ops** (прежняя NFC-only proximity-граница снята); единственная митигация — Android confirm-UX, не reader-граница. Документировать как сознательный trade-off; RTT/RSSI presence-gate отложен (нужно железо).

8. **Backend DB-mirror конфигурации отстаёт** (Phase-3 follow-up: enroll не зеркалит полный config в БД; serial SET-* авторитативен в NVS). Закрыть, чтобы у аудита/ре-провижининга была server-side-копия. При добавлении новых per-reader-параметров (`cd_grant_margin`, `loop_delay_ms`) держать `ConfigParamCatalog` байт-выровненным с CFG[] (сегодня точное ×27-зеркало).

9. **Тройное определение счётчика слотов (UI-1)** — KeysScreen (local `activeCount`), Permits/Detail (server `activeKeysCount`), `PermitDao.recomputeActiveCounts` (третье) — стандартизировать на backend `is_active`-семантике (active OR revoked_by_server, исключая reader/bloom/expired) во избежание рецидива.

10. **Два inset-owning Scaffold (UI-2)** — стандартизировать ОДИН и задокументировать конвенцию, иначе класс бага вернётся при добавлении новых экранов.

---

*Этот документ — кураторская синтез-выжимка шести областных исследований с приоритизацией и планом. Все `file:line` приведены для прямой навигации; честно разделены verified (hw-proven), host-proven (compile-only) и estimated (datasheet) утверждения.*