# SCUD — Энергосберегающая архитектура ридера: HAL-seam, CLRC663-драйвер, sleep-машина и power-профили

> Дата: 2026-06-01 · Ветка: `feature/transport-compile-only`
> Тип документа: **DESIGN-ONLY** (финальная архитектура, не реализация). Ни строки кода не коммитится этим документом; здесь зафиксированы интерфейсы, состояния, регистровые карты и порядок работ.
> Опирается напрямую на `docs/10_esp_readiness_power_portability.md` (§3 энергопотребление, §4 портируемость — его матрица и HAL-набросок здесь доводятся до финальной формы) и на текущие точки сопряжения прошивки: `main.cpp` `loop()`, `transport/apdu.cpp`, `hw/rtc.cpp`, `ble/ble_channel.cpp`, `config.h`.
> ID-области (`HAL-`, `NFC-`, `SLP-`, `PWR-`, `RM-`) сквозные — ищутся по документу.

---

## 0. Scope и вердикт

Этот документ закрывает **единственный явно-незавершённый пункт 1.0** — батарейный / автономный режим ридера — как **готовую к реализации архитектуру**. Всё, что вне BATTERY/POWER-AUTONOMY, по policy 1.0 считается завершённым.

Граница: проектируем (a) **HAL-seam** — финальные интерфейсы `INfcFrontend` / `IRtc` и отображение сегодняшних `apdu_*`/`rtc_*` за них с минимальным churn, **резервируя `powerDown`/LPCD сейчас** (на PN532 — no-op), чтобы избежать второго API-перелома; (b) **план драйвера CLRC663** — SPI, make-or-break ISO-DEP/T=CL framing, карта field-control-регистров, истинный LPCD (wake-on-card с полем OFF) — это и есть ключ к батарее; (c) **sleep-машину** — light/deep-sleep с источниками пробуждения, разрешение конфликта «BLE-always-on vs MCU-deep-sleep», PN532-powerdown-duty-cycling как interim-вариант без нового железа, и два формальных power-профиля (`mains_ble` vs `battery_nfc`) под build/config-флагом; (d) **бюджет мощности** — datasheet-ОЦЕНКИ (явно помечены как не-измерения) до/после + методика измерения; (e) **упорядоченный roadmap** 0% → working.

**Что уже сделано к дате документа** (фундамент, на котором строимся):
- NFC- и RTC-seam — уже **чистые свободные функции** без утечек типов вендора (`docs/10` §4.1): `rtc_init/now/set`, `apdu_init/detect_target/exchange/field_off/reset_field/reinit`.
- **BLE intermittent advertising (Ось a, `docs/10` §3.3)** — уже реализовано: `ble_init()` ставит `setMinInterval(BLE_ADV_MIN_INTERVAL_UNITS=1600)` / `setMaxInterval(2400)` перед `startAdvertising()` (`ble_channel.cpp:740-741`, `config.h:186-187`). Интервал — свойство `g_adv`, переживает stop/start, наследуется путём `onDisconnect`-реадвертайза. Это снимает первый пункт оси BLE; ниже только доводим до per-reader-провижининга.
- **`esp_task_wdt`-backstop** — уже на месте (`main.cpp:113-123`), 15 s; важно для sleep-дизайна (sleep не должен голодать WDT — см. §3.6).
- **Sleep/duty-cycling — 0% (greenfield).** Grep `esp_sleep|light_sleep|deep_sleep|esp_pm|gpio_wakeup` по `src/` → 0 совпадений. `loop()` крутится на 240 MHz с `delay(MAIN_LOOP_DELAY_MS=10)`, поле PN532 энергизовано ~100% idle (`main.cpp:131`, `apdu.cpp:41`).

Все токи здесь — **datasheet/vendor-оценки, НЕ измерения** (см. §4, §5). Каждое тезисное число требует PPK2/Joulescope/Otii-замера per-rail прежде, чем войти в защиту ВКР.

---

## 1. HAL-seam: финальные интерфейсы `INfcFrontend` и `IRtc`

### 1.1 Цель и стиль seam

Сегодняшние `apdu_*`/`rtc_*` — уже корректная граница (`docs/10` §4.1: 0 утечек DS3231/PN532 за пределы `rtc.cpp`/`apdu.cpp`). Менять стиль вызова на сайтах НЕ нужно — все 17 time-call-site и единственный NFC-poll-сайт (`main.cpp:131`) остаются нетронутыми. Архитектурное решение (повтор `docs/10` §4.3 Шаг 0, фиксируем выбор):

- **Принимаем вариант (B) — C++-интерфейс + тонкие свободные-функции-форвардеры.** Свободные функции `apdu_*`/`rtc_*` сохраняются как public-surface (нулевой churn на сайтах), но их тела форвардят к синглтону `g_nfc` / `g_rtc` типа `INfcFrontend*` / `IRtc*`, выбранному по build-flag. Это даёт «учебниковую» HAL-диаграмму для ВКР **и** оставляет caller-код байт-в-байт прежним. Вариант (A) (просто второй `.cpp` по флагу) эквивалентен по результату, но интерфейс даёт явный контракт для CLRC663-автора и для будущих power-методов — поэтому (B).

**Размещение:** `ESP32/firmware/src/hw/nfc/` (интерфейс + per-vendor impl) и `ESP32/firmware/src/hw/rtc/`. Выбор backend — PlatformIO build-flag, зеркаля существующий `SCUD_BLE_ENABLED`-сплит (`-D SCUD_NFC_BACKEND=PN532|CLRC663`, `-D SCUD_RTC_BACKEND=DS3231|...`).

### 1.2 `INfcFrontend` — финальная форма

```cpp
// hw/nfc/nfc_frontend.h  (DESIGN — финальный контракт)
class INfcFrontend {
public:
    virtual ~INfcFrontend() = default;

    // --- сегодняшняя поверхность (apdu_* 1:1) ---
    virtual bool   init() = 0;                 // ← apdu_init()
    virtual bool   detectTargetAndSelect(uint32_t timeout_ms) = 0;  // ← apdu_detect_target()
    virtual size_t exchange(const uint8_t* req, size_t req_len,
                            uint8_t* resp, size_t resp_max) = 0;     // ← apdu_exchange()
    virtual void   fieldOff() = 0;             // ← apdu_field_off()
    virtual void   fieldReset() = 0;           // ← apdu_reset_field()
    virtual bool   reinit() = 0;               // ← apdu_reinit_pn532() (переименован)

    // --- РЕЗЕРВ ПОД БАТАРЕЮ: добавляем СЕЙЧАС, PN532 = no-op/false ---
    // Перевести frontend в самый низкий статический режим (поле OFF). На PN532 —
    // soft power-down (InPowerDown) ИЛИ просто fieldOff(); на CLRC663 — командный
    // standby. Возвращает true, если успешно вошли в режим.
    virtual bool   powerDown() = 0;

    // Вооружить аппаратный wake-on-card (LPCD: поле OFF, периодический
    // capacitance/threshold-зонд, IRQ на изменение). Возвращает true ТОЛЬКО если
    // frontend реально умеет LPCD. PN532 → return false (нет истинного LPCD).
    // Caller, получив false, остаётся на duty-cycled polling-модели (§3.4).
    virtual bool   wakeOnCard() = 0;

    // GPIO-номер, на который frontend дёргает IRQ при LPCD-детекте (для
    // esp_sleep_enable_ext0/gpio_wakeup). -1, если IRQ-линия не заведена/не
    // поддерживается. PN532 → -1.
    virtual int    irqGpio() const = 0;
};
```

**Контрактные инварианты (обязательны для любого backend):**
- `exchange()` возвращает длину ISO-DEP-ответа (включая SW1SW2), 0 при разрыве/ошибке. `resp_max ≥ 260` (как сегодня, `apdu.h:11`).
- `fieldReset()` держит **паузу ≥ FIELD_RESET_PAUSE_MS (80 ms, ≥50 ms минимум)** между off и on — иначе телефонный HCE не регистрирует деактивацию (`apdu.cpp:66-69`). Контракт переносится в CLRC663 буквально (см. §2.4).
- `detectTargetAndSelect()` после детекта цели шлёт SELECT-AID и возвращает true ТОЛЬКО на `90 00` (как `apdu.cpp:53-55`). Для PN532 anticoll/RATS спрятаны в `readPassiveTargetID`/`inDataExchange`; для CLRC663 их делает сам backend (см. §2.3).

### 1.3 `IRtc` — финальная форма

```cpp
// hw/rtc/rtc_iface.h  (DESIGN)
class IRtc {
public:
    virtual ~IRtc() = default;
    virtual bool     init() = 0;        // ← rtc_init(); ВЛАДЕЕТ своей шиной (см. §1.4)
    virtual uint64_t now() = 0;         // ← rtc_now(): unix-сек, 0 == неизвестно (RTC_DEAD_SENTINEL)
    virtual bool     set(uint64_t epoch) = 0;  // ← rtc_set()

    // --- РЕЗЕРВ ПОД БАТАРЕЮ ---
    // Запрограммировать аппаратный alarm на epoch_utc и завести его на SQW/INT-пин,
    // чтобы он работал источником пробуждения MCU из deep-sleep. Возвращает GPIO
    // alarm-линии (для esp_sleep_enable_ext0) или -1, если RTC/разводка не умеет.
    // DS3231 УМЕЕТ (Alarm1/Alarm2 → INT/SQW, GPIO сейчас НЕ заведён — см. §3.3).
    virtual int alarmAt(uint64_t epoch_utc) = 0;
    virtual void alarmClear() = 0;
};
```

**Sentinel-контракт:** `now()==0` означает «время неизвестно/RTC мёртв» (`config.h:129 RTC_DEAD_SENTINEL`, потребляется `access.cpp:63-64` → fail-closed EXPIRED). Любой backend ОБЯЗАН вернуть 0, когда не уверен во времени. Это критично для battery-режима: после глубокого разряда/brownout RTC может потерять время — ридер обязан fail-closed, а не выдать просроченный grant.

### 1.4 Отображение сегодняшнего кода за seam (минимальный churn)

| Сегодня | За seam | Изменение |
|---|---|---|
| `apdu_init()` (`apdu.cpp:13`) | `Pn532Frontend::init()` | тело без изменений, метод класса |
| `apdu_detect_target()` (`apdu.cpp:37`) | `Pn532Frontend::detectTargetAndSelect()` | тело без изменений |
| `apdu_exchange()` (`apdu.cpp:58`) | `Pn532Frontend::exchange()` | тело без изменений |
| `apdu_field_off()` / `apdu_reset_field()` | `fieldOff()` / `fieldReset()` | тело без изменений |
| `apdu_reinit_pn532()` (`apdu.cpp:77`) | `reinit()` | **переименовать** public-символ (chip-имя уходит из surface); единств. caller `transfer.cpp:362` |
| — (нет) | `Pn532Frontend::powerDown()` | **новый no-op-ish:** `nfc.setRFField(0,0)` + (опц.) InPowerDown; return true |
| — (нет) | `Pn532Frontend::wakeOnCard()` | **новый:** `return false` (PN532 без истинного LPCD) |
| — (нет) | `Pn532Frontend::irqGpio()` | `return -1` |
| свободные `apdu_*` | тонкие форвардеры к `g_nfc` | `g_nfc->detectTargetAndSelect(t)` и т.д. |
| `rtc_init/now/set` (`rtc.cpp`) | `Ds3231Rtc::{init,now,set}` + форвардеры | тело без изм.; `Wire.begin` переезжает внутрь `init()` (см. ниже) |
| `Wire.begin(...)` в `main.cpp:54` | `Ds3231Rtc::init()` владеет шиной | убрать coupling-коммент «DS3231 only»; guard от double-begin |
| `alarmAt/alarmClear` | `Ds3231Rtc` через RTClib Alarm1 | **новый;** требует завести INT/SQW на GPIO (board-profile, §3.3) |

**Churn-оценка:** RTC-seam — **Low** (1–2 файла). NFC-seam (переименование reinit + интерфейс + 3 новых reserved-метода no-op) — **Low-Med, без изменения поведения** (verified-PN532-путь байт-идентичен). Это и есть смысл «зарезервировать power/LPCD сейчас»: когда придёт CLRC663, surface уже финальная — второго API-перелома и второго прохода по сайтам не будет.

### 1.5 Tier-B (пины/шина) под board-profile

Свопу frontend мешает не seam, а сырые `#define` пинов в `config.h:8-30` (читаются на сайтах напрямую: `apdu.cpp:14`, `main.cpp:54`). Финальное решение (из `docs/10` §2.3, фиксируем): `ESP32/firmware/src/board_profiles/{esp32_pn532.h, esp32_clrc663.h}`, выбор `-D BOARD_PROFILE_*`. PN532-профиль несёт HSU-пины + `PN532_RF_TIMING_*` + `MAX_APDU_DATA_SIZE=240`; CLRC663-профиль несёт SPI-пины + IRQ-пин + свои FSD/FSC. Это предусловие к §2; без него CLRC663-impl не скомпилировать рядом с PN532.

---

## 2. План драйвера CLRC663 (истинный LPCD — ключ к батарее)

CLRC663 — **frontend-only** IC (как и PN5180/ST25R3916): он НЕ имеет встроенного протокольного стека, который PN532 прячет внутри `InDataExchange`. Это даёт истинный LPCD (поле OFF, ~3–6 мкА), но цена — **T=CL framing переезжает на host**. Это make-or-break пункт всего батарейного направления.

### 2.1 Шина: SPI

- CLRC663 — SPI (до 10 MHz), в отличие от PN532-HSU. Освобождает UART2 (GPIO16/17), занимает SPI (SCK/MOSI/MISO/CS) + 1 GPIO под IRQ (LPCD/RX-done).
- Backend `Clrc663Frontend::init()`: `SPI.begin(...)` из CLRC663-board-profile, soft-reset (команда `Cmd_SoftReset`), загрузка ISO14443A-протокола в регистры, конфигурация TxControl/драйверов, RxThreshold. Заменяет PN532-HSU-construction (`apdu.cpp:9-11`) — целиком внутри backend, наружу не течёт.
- **Не делить SPI-шину с критичными по таймингу периферийными.** Если SPIFFS-фильтр-партиция на той же SPI-флешке (внутренняя ESP32-flash — отдельная шина, ОК), конфликта нет; внешняя SPI-периферия потребует CS-арбитража.

### 2.2 T=CL / ISO-DEP framing — make-or-break

Два пути, выбрать рано (это пункт 5 «Открытых рисков» `docs/10` §7):

**Путь (a) — vendor NXP NFC Reader Library (рекомендуется для корректности).**
Портировать/vendor'ить слои `phpalI14443p3a` (REQA/anticoll/SAK) + `phpalI14443p4` (RATS/PPS) + `phpalI14443p4a` и T=CL-обмен (`phpalI14443p4_Exchange`) из NXP NFC Reader Library. `exchange()` → один `phpalI14443p4_Exchange(I-block, chaining)`. Плюс: проверенный стек, корректный I-block chaining/CRC/WTX/таймауты. Минус: библиотека крупная, HAL NXP под их BAL — нужен shim под ESP32-SPI; лицензия/объём flash.

**Путь (b) — hand-roll поверх register-level TX/RX (минимальный объём, выше риск).**
Руками: REQA(0x26) → anticollision-cascade (CL1/CL2, BCC) → SELECT → RATS(0xE0, FSDI) → (опц.) PPS → затем цикл T=CL I-block round-trip с chaining (PCB block-number toggle, CRC_A, FWT/WTX-таймауты). `detectTargetAndSelect()` = REQA/anticoll/RATS/SELECT-AID; `exchange()` = один I-block round-trip с chaining до полного APDU-ответа. Плюс: только нужное, нет внешней зависимости. Минус: chaining/WTX/таймаут-краевые случаи — именно то, на чём ломаются hand-roll-стеки; верифицируемо ТОЛЬКО на железе (ветка compile-only).

**Отображение на seam:**
| `INfcFrontend`-метод | PN532 (есть) | CLRC663 (план) |
|---|---|---|
| `detectTargetAndSelect()` | `readPassiveTargetID` + `inDataExchange(SELECT-AID)` | REQA → anticoll → RATS → (PPS) → SELECT-AID I-block; true на `90 00` |
| `exchange()` | `inDataExchange` (0x40, стек в PN532-fw) | host-side T=CL I-block(s) с chaining + CRC + FWT |
| `reinit()` | sleep-wakeup + SAMConfig | `Cmd_SoftReset` + перезагрузка протокол-регистров |

### 2.3 Карта field-control-регистров

| Действие | PN532 (есть) | CLRC663 (план) |
|---|---|---|
| `fieldOff()` | `setRFField(0x00,0x00)` (`apdu.cpp:74`) | `TxControl` (0x29): сбросить `Tx1RFEn`/`Tx2RFEn` биты драйверов → поле OFF |
| field ON | авто на след. detect (PN532 InListPassiveTarget) | `TxControl`: выставить `Tx1RFEn`/`Tx2RFEn` |
| `fieldReset()` | off → `delay(field_pause≥50ms)` → on (`apdu.cpp:67-69`) | TxControl OFF → `delay(field_pause)` → ON; **держать контракт ≥50 ms** (§1.2) |
| RF-tuning | `setRfTimings`/`setPassiveActivationRetries` (`apdu.cpp:28-32`) | нет RFConfiguration-команды → map в `Timer`/`RxThreshold`/`DrvMode`-регистры ИЛИ deprecate ключи `rf_atr/rf_retry/pn532_retries` для CLRC663-build (board-profile) |

> **Регистровые адреса даны как ориентир по семейству CLRC663 (0x29 TxControl и т.п.); сверить с конкретным datasheet ревизии (CLRC663 plus) при реализации — здесь это DESIGN, не verified-маппинг.**

### 2.4 Истинный LPCD (wake-on-card, поле OFF) — то, ради чего всё

Это единственный путь к field-off idle (PN532 не умеет — он энергизует поле для каждого детекта, `docs/10` §3.3 Option B1).

- **Принцип LPCD:** CLRC663 периодически (внутренний LP-таймер, период ~десятки–сотни мс) на короткое время поднимает поле, мерит I/Q-отклик антенны (наличие карты сдвигает резонанс/амплитуду), сравнивает с откалиброванным порогом, и при превышении — дёргает IRQ. Средний ток ~3–6 мкА (datasheet-ОЦЕНКА), потому что поле включено доли процента времени и MCU спит.
- **`wakeOnCard()`-impl (CLRC663):** калибровка порога (`LPCD_QMin/QMax/IMin/IMax`-регистры) на пустой антенне при provision/boot → запись порога → `Cmd_LPCD` (low-power card detection command) → `Standby`. Выставляет `irqGpio()` на сконфигурированный IRQ-пин.
- **Связь с MCU-sleep (§3):** `irqGpio()` заводится как `esp_sleep_enable_ext0_wakeup(irq, level)` (deep-sleep) или `gpio_wakeup`+`esp_sleep_enable_gpio_wakeup` (light-sleep). MCU спит → CLRC663 сам зондирует поле → карта → IRQ → MCU wake → `detectTargetAndSelect()` → tap. Поле остальное время OFF.
- **Калибровка — практический риск:** LPCD-порог чувствителен к антенне/корпусу/окружению. Нужна (1) калибровка на смонтированном ридере, (2) сохранение порога в NVS (новый Tier-A-параметр `lpcd_threshold`, board/install-specific), (3) периодическая ре-калибровка при дрейфе температуры. Закладываем как provisioned-параметр.
- **Альтернативы по току (ориентир, datasheet):** CLRC663 plus LPCD ~3–6 мкА; PN5180 ~15–35 мкА; ST25R3916 — свой capacitive/inductive wake. Выбор за availability/цена/антенна; seam одинаков.

### 2.5 APDU-лимиты — перехарактеризовать на CLRC663

Сегодняшние 240-байт APDU-ceiling (`config.h:58 MAX_APDU_DATA_SIZE`) и 146-байт PUSH_CHUNK (`transfer.cpp:120`) — **PN532-FSC-артефакты** (FSC=256 + `uint8_t responseLength` в PN532-HAL), а НЕ протокол-лимиты (`docs/10` §2.2, §4.2, §7 п.5). На CLRC663 FSD/FSC определяются нашим RATS/ATS-обменом — **перемерить и перенастроить эти константы в CLRC663-board-profile.** Это hardware-only-verifiable → остаётся на ветке compile-only до железа. Crypto/op-слои frontend-agnostic (сидят на `exchange()`) — изменений не требуют.

### 2.6 Гейтинг

CLRC663-build — за **отдельным platformio-env** (`-D SCUD_NFC_BACKEND=CLRC663 -D BOARD_PROFILE_ESP32_CLRC663`), чтобы verified PN532-путь оставался default (`docs/10` §4.3). Прогон GROUP 1 (`docs/10` §6) на CLRC663 → идентичные wire-байты = критерий приёмки (HAL-1).

---

## 3. Sleep-архитектура

### 3.1 Конфликт «BLE-always-on vs MCU-deep-sleep» и его разрешение

Это центральная развилка (`docs/10` §3.1): **deep-sleep сносит NimBLE-стек и RAM (`g_inbound`) и ресетит чип** → несовместим с персистентным BLE-коннектом и непрерывным adv. То есть «always-on BLE» и «MCU-deep-sleep» взаимоисключающи. `platformio.ini:36-37` уже кодирует этот сплит в комментарии («BLE radio убивает автономность»).

**Разрешение = power-profile-split (не один универсальный режим):**
- **Профиль `mains_ble`** — питание от сети, BLE нужен (шлагбаум/турникет/courier-BLE). MCU **не уходит в deep-sleep**; допустим только **light-sleep** (стек и RAM сохраняются) + duty-cycle радио. BLE остаётся доступен.
- **Профиль `battery_nfc`** — батарея, BLE выключен (`ble_enabled=false`). MCU уходит в **deep-sleep** между tap'ами, просыпается по NFC-IRQ (LPCD) / RTC-alarm / fallback-timer. Это даёт ~10–100 мкА floor.

Профиль выбирается **build/config-флагом** (расширение существующего `SCUD_BLE_ENABLED` до `device_mode`; см. §3.5). Один прошитый ридер — один профиль; смешения нет by design.

### 3.2 State-machine (light + deep sleep)

```
                 ┌──────────────────────────────────────────────┐
                 │                  BOOT / setup()               │
                 │  init HAL, load cfg, выбор power-профиля       │
                 └───────────────┬──────────────────────────────┘
                                 │
                 ┌───────────────▼──────────────┐
            ┌───►│            IDLE               │
            │    │  поле OFF (LPCD) или duty-poll │
            │    └──┬───────────┬───────────┬────┘
            │       │ NFC-IRQ   │ BLE-conn  │ idle-таймер истёк
            │       │ /LPCD-hit │ (только   │ & нет работы
            │       │           │ mains_ble)│
            │       ▼           ▼           ▼
            │  ┌─────────┐ ┌─────────┐ ┌──────────────────┐
            │  │ TAP/    │ │ BLE_OP  │ │ SLEEP_ARM        │
            │  │ SESSION │ │ SERVICE │ │ выбрать глубину:  │
            │  │ (run_   │ │ (on_op_ │ │  mains_ble→LIGHT  │
            │  │  tap_   │ │  comp.) │ │  battery_nfc→DEEP │
            │  │ session)│ │         │ │ вооружить wake-src │
            │  └────┬────┘ └────┬────┘ └────────┬─────────┘
            │       │ END/      │ done           │
            │       │ cooldown  │                ▼
            │       ▼           │      ┌───────────────────┐
            │  ┌─────────┐      │      │  LIGHT_SLEEP       │  (mains_ble)
            └──┤ COOLDOWN│◄─────┘      │  esp_light_sleep_  │
               │ field   │            │  start(); wake:    │
               │ OFF     │            │  timer | NFC-IRQ   │──┐ wake → IDLE
               └────┬────┘            └───────────────────┘  │
                    └──────────────────────────►─────────────┘
                                                  ┌───────────────────┐
                                                  │  DEEP_SLEEP        │  (battery_nfc)
                                                  │  esp_deep_sleep_   │
                                                  │  start(); wake:    │
                                                  │  ext0 NFC-IRQ |    │
                                                  │  RTC-alarm | timer │
                                                  │  → reset → BOOT    │
                                                  └───────────────────┘
```

**Состояния:**
- **IDLE** — ничего не происходит. В `battery_nfc`: поле OFF, LPCD вооружён (CLRC663) или PN532 powerdown'нут между poll'ами (interim). В `mains_ble`: BLE-adv (intermittent, уже есть), готовность принять connect/tap.
- **TAP/SESSION** — текущий `run_tap_session()` (`main.cpp:132`); без изменений.
- **BLE_OP_SERVICE** — `ble_loop_tick()` / `on_op_complete()`; только в `mains_ble`.
- **COOLDOWN** — текущее `transfer_in_cooldown()`-окно (`transfer.cpp:294`), поле OFF (`transfer.cpp:708`); по выходу → IDLE.
- **SLEEP_ARM → LIGHT/DEEP_SLEEP** — новые. Глубина по профилю.

### 3.3 Источники пробуждения

| Wake-source | Профиль | Механизм | Готовность сегодня |
|---|---|---|---|
| **NFC-IRQ (LPCD)** | оба | CLRC663 IRQ-GPIO → `INfcFrontend::irqGpio()`; light: `gpio_wakeup`; deep: `esp_sleep_enable_ext0_wakeup` | **нужен CLRC663** (PN532 `irqGpio()` = -1); на PN532 заменяется duty-poll-timer (§3.4) |
| **RTC-alarm** | battery_nfc | DS3231 Alarm1 → INT/SQW-пин → ext0-wake; `IRtc::alarmAt()` (§1.3) | **DS3231 умеет, GPIO сейчас НЕ заведён** (только I2C, `main.cpp:54`); нужен board-profile-пин + RTClib alarm |
| **Timer** | оба | `esp_sleep_enable_timer_wakeup(us)`; периодический tick для housekeeping (cleanup nonces, blacklist-expire) и как fallback-poll на PN532 | тривиально |
| **Серийная провизия / кнопка** | оба | boot-button (`PROVISIONING_BUTTON_PIN=0`) уже = forced provisioning (`main.cpp:80`); из deep-sleep — ext1/RTC-GPIO wake | кнопка есть; wake-маппинг новый |

**Важно про deep-sleep housekeeping:** `cleanup_stale_nonces()` / `expire_local_blacklist_entries()` (`main.cpp:136-137`) сейчас крутятся каждую итерацию. В `battery_nfc` они должны исполняться на каждом wake (NFC/RTC/timer), а timer-wake задаёт их максимальный период. Поскольку deep-sleep ресетит RAM, nonce-ring и blacklist-TTL должны переживать через NVS (blacklist уже в NVS, `local.cpp`; nonce-ring — RAM-only, TTL'ится по `millis()` — **в battery_nfc nonce-anti-replay-окно надо привязать к `rtc_now()`, а не `millis()`**, иначе после deep-sleep-reset replay-защита обнуляется — отметить как security-следствие batter-режима).

### 3.4 PN532-powerdown duty-cycling — interim без нового железа

Пока нет CLRC663, единственный no-new-hardware-вариант (Option B1, `docs/10` §3.3):
- Между poll'ами слать PN532 soft power-down (`InPowerDown`, ~22 мкА datasheet-ОЦЕНКА) и будить перед poll'ом; ИЛИ держать MCU в **light-sleep с timer-wake** на `loop_delay`-период, а PN532 — fieldOff между poll'ами.
- **Ключевое ограничение:** PN532 ВСЁ РАВНО энергизует поле на каждый реальный detect (нет LPCD) → экономится только inter-poll idle, не сам poll. Это **частичная** экономия (`docs/10` §3.2 Профиль B: «InAutoPoll/powerdown режет idle, но ВСЁ РАВНО энергизует поле»).
- Реализуется через зарезервированный `powerDown()` (§1.2): на PN532 = `fieldOff()` + опц. InPowerDown; перед detect — wake. Сам `loop()` оборачивается light-sleep'ом (§3.6). Никакого нового API — seam уже готов.
- Цена: tap-latency растёт на период duty-cycle (poll реже) — тюнится `nfc_detect`/`loop_delay` против энергии.

### 3.5 Два формальных power-профиля (build/config-флаг)

Расширяем `SCUD_BLE_ENABLED` (сегодня bool BLE-gate) до `device_mode`. Финальная форма:

```ini
; platformio.ini (DESIGN — новый env)
[env:esp32_battery_nfc]
extends = env:esp32dev               ; БЕЗ -DSCUD_BLE_ENABLED → BLE компилируется в no-op
build_flags =
    ${env:esp32dev.build_flags}
    -DSCUD_POWER_PROFILE=BATTERY_NFC ; гейтит deep-sleep + LPCD-wake
    -DBOARD_PROFILE_ESP32_CLRC663    ; (когда появится CLRC663; до того — PN532 duty-cycle)
```

| Аспект | `mains_ble` (= сегодняшний `esp32dev_ble`) | `battery_nfc` (новый) |
|---|---|---|
| BLE | on, intermittent adv (уже есть) | **off** (`ble_enabled=false`, NimBLE no-op) |
| MCU-sleep | **light** only (RAM/стек целы) | **deep** между tap'ами |
| Wake | timer + (опц.) NFC-IRQ | NFC-IRQ(LPCD) / RTC-alarm / timer |
| NFC idle | поле duty-cycled | поле OFF (LPCD, CLRC663) / powerdown (PN532 interim) |
| Frontend | PN532 или CLRC663 | CLRC663 (для истинного LPCD) или PN532 (частичный) |
| Tap-latency | низкая | выше (sleep-wake + LPCD-период) |
| Idle-ток (ОЦЕНКА) | единицы мА | ~10–100 мкА (CLRC663) |
| Courier-sync | BLE (filter/receipts) | **только NFC-tap-courier** (BLE недоступен) |

**Связь с per-reader cfg:** `device_mode` — НЕ runtime-provisionable (определяет sleep-стратегию и линковку BLE → compile-time, как `SCUD_BLE_ENABLED`). НО `loop_delay_ms`, `nfc_detect`, `lpcd_threshold` (новый), `ble_adv_*_units` — provisionable knobs внутри профиля (`docs/10` §2.3 п.6). Backend-policy при enroll должен валидировать, что `battery_nfc`-ридер не пытается провизить BLE-зависимые поля (shared-protocol-инвариант, `docs/10` §2.3).

### 3.6 Sleep vs Task-WDT и BLE-tick — корректность

- **WDT.** `esp_task_wdt_reset()` кормится в начале `loop()` (`main.cpp:129`), timeout 15 s (`config.h:103`). Light-sleep длиной < timeout безопасен (wake → след. итерация кормит WDT). Для длинного light-sleep — либо `esp_task_wdt_reset()` непосредственно перед/после sleep, либо отписать loop-task от WDT на время sleep и переподписать на wake. Deep-sleep ресетит чип → WDT не релевантен (новый boot).
- **BLE-tick.** В `mains_ble` light-sleep не должен голодать `ble_loop_tick()` (idle-watchdog `ble_idle_ms`, deferred-INFO). Решение: light-sleep только когда `g_conn_handle == BLE_CONN_HANDLE_NONE` (нет активного central) И не в cooldown; при активном коннекте — обычный busy-loop (центральный держит соединение, экономить нечего, питание сетевое). Это естественно стыкуется с автоматическим `esp_pm`-light-sleep (CPU спит в idle FreeRTOS-тика, просыпается на событие/тик) — **рекомендуемый механизм для `mains_ble`**, т.к. NimBLE-события сами будят.
- **Cooldown.** Sleep-arm только при `!transfer_in_cooldown()` (`transfer.cpp:294`) — иначе sleep пропустит истечение cooldown-окна. В cooldown поле уже OFF (`transfer.cpp:708`), так что light-sleep с timer-wake на остаток cooldown-окна допустим и полезен.

### 3.7 Точка интеграции в `loop()`

Сегодня (`main.cpp:128-140`):
```cpp
void loop() {
    esp_task_wdt_reset();
    if (!transfer_in_cooldown() && apdu_detect_target(g_state.cfg.nfc_detect)) run_tap_session();
    ble_loop_tick();
    cleanup_stale_nonces();
    expire_local_blacklist_entries();
    delay(MAIN_LOOP_DELAY_MS);
}
```

Финальная форма (DESIGN, профиль-гейтированная idle-ветка вместо `delay`):
```cpp
void loop() {
    esp_task_wdt_reset();
    bool did_tap = !transfer_in_cooldown() && apdu_detect_target(g_state.cfg.nfc_detect);
    if (did_tap) run_tap_session();
    ble_loop_tick();
    cleanup_stale_nonces();
    expire_local_blacklist_entries();

    // power_idle(): вместо busy delay(10) — sleep по профилю.
    //   battery_nfc: если idle и !cooldown и (LPCD вооружён ИЛИ pn532 powerdown)
    //                → esp_deep_sleep_start() с ext0(NFC-IRQ)/RTC-alarm/timer wake.
    //                (deep-sleep = reset → следующий boot повторит setup() и обработает tap)
    //   mains_ble:   если !cooldown и нет BLE-коннекта → короткий esp_light_sleep_start()
    //                с timer + (опц.) NFC-IRQ wake; иначе delay(loop_delay_ms).
    power_idle(did_tap);
}
```
`power_idle()` — единственная новая функция в hot-path; вся sleep-логика инкапсулирована в новом модуле `hw/power.{h,cpp}`, гейтированном `SCUD_POWER_PROFILE`. На `mains_ble`-без-sleep она вырождается в сегодняшний `delay(loop_delay_ms)` → нулевой регресс для verified-пути.

---

## 4. Бюджет мощности (datasheet-ОЦЕНКИ — НЕ измерено)

> **ВСЕ числа ниже — типовые datasheet/vendor-оценки. Это планировочные цифры: мультиметра/PPK не было.** Реальный ток зависит от напряжения шины, регуляторов платы (red-board PN532: AMS1117 + level-shifters добавляют quiescent), настройки антенны, нагрузки tag. Источники чисел — те же, что в `docs/10` §3.2, здесь сведены до/после по профилям. Перед любым тезисным утверждением — ИЗМЕРИТЬ (§5).

### 4.1 ДО (сегодня — always-on, как в `main.cpp`)

| Профиль сборки | Компоненты-доминанты | Est. средний ток | Автономность @5000 mAh (ОЦЕНКА) |
|---|---|---|---|
| `esp32dev_ble` (BLE) | ESP32 active+BLE (~95–130 мА) + PN532 поле ~100% idle (~100–150 мА) | **≈200–280 мА** | **~18–25 ч** |
| `esp32dev` (NFC-only) | ESP32 active (~40–70 мА) + PN532 поле (~100–150 мА); sleep нет | **≈140–220 мА** | **~23–36 ч** |

DS3231 ~0.1–0.2 мА (VCC-timekeeping) и замок (one-shot, avg≈0 если не maglock) — <1% бюджета, опущены.

### 4.2 ПОСЛЕ (по профилям этого документа)

| Профиль | Конфигурация | Est. idle-ток | Est. автономность @5000 mAh | Множитель vs «до» |
|---|---|---|---|---|
| `mains_ble` + light-sleep | ESP32 light-sleep между событиями (~0.8 мА) + intermittent BLE-adv (уже есть, ~1–5 мА avg) + PN532 duty-poll | **~3–10 мА idle** | сетевое (автономность не цель) | ~20–90× ниже idle |
| `battery_nfc` + PN532 duty-cycle (interim) | ESP32 deep/light-sleep + PN532 powerdown между poll'ами; **поле всё равно on на poll** | **~1–22 мА** (доминирует poll-burst-duty) | **~10–200 ч** (зависит от poll-частоты) | частично |
| `battery_nfc` + CLRC663 LPCD (цель) | ESP32 deep-sleep (~10–100 мкА) + CLRC663 LPCD (~3–6 мкА, поле OFF) | **~15–110 мкА idle** | **месяцы** (~2000–10000+ ч idle-floor) | **~2000–20000× ниже idle** |

> Активный tap (поле on + crypto + замок) у всех профилей сравним с «до» на длительность сессии (~единицы сек); экономия — в idle-доле, которая доминирует duty-цикл реального ридера. Реальная автономность = ∫(idle-floor + N_tap·E_tap + housekeeping)/сутки — считать после замера E_tap.

### 4.3 Что именно меняет порядок

1. Поле OFF в idle (CLRC663 LPCD) убирает ~100–150 мА PN532-доминанту → это **самый крупный single-выигрыш** и единственный путь к field-off idle (`docs/10` §3.3 Option B2).
2. MCU deep-sleep убирает 40–70 мА ESP32-active → до ~10–100 мкА.
3. BLE-off (battery-профиль) убирает ~60–130 мА радио.
Только все три вместе дают мкА-floor; любой по отдельности — частичный.

---

## 5. Как ИЗМЕРИТЬ (заменить оценки) — обязательно до защитных чисел

| Метод | Что захватывает | Применение |
|---|---|---|
| USB inline power-meter (USB-C тестер) | Whole-board 5V, грубо (~10 мА разрешение) | быстрый sanity-check «до», без transient |
| Bench DMM в разрыве 5V | Steady-state per-profile (мкА–A) | idle/field-on avg; burden-voltage теряет быстрые пики |
| **Nordic PPK2 / Joulescope / Otii Arc** | **Transient:** poll-burst, BLE-adv-пик, lock-inrush, **sleep-floor (мкА)**, LPCD-зонд-импульсы | **обязателен для duty-cycled/sleep-профилей;** интеграл → mAh/день |

**Процедура (минимум для ВКР-числа):**
1. Мерить **каждую шину отдельно**: ESP32 3V3, PN532/CLRC663 5V, замок 12V — у них разные доминанты.
2. Захватить, по каждому профилю: (a) idle-floor (sleep), (b) один poll/LPCD-зонд-импульс, (c) полный tap-цикл (детект→crypto→замок→cooldown), (d) BLE-adv-событие (mains_ble), (e) deep-sleep-wake-стоимость (boot-cost на каждый wake в battery_nfc — он НЕ бесплатен, ~десятки–сотни мс active).
3. Интегрировать в mAh/день при заданном профиле использования (N tap/сутки) → проектная автономность с реальной батареей.
4. **Заменить КАЖДОЕ число §4 измеренным** прежде, чем оно войдёт в текст защиты. Datasheet-числа здесь — планировочные (повтор `docs/10` §7 п.3).

---

## 6. Упорядоченный roadmap (effort vs saving) — 0% → working

Порядок: дешёвое и BLE-совместимое сначала, дорогой frontend-своп последним. Каждый шаг самостоятелен и измерим.

| # | Шаг | Effort | Saving | Зависит от | Профиль |
|---|---|---|---|---|---|
| **RM-0** | **Baseline-замер** (P-1, `docs/10` §6): idle/poll/BLE-adv/tap-ток на обоих сегодняшних build'ах. Квантифицировать gap прежде любой оптимизации. | XS | — (gating) | — | оба |
| **RM-1** | **HAL-seam финализация** (§1): `INfcFrontend`/`IRtc`-интерфейсы + форвардеры; переименовать `apdu_reinit_pn532→reinit`; **зарезервировать `powerDown`/`wakeOnCard`/`irqGpio`/`alarmAt` no-op на PN532/DS3231**; `Wire.begin`→`rtc init`. Поведение PN532-пути байт-идентично. | Low-Med | — (enabler, без второго churn) | — | оба |
| **RM-2** | **Board-profiles** (`docs/10` §2.3 Tier-B): `board_profiles/esp32_pn532.h`; вынести пины/RF-timing/FSC из `config.h`. Предусловие CLRC663. | Low | — (enabler) | RM-1 | оба |
| **RM-3** | **Per-reader power knobs**: `loop_delay_ms`, `lpcd_threshold` (new) в CFG[] + `ConfigParamCatalog`; `cd_grant_margin` (`docs/10` §2.3 п.5-6). BLE-adv-интервал → provisionable (`docs/10` §3.3). | Low | Low (тюнинг) | RM-1 | оба |
| **RM-4** | **ESP32 auto light-sleep** (C1, `docs/10` §3.3): `esp_pm_config`/`power_idle()` в idle-ветке `loop()` (§3.7); gate на `!cooldown && !ble_connected` (§3.6). BLE-совместимо. **Парится с уже-готовым intermittent-adv.** | Med | Med | RM-1 | mains_ble |
| **RM-5** | **PN532 powerdown duty-cycle** (B1, interim, §3.4): `powerDown()` реальный на PN532 (fieldOff+InPowerDown) + light-sleep между poll'ами. **Частичная экономия, БЕЗ нового железа** — первый батарейный результат. | Low-Med | Partial | RM-1, RM-4 | battery_nfc |
| **RM-6** | **Profile-split формализация** (§3.5): env `esp32_battery_nfc`, `SCUD_POWER_PROFILE`; `power_idle()` deep-sleep-ветка; RTC-alarm-wake (`alarmAt` + завести DS3231 INT/SQW на GPIO, board-profile, §3.3); nonce-anti-replay → `rtc_now()`-based (§3.3). | Med | Large (deep-sleep) | RM-2,3,5 | battery_nfc |
| **RM-7** | **CLRC663-драйвер** (§2): SPI-backend, **T=CL framing (путь a vendor / b hand-roll — решить в начале RM-7)**, field-register-map, **истинный LPCD `wakeOnCard()`** + IRQ-wake. Перехарактеризовать APDU-лимиты (§2.5). Отдельный env, GROUP 1 → идентичные байты (HAL-1). | **High** | **Largest** (field-off мкА-idle) | RM-1,2,6 | battery_nfc |
| **RM-8** | **Замеры после каждого RM-4..7** (§5, P-2/P-3): PPK2 per-rail, mAh/день, discharge-кривая, P-4 (DS3231 держит время через deep-sleep, ACCESS fail-close на его потере). **Заменить все §4-оценки.** | Med | — (validation) | RM-4..7 | оба |

**Критический путь к настоящей батарее:** RM-1 → RM-2 → RM-6 → **RM-7** (CLRC663 LPCD — это и есть «ключ к батарее», field-off idle). RM-4/RM-5 дают ранний частичный результат на текущем железе без ожидания CLRC663. RM-0 и RM-8 обрамляют всё измерением (без них любое число — оценка, не факт).

**Риск-фокус (повтор `docs/10` §7):** (1) T=CL-корректность на CLRC663 — make-or-break RM-7, verifiable только на железе (ветка compile-only); (2) LPCD-калибровка порога под антенну/корпус — практический, требует на-месте; (3) nonce-replay-окно через deep-sleep-reset — security-следствие, привязать к RTC; (4) все §4-числа — оценки до RM-8.

---

## 7. Связь с остальной документацией

- `docs/10 §3` — исходный power-анализ (где уходит ток, три оси оптимизации, бюджет); этот документ доводит его Ось a (готова), Ось b (RM-5/RM-7), Ось c (RM-4/RM-6) до финальной архитектуры.
- `docs/10 §4` — исходная HAL-матрица портируемости; §1–2 здесь — её финальная форма с зарезервированными power-методами.
- `docs/10 §2.3` — Tier-B board-profiles и power-knobs (`loop_delay_ms`); RM-2/RM-3.
- `docs/10 §6` GROUP 7 (POWER) / GROUP 8 (PORTABILITY) — приёмочные сценарии под этот дизайн (P-DESIGN/P-1..4, HAL-DESIGN/HAL-1..2).
- `docs/11` — provisioning/SET-* и `ConfigParamCatalog`-зеркало; новые knobs (RM-3) держать байт-выровненными с CFG[].
- **Shared-protocol-инвариант (MEMORY):** добавление per-reader power-полей и `device_mode`-валидация при enroll координируются с backend-policy; wire-протокол ACCESS/FILTER/и т.д. этим документом НЕ меняется (frontend-своп даёт идентичные wire-байты — критерий HAL-1).

---

*Документ — финальная DESIGN-спецификация энергосберегающей архитектуры (единственный незавершённый 1.0-пункт). Реализация — по roadmap §6. Все токи — datasheet-оценки до замера (§5); все регистровые карты CLRC663 — ориентир по семейству, сверять с datasheet ревизии при реализации.*
