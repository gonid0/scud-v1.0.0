# 02. Firmware — ТЗ для прошивки ESP32 (Arduino framework)

**Этот документ читается вместе с `00_shared_protocol.md`.** Там — форматы всех объектов, криптопримитивы, domain tags, алгоритмы verification.

## 0. Задача

Прошивка ридера СКУД на ESP32 в Arduino-фреймворке. Hardware: ESP32 (рекомендуется WROOM-32E 4/8 MB), PN532 (I2C), DS3231 (I2C), реле/GPIO для замка, двуцветный LED.

Ридер оффлайновый: всё взаимодействие с сервером идёт через Android-приложение, подносимое по NFC. Ридер хранит:
- Свою пару ключей Ed25519 и публичные ключи сервера (Ed25519 + X25519).
- Bloom-фильтр отозванных ключей + whitelist ложноположительных.
- Локальный blacklist ключей, отозванных через ридер.
- Время (DS3231 RTC).

## 1. Стек

| Компонент | Версия | Примечание |
|---|---|---|
| Framework | Arduino (PlatformIO) | platform = espressif32 |
| Board | esp32dev (или esp-wrover-kit для 8MB) | |
| Compiler | ESP32 Arduino core 2.0.14+ | |
| IDE | PlatformIO (рекомендуется) или Arduino IDE 2.x | |

### 1.1 Критические решения по сборке

1. **WiFi отключен** в рантайме (не использовать WiFi.h; bootloader не инициализирует radio).
2. **BLE в MVP не используется** (резерв на будущее).
3. **Криптография:** встроенный `mbedTLS` из ESP32 Arduino core. Нужные примитивы:
   - Ed25519 (mbedtls_ecdsa с curve25519 или через дополнительный модуль — см. §7.1).
   - ChaCha20-Poly1305: `mbedtls/chachapoly.h`.
   - BLAKE2b: **нет** в mbedTLS по умолчанию. Подключаем отдельный BLAKE2 ref implementation.
   - BLAKE2s: аналогично.
4. **PN532 — через UART (HSU, High Speed UART), не I2C.** Использовать `elechouse/PN532` (https://github.com/elechouse/PN532) — единственная нативно поддерживающая HSU-режим на ESP32. I2C-интерфейс у PN532 на ESP32 ненадёжен (таймауты, коллизии с DS3231), UART надёжнее и проще в отладке.

   На модуле PN532: перевести перемычки SEL0/SEL1 в положение HSU (`SEL0=0, SEL1=0`).

5. **DS3231 — через I2C**, отдельно от PN532. На своей I2C-шине (Wire).

**Альтернатива mbedTLS — Monocypher (single-file C99):** проще интегрировать, меньше кода. Скачать `monocypher.c` и `monocypher.h` в `lib/Monocypher/`. Поддерживает всё, что нужно: Ed25519, X25519, ChaCha20-Poly1305, BLAKE2b.

**Рекомендация:** Monocypher. Встроенный mbedTLS слишком большой и не умеет Ed25519 из коробки в Arduino-сборках.

## 2. Структура проекта

PlatformIO-based:

```
firmware/
├── platformio.ini
├── src/
│   ├── main.cpp                   # setup() / loop()
│   ├── config.h                   # hardware pins, buffer sizes
│   ├── state/
│   │   ├── immutable.h            # чтение из NVS
│   │   ├── immutable.cpp
│   │   ├── authoritative.h        # filter, delivery_record, time_sync_state
│   │   ├── authoritative.cpp
│   │   ├── local.h                # blacklist, nonce_ring
│   │   └── local.cpp
│   ├── crypto/
│   │   ├── domains.h              # domain tags
│   │   ├── ed25519.h              # обёртка Monocypher
│   │   ├── ed25519.cpp
│   │   ├── sealed_box.h
│   │   ├── sealed_box.cpp
│   │   ├── bloom.h
│   │   ├── bloom.cpp
│   │   └── key_id.h
│   ├── transport/
│   │   ├── apdu.h                 # PN532 APDU I/O
│   │   ├── apdu.cpp
│   │   ├── transfer.h             # PUSH/PULL layer
│   │   └── transfer.cpp
│   ├── ops/
│   │   ├── info.cpp
│   │   ├── access.cpp
│   │   ├── time_sync.cpp
│   │   ├── filter_update.cpp
│   │   ├── fdi.cpp
│   │   ├── blacklist.cpp
│   │   └── revoke_key.cpp
│   ├── hw/
│   │   ├── rtc.h                  # DS3231
│   │   ├── rtc.cpp
│   │   ├── lock.h                 # GPIO замка
│   │   ├── lock.cpp
│   │   ├── led.h                  # LED
│   │   └── led.cpp
│   ├── provisioning/
│   │   ├── serial_cmd.h           # UART-команды для прошивки ключей
│   │   └── serial_cmd.cpp
│   └── utils/
│       └── little_endian.h
├── lib/
│   ├── Monocypher/
│   │   ├── monocypher.c
│   │   └── monocypher.h
│   └── PN532/                     # выбранная библиотека
├── include/
├── data/                          # для LittleFS partition (не используется)
├── partitions.csv                 # кастомная partition table
└── tools/
    └── provisioner.py             # CLI на Python для provisioning через Serial
```

## 3. platformio.ini

```ini
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
monitor_speed = 115200
upload_speed = 921600
board_build.partitions = partitions.csv

build_flags =
    -Os
    -DCORE_DEBUG_LEVEL=3
    -DCONFIG_FREERTOS_HZ=1000
    ; отключить WiFi/BT не получится через flags на Arduino; 
    ; просто не использовать в коде

lib_deps =
    Wire
    ; PN532 — через HSU (High Speed UART). elechouse/PN532 поддерживает HSU-режим нативно.
    ; Клонируется как git submodule из-за включённых примеров (single lib не опубликована в PIO registry).
    https://github.com/elechouse/PN532.git
    ; DS3231:
    adafruit/RTClib@^2.1.1

monitor_filters = esp32_exception_decoder
```

**По библиотеке PN532.** В `lib/PN532/` после `pio lib install` окажется несколько папок (PN532, PN532_HSU, PN532_I2C, PN532_SPI). Для нашего случая используются **только PN532 + PN532_HSU**. Остальные папки можно удалить или исключить через `lib_ignore`:

```ini
lib_ignore =
    PN532_I2C
    PN532_SPI
    NDEF
    ; ndef-примеры нам не нужны
```

## 4. Partition table

`partitions.csv` для 4 MB flash:

```
# Name,    Type, SubType,  Offset,   Size,      Flags
nvs,       data, nvs,      0x9000,   0x6000,
phy_init,  data, phy,      0xf000,   0x1000,
factory,   app,  factory,  0x10000,  0x180000,
filter,    data, spiffs,   0x190000, 0x40000,
state,     data, nvs,      0x1D0000, 0x10000,
```

Итого:
- app: 1.5 MB
- filter: 256 KB (будет использоваться как двойной слот через SPIFFS API или кастомную логику)
- state: 64 KB (NVS для blacklist, delivery_record, time_sync_state)
- nvs (config): 24 KB для immutable

## 5. Hardware (pin mapping)

`config.h`:

```cpp
#pragma once

// =========================================================================
// PN532 — через UART (HSU, High Speed UART)
// ESP32 использует UART2 (Serial2) для общения с PN532.
// ESP32 TX2 (GPIO17) → PN532 RX
// ESP32 RX2 (GPIO16) ← PN532 TX
// ВНИМАНИЕ: на PN532-модуле перемычки SEL0/SEL1 должны быть в положении HSU (0,0).
// =========================================================================
#define PN532_UART_NUM 2                    // используем UART2 через HardwareSerial(2)
#define PN532_UART_RX_PIN 16                // ESP32 GPIO16 ← PN532 TX
#define PN532_UART_TX_PIN 17                // ESP32 GPIO17 → PN532 RX
#define PN532_UART_BAUD 115200              // elechouse/PN532 поддерживает 115200 HSU

// =========================================================================
// DS3231 — через I2C (Wire, отдельно от PN532)
// ESP32 GPIO21 (SDA) ↔ DS3231 SDA
// ESP32 GPIO22 (SCL) ↔ DS3231 SCL
// Адрес на шине: 0x68
// =========================================================================
#define I2C_SDA_PIN 21
#define I2C_SCL_PIN 22
#define I2C_FREQ 400000

// Lock control
#define LOCK_SIGNAL_PIN 26      // к реле/транзистору
#define LOCK_SIGNAL_ACTIVE_HIGH true

// LED (двуцветный, общий катод)
#define LED_RED_PIN 27
#define LED_GREEN_PIN 14

// Provisioning button (опционально, для entering provisioning mode)
#define PROVISIONING_BUTTON_PIN 0   // boot button на большинстве dev-плат

// Buffer sizes
#define TRANSFER_BUFFER_CAP 16384          // 16 KB
#define NONCE_RING_SIZE 8
#define NONCE_TTL_MS 10000                 // 10 seconds
#define LOCAL_BLACKLIST_CAP 256

// Lock defaults (overridable via provisioning)
#define DEFAULT_LOCK_SIGNAL_DURATION_MS 3000

// Protocol version
#define PROTOCOL_VERSION 1

// Filter limits
#define MAX_FILTER_BYTES (128 * 1024 - 1024)  // немного меньше слота, чтобы влезло с header/whitelist

// Clock skew tolerance
#define CLOCK_SKEW_SECONDS 60
```

### 5.1 Сводная таблица подключения

| ESP32 pin | Направление | Назначение | К устройству |
|---|---|---|---|
| GPIO16 (RX2) | IN | UART RX от PN532 | PN532 TX |
| GPIO17 (TX2) | OUT | UART TX к PN532 | PN532 RX |
| GPIO21 | bidir | I2C SDA | DS3231 SDA |
| GPIO22 | OUT/bidir | I2C SCL | DS3231 SCL |
| GPIO26 | OUT | сигнал замка | Реле / транзистор |
| GPIO27 | OUT | LED красный | LED anode/R |
| GPIO14 | OUT | LED зелёный | LED anode/G |
| GPIO0 | IN | provisioning button | boot кнопка (на DevKit) |
| 3.3V / 5V | PWR | питание | PN532 VCC (5V через 3.3V LDO на модуле), DS3231 VCC (3.3V) |
| GND | GND | общая земля | все устройства |

**Проверка на старте:** перед использованием PN532 код должен вызвать `pn532_hsu.begin()` и проверить `nfc.getFirmwareVersion()` — если возвращается 0, то либо неправильные пины, либо SEL-перемычки не в HSU, либо питание недостаточное (PN532 требует стабильные 5V при пиках до 150 mA).

## 6. Модель состояния

### 6.1 Immutable (NVS namespace "scud_imm")

Прошивается при provisioning через Serial. Read-only в рантайме.

| Ключ NVS | Тип | Размер |
|---|---|---|
| `reader_id` | blob | 16 B |
| `reader_ed_priv` | blob | 32 B |
| `reader_ed_pub` | blob | 32 B |
| `server_ed_pub` | blob | 32 B |
| `server_x_pub` | blob | 32 B |
| `reader_group_id` | blob | 16 B |
| `lock_duration_ms` | uint16 | 2 B |
| `lock_signal_pin` | uint8 | 1 B |
| `led_red_pin` | uint8 | 1 B |
| `led_green_pin` | uint8 | 1 B |
| `provisioned_flag` | uint8 | 1 B (= 1 когда прошито) |

Если `provisioned_flag != 1` → устройство запускается в provisioning-режиме (только UART), access-операции недоступны.

### 6.2 Authoritative

**В SPIFFS partition "filter":**
- Файл `/filter_A.bin` и `/filter_B.bin` — два слота filter_package.
- Файл `/filter_current.txt` — содержит "A" или "B" (текущий активный).

**В NVS partition "state", namespace "scud_auth":**
- `filter_version` (uint64) — версия текущего активного фильтра.
- `filter_m_bits` (uint32) — параметры bloom.
- `filter_k_hashes` (uint8).
- `filter_hash_seed` (uint32).
- `filter_generated_at` (uint64).
- `delivery_courier_id` (blob 16 B).
- `delivery_received_at` (uint64).
- `time_sync_last_at` (uint64) — `last_sync_at_local`.
- `time_sync_last_authority_id` (blob 16 B).
- `time_sync_last_kind` (uint8) — 0x01/0x02.
- `blacklist_count` (uint16) — количество записей.

### 6.3 Local (NVS namespace "scud_local")

- `blacklist_N_key_id` (blob 16 B) — для N ∈ [0, 255]
- `blacklist_N_meta` (blob 17 B) — revoked_at (8) + expires_at (8) + reason (1)

**nonce_ring и transfer_buffer — только в RAM.**

### 6.4 In-RAM структуры

```cpp
struct NonceEntry {
    uint8_t nonce[32];
    uint64_t issued_at_ms;  // millis() при выпуске
    bool consumed;
};

struct ReaderState {
    // Immutable (кэш, читается при старте)
    uint8_t reader_id[16];
    uint8_t reader_ed_priv[32];
    uint8_t reader_ed_pub[32];
    uint8_t server_ed_pub[32];
    uint8_t server_x_pub[32];
    uint8_t reader_group_id[16];
    uint16_t lock_duration_ms;
    
    // Authoritative cache
    uint64_t filter_version;
    uint32_t filter_m_bits;
    uint8_t filter_k_hashes;
    uint32_t filter_hash_seed;
    uint64_t filter_generated_at;
    uint8_t delivery_courier_id[16];
    uint64_t delivery_received_at;
    uint64_t time_sync_last_at;
    uint8_t time_sync_last_authority_id[16];
    uint8_t time_sync_last_kind;
    uint16_t blacklist_count;
    
    // In-RAM bloom filter (read from flash on boot)
    uint8_t* bloom_bytes;     // malloc'd, size = m_bits / 8
    
    // In-RAM whitelist (read from flash on boot)
    struct WhitelistEntry {
        uint8_t key_id[16];
        uint64_t expires_at;
    };
    WhitelistEntry* whitelist;
    uint16_t whitelist_count;
    
    // In-RAM local_blacklist (read from NVS on boot)
    struct BlacklistEntry {
        uint8_t key_id[16];
        uint64_t revoked_at;
        uint64_t expires_at;
        uint8_t reason;
    };
    BlacklistEntry blacklist[LOCAL_BLACKLIST_CAP];
    
    // Nonce ring (RAM only)
    NonceEntry nonce_ring[NONCE_RING_SIZE];
    
    // Transfer layer state
    uint32_t current_push_msg_id;
    uint8_t* push_buffer;
    uint32_t push_buffer_size;
    uint32_t push_expected_total;
    uint32_t push_received;
    uint8_t push_inner_opcode;
    uint64_t push_last_activity_ms;
    
    uint32_t current_pull_msg_id;
    uint8_t* pull_buffer;
    uint32_t pull_buffer_size;
    uint32_t pull_sent;
    uint64_t pull_last_activity_ms;
};

extern ReaderState g_state;
```

## 7. Криптография — Monocypher

`lib/Monocypher/monocypher.c` и `monocypher.h` — положить из https://monocypher.org/ (последняя стабильная версия, single-file).

### 7.1 Обёртки

`src/crypto/ed25519.h`:

```cpp
#pragma once
#include <stdint.h>

void ed25519_sign(
    const uint8_t priv[32], const uint8_t pub[32],
    const uint8_t* message, size_t len,
    uint8_t signature[64]);

bool ed25519_verify(
    const uint8_t pub[32],
    const uint8_t* message, size_t len,
    const uint8_t signature[64]);

// Для streaming verify (filter_package)
struct Ed25519VerifyCtx {
    // состояние SHA-512, используемое в Ed25519
};

void ed25519_verify_init(Ed25519VerifyCtx* ctx, const uint8_t pub[32]);
void ed25519_verify_update(Ed25519VerifyCtx* ctx, const uint8_t* data, size_t len);
bool ed25519_verify_finalize(Ed25519VerifyCtx* ctx, const uint8_t signature[64]);
```

**Monocypher API:**
- `crypto_eddsa_sign(sig, priv, msg, msg_len)` — комбинированный key derivation.
- `crypto_eddsa_check(sig, pub, msg, msg_len)` — всё в памяти.

**Для streaming verify:** Monocypher предоставляет `crypto_ed25519_check_init`, `crypto_ed25519_check_update`, `crypto_ed25519_check_final` (или эквиваленты в используемой версии). Проверить API актуальной версии при интеграции.

### 7.2 Domain tags

`src/crypto/domains.h`:

```cpp
#pragma once
#include <stdint.h>

// 16 B ASCII + \x00 padding
extern const uint8_t DOMAIN_KEY[16];  // "RDR-KEY-v1\0\0\0\0\0\0"
extern const uint8_t DOMAIN_INF[16];
extern const uint8_t DOMAIN_RSP[16];
extern const uint8_t DOMAIN_FLT[16];
extern const uint8_t DOMAIN_RCP[16];
extern const uint8_t DOMAIN_BLK[16];
extern const uint8_t DOMAIN_FDI[16];
extern const uint8_t DOMAIN_TGR[16];
extern const uint8_t DOMAIN_TIM[16];
extern const uint8_t DOMAIN_REV[16];
```

### 7.3 key_id

```cpp
#include "monocypher.h"

void compute_key_id(
    const uint8_t reader_id[16],
    const uint8_t phone_pubkey[32],
    uint64_t issued_at,
    uint32_t serial,
    uint8_t out_key_id[16])
{
    crypto_blake2b_ctx ctx;
    crypto_blake2b_init(&ctx, 16);  // 16-byte output
    // ВАЖНО: shared protocol говорит BLAKE2s-128. Monocypher даёт BLAKE2b.
    // Используем blake2s, если Monocypher предоставляет; иначе — подключить
    // blake2s reference impl. Обе версии BLAKE2 - разные алгоритмы,
    // они НЕ совпадают.
    // ...
}
```

**Критично:** протокол требует BLAKE2s-128, не BLAKE2b. Monocypher в современных версиях предоставляет только BLAKE2b. Варианты:
1. **Рекомендовано:** добавить reference BLAKE2s (blake2s-ref.c из RFC 7693 appendix) как ещё один single-file в lib/BLAKE2s/.
2. Альтернатива: изменить shared protocol на BLAKE2b-128. Это потребует согласования с backend и android — **не делать без координации**.

Для этого проекта — **BLAKE2s-128**. Подключить через `lib/BLAKE2s/blake2s-ref.c`.

### 7.4 sealed_box

```cpp
// Только decrypt не нужен (ридер только шифрует для сервера).
// Encrypt:

void sealed_box_encrypt(
    const uint8_t server_x25519_pub[32],
    const uint8_t* plaintext, size_t pt_len,
    uint8_t* out_blob /* 32 + pt_len + 16 */,
    const uint8_t random_bytes_32[32])  // источник рандома передаём явно
{
    // 1. ephemeral priv = random_bytes_32 (передан извне для testability)
    // 2. ephemeral_pub = X25519(ephemeral_priv, basepoint)
    crypto_x25519_public_key(out_blob /* write ephemeral_pub here */, random_bytes_32);
    
    // 3. shared = X25519(ephemeral_priv, server_x25519_pub)
    uint8_t shared[32];
    crypto_x25519(shared, random_bytes_32, server_x25519_pub);
    
    // 4. nonce = BLAKE2b(ephemeral_pub || server_x25519_pub, 24)[:12]
    uint8_t nonce_material[64];
    memcpy(nonce_material, out_blob, 32);              // ephemeral_pub
    memcpy(nonce_material + 32, server_x25519_pub, 32);
    uint8_t nonce_24[24];
    crypto_blake2b_ctx b;
    crypto_blake2b_init(&b, 24);
    crypto_blake2b_update(&b, nonce_material, 64);
    crypto_blake2b_final(&b, nonce_24);
    
    // 5. ChaCha20-Poly1305(shared, nonce_12, plaintext)
    uint8_t* ct = out_blob + 32;                // ciphertext goes here
    uint8_t* tag = out_blob + 32 + pt_len;      // tag after ciphertext
    crypto_aead_lock(
        ct,           // encrypted output
        tag,          // mac output (16)
        shared,       // key (32)
        nonce_24,     // 12-byte nonce (Monocypher uses 24?)
        NULL, 0,      // ad
        plaintext, pt_len
    );
    // ВАЖНО: Monocypher's crypto_aead_lock uses 24-byte nonce (XChaCha20-Poly1305).
    // Если протокол требует ChaCha20-Poly1305 с 12-байтным nonce — 
    // использовать mbedTLS mbedtls_chachapoly_encrypt.
    // 
    // Shared protocol §2.4: "ChaCha20-Poly1305" с 12-байтным nonce, но nonce
    // получаем как BLAKE2b(..., 24)[:12]. Monocypher НЕ подходит здесь — 
    // использовать встроенный mbedtls_chachapoly.
}
```

**Вывод по крипто:** использовать **смешанный стек**:
- Monocypher для Ed25519 и X25519.
- mbedtls/chachapoly.h для ChaCha20-Poly1305 (12-byte nonce).
- blake2s-ref.c для BLAKE2s-128 (key_id).
- Monocypher BLAKE2b для 24-байтного sealed-box nonce derivation.

### 7.5 Bloom

`src/crypto/bloom.h`:

```cpp
#pragma once
#include <stdint.h>

// MurmurHash3_x86_32 reference implementation
uint32_t murmur3_x86_32(const void* key, int len, uint32_t seed);

bool bloom_contains(
    const uint8_t* bits, uint32_t m_bits,
    uint8_t k_hashes, uint32_t hash_seed,
    const uint8_t key_id[16]);
```

```cpp
bool bloom_contains(
    const uint8_t* bits, uint32_t m_bits,
    uint8_t k_hashes, uint32_t hash_seed,
    const uint8_t key_id[16])
{
    for (uint8_t i = 0; i < k_hashes; i++) {
        uint32_t h = murmur3_x86_32(key_id, 16, hash_seed + i) % m_bits;
        if ((bits[h / 8] & (1 << (h % 8))) == 0) {
            return false;
        }
    }
    return true;
}
```

## 8. Transport layer

### 8.1 APDU через PN532 (HSU)

PN532 выступает в режиме **initiator** (reader). Телефон — Android HCE target.

**Инициализация (`src/transport/apdu.cpp`):**

```cpp
#include <Arduino.h>
#include <PN532.h>
#include <PN532_HSU.h>
#include "config.h"

// Используем UART2 на ESP32 (HardwareSerial 2)
static HardwareSerial pn532_serial(PN532_UART_NUM);
static PN532_HSU pn532_hsu(pn532_serial);
static PN532 nfc(pn532_hsu);

bool apdu_init() {
    // Настройка UART2 на указанных пинах
    pn532_serial.begin(PN532_UART_BAUD, SERIAL_8N1, PN532_UART_RX_PIN, PN532_UART_TX_PIN);
    
    nfc.begin();
    
    uint32_t versiondata = nfc.getFirmwareVersion();
    if (!versiondata) {
        Serial.println("[PN532] firmware version not found — check wiring/SEL jumpers/power");
        return false;
    }
    Serial.printf("[PN532] Found chip PN5%02X, firmware %d.%d\n",
        (versiondata >> 24) & 0xFF,
        (versiondata >> 16) & 0xFF,
        (versiondata >> 8) & 0xFF);
    
    // Configure SAM (Secure Access Module) — для режима initiator необходимо
    nfc.SAMConfig();
    
    // Timeout реакции на отсутствие целей — 0 значит не ждать бесконечно
    nfc.setPassiveActivationRetries(0x10);
    
    return true;
}

/**
 * Опрос PN532 на наличие HCE-цели.
 * Возвращает true если цель обнаружена и SELECT AID прошёл успешно.
 */
bool apdu_detect_target(uint32_t timeout_ms) {
    uint8_t uid[10];
    uint8_t uid_len = 0;
    
    bool found = nfc.readPassiveTargetID(
        PN532_MIFARE_ISO14443A, uid, &uid_len, timeout_ms);
    if (!found) return false;
    
    // После readPassiveTargetID ридер "активировал" карту.
    // Для HCE-телефона нужно затем послать SELECT AID APDU.
    
    uint8_t select_aid[] = {
        0x00, 0xA4, 0x04, 0x00,  // CLA INS P1 P2
        0x06,                     // Lc (длина AID)
        0xF0, 0x53, 0x43, 0x55, 0x44, 0x01,  // "SCUD" AID
        0x00                      // Le
    };
    uint8_t response[4];
    uint8_t resp_len = sizeof(response);
    
    bool ok = nfc.inDataExchange(select_aid, sizeof(select_aid), response, &resp_len);
    if (!ok || resp_len < 2) return false;
    // Успех если SW1 SW2 = 90 00
    return response[resp_len - 2] == 0x90 && response[resp_len - 1] == 0x00;
}

/**
 * Обмен APDU (input → output) с уже активированной целью.
 * Возвращает количество байт ответа, 0 при ошибке.
 */
size_t apdu_exchange(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    uint8_t resp_len = resp_max < 255 ? (uint8_t)resp_max : 255;
    bool ok = nfc.inDataExchange((uint8_t*)req, req_len, resp, &resp_len);
    return ok ? resp_len : 0;
}
```

Высокоуровневая последовательность (query-response модель, см. shared §4):

1. `apdu_init()` в setup() — однократно. Проверяет firmware version, настраивает SAM.
2. В loop() периодически вызывается `apdu_detect_target(100)` — опрашивает PN532 на наличие HCE-цели. Если true — SELECT AID уже прошёл.
3. Ридер запускает tap-сессию: `run_tap_session()`.
4. Внутри сессии:
   - Сформировать INFO (собственный state + fresh_nonce), подписать.
   - Отправить `PUSH_INFO` APDU.
   - В цикле: `FETCH` APDU с prev_result → получить от телефона либо OP_SINGLE, либо OP_CHUNKED, либо NO_OP.
   - Для OP_CHUNKED — тянуть чанки через `READ_CHUNK` APDU.
   - Выполнить операцию (access / time_sync / filter_update / fdi / blk / revoke).
   - Сформировать result; если > 252 B — отдать через серию `PUSH_CHUNK` APDU; иначе inline в следующем FETCH.
   - Завершить через `END` APDU когда phone вернул NO_OP.
5. При разрыве (телефон убрали) — `apdu_exchange` вернёт 0 байт; сессия очищается, loop возвращается к polling.

**AID:** `F0 53 43 55 44 01` (6 байт, префикс F0 + "SCUD" + version).

**APDU формат (CLA, INS, P1, P2, Lc, Data, Le):**

| Command | INS | Data |
|---|---|---|
| SELECT AID | A4 (P1=04) | AID 6B |
| PUSH_INFO | C1 | INFO 146 B |
| FETCH | C2 | prev_result (0..256 B) |
| READ_CHUNK | C3 | msg_id(4) + offset(4) + max_chunk_len(2) = 10 B |
| PUSH_CHUNK | C4 | msg_id(4) + offset(4) + total(4) + flags(1) + chunk_len(2) + chunk = 15 + chunk_len |
| END | C5 | пусто |

Для всех команд: CLA=0x00, P2=0x00, Le=0x00.

### 8.2 Transfer layer — implementation outline

```cpp
// src/transport/transfer.h

#define OPCODE_SELECT_AID 0xA4
#define OPCODE_PUSH_INFO  0xC1
#define OPCODE_FETCH      0xC2
#define OPCODE_READ_CHUNK 0xC3
#define OPCODE_PUSH_CHUNK 0xC4
#define OPCODE_END        0xC5

#define FETCH_STATUS_NO_OP       0x00
#define FETCH_STATUS_OP_SINGLE   0x01
#define FETCH_STATUS_OP_CHUNKED  0x02
#define FETCH_STATUS_ERROR       0x03

struct PendingResult {
    uint8_t* bytes;          // malloc'd
    size_t   len;
    uint32_t msg_id;         // для reference mode
    bool     pushed_via_chunks;  // true если отданы через PUSH_CHUNK-серию
};

/**
 * Запустить tap-сессию. Возвращает когда phone прислал NO_OP или
 * произошёл разрыв. Вся логика (формирование INFO, обработка операций,
 * шифрование response) — здесь.
 */
void run_tap_session();
```

`src/transport/transfer.cpp` (высокоуровневый скелет):

```cpp
#include "transfer.h"
#include "apdu.h"
#include "ops/info.h"
#include "ops/access.h"
#include "ops/time_sync.h"
#include "ops/filter_update.h"
#include "ops/fdi.h"
#include "ops/blacklist.h"
#include "ops/revoke_key.h"

static uint8_t g_apdu_buf_in[512];
static uint8_t g_apdu_buf_out[512];

static bool send_push_info() {
    uint8_t info[146];
    if (!build_info_bytes(info, sizeof(info))) return false;
    
    // APDU: 00 C1 00 00 Lc=0x92 <info 146> 00
    uint8_t apdu[5 + 146 + 1];
    apdu[0] = 0x00; apdu[1] = 0xC1; apdu[2] = 0x00; apdu[3] = 0x00;
    apdu[4] = 146;
    memcpy(apdu + 5, info, 146);
    apdu[5 + 146] = 0x00;
    
    uint8_t resp[4];
    size_t resp_len = apdu_exchange(apdu, sizeof(apdu), resp, sizeof(resp));
    return resp_len >= 2 && resp[resp_len-2] == 0x90 && resp[resp_len-1] == 0x00;
}

/**
 * Отправить FETCH с prev_result, получить от phone следующую операцию.
 * Возвращает status и заполняет поля в out.
 */
static bool send_fetch(const uint8_t* prev_result, size_t prev_len,
                       uint8_t* status, uint8_t* inner_opcode,
                       uint32_t* msg_id, uint32_t* total_len,
                       uint8_t* first_chunk, size_t* first_chunk_len) {
    // Подготовить prev_result encoding:
    //   EMPTY     = 0x00 0x00
    //   INLINE    = len(2B LE) + bytes
    //   REFERENCE = 0xFF 0xFF + msg_id(4B)
    uint8_t encoded[260];
    size_t enc_len;
    if (prev_result == nullptr || prev_len == 0) {
        encoded[0] = 0x00; encoded[1] = 0x00;
        enc_len = 2;
    } else if (prev_len <= 252) {
        encoded[0] = (uint8_t)(prev_len & 0xFF);
        encoded[1] = (uint8_t)((prev_len >> 8) & 0xFF);
        memcpy(encoded + 2, prev_result, prev_len);
        enc_len = 2 + prev_len;
    } else {
        // Большой result — должен был быть отдан через PUSH_CHUNK серию,
        // тогда prev_result здесь не должен был прийти напрямую.
        // Эта ветка — программная ошибка; обработать отдельно.
        return false;
    }
    
    // APDU: 00 C2 00 00 Lc encoded Le=0
    g_apdu_buf_out[0] = 0x00; g_apdu_buf_out[1] = 0xC2;
    g_apdu_buf_out[2] = 0x00; g_apdu_buf_out[3] = 0x00;
    g_apdu_buf_out[4] = (uint8_t)enc_len;
    memcpy(g_apdu_buf_out + 5, encoded, enc_len);
    g_apdu_buf_out[5 + enc_len] = 0x00;
    
    size_t rlen = apdu_exchange(g_apdu_buf_out, 5 + enc_len + 1,
                                 g_apdu_buf_in, sizeof(g_apdu_buf_in));
    if (rlen < 3) return false;  // минимум status + SW
    // Последние 2 байта = SW 90 00
    if (g_apdu_buf_in[rlen-2] != 0x90 || g_apdu_buf_in[rlen-1] != 0x00) return false;
    
    size_t payload_len = rlen - 2;  // без SW
    *status = g_apdu_buf_in[0];
    
    switch (*status) {
      case FETCH_STATUS_NO_OP:
        return true;
      case FETCH_STATUS_OP_SINGLE: {
        if (payload_len < 4) return false;
        *inner_opcode = g_apdu_buf_in[1];
        uint16_t op_len = (uint16_t)g_apdu_buf_in[2] | ((uint16_t)g_apdu_buf_in[3] << 8);
        if (payload_len < 4u + op_len) return false;
        *total_len = op_len;
        *first_chunk_len = op_len;
        memcpy(first_chunk, g_apdu_buf_in + 4, op_len);
        return true;
      }
      case FETCH_STATUS_OP_CHUNKED: {
        if (payload_len < 12) return false;
        *inner_opcode = g_apdu_buf_in[1];
        *msg_id = *(uint32_t*)&g_apdu_buf_in[2];     // LE
        *total_len = *(uint32_t*)&g_apdu_buf_in[6];  // LE
        *first_chunk_len = *(uint16_t*)&g_apdu_buf_in[10];
        if (payload_len < 12u + *first_chunk_len) return false;
        memcpy(first_chunk, g_apdu_buf_in + 12, *first_chunk_len);
        return true;
      }
      case FETCH_STATUS_ERROR:
      default:
        return false;
    }
}

/**
 * Читать следующий чанк операции от phone.
 */
static bool send_read_chunk(uint32_t msg_id, uint32_t offset, uint16_t max_chunk,
                             uint8_t* out_chunk, uint16_t* out_chunk_len, bool* out_last) {
    uint8_t data[10];
    memcpy(data + 0, &msg_id, 4);
    memcpy(data + 4, &offset, 4);
    memcpy(data + 8, &max_chunk, 2);
    
    uint8_t apdu[5 + 10 + 1] = {
        0x00, 0xC3, 0x00, 0x00, 10,
        data[0], data[1], data[2], data[3], data[4], data[5], data[6], data[7], data[8], data[9],
        0x00
    };
    
    size_t rlen = apdu_exchange(apdu, sizeof(apdu), g_apdu_buf_in, sizeof(g_apdu_buf_in));
    if (rlen < 5) return false;  // chunk_len(2) + flags(1) + SW(2)
    if (g_apdu_buf_in[rlen-2] != 0x90) return false;
    
    uint16_t chunk_len = *(uint16_t*)&g_apdu_buf_in[0];
    uint8_t flags = g_apdu_buf_in[2];
    if (rlen < 5u + chunk_len) return false;
    
    memcpy(out_chunk, g_apdu_buf_in + 3, chunk_len);
    *out_chunk_len = chunk_len;
    *out_last = (flags & 0x01) != 0;
    return true;
}

/**
 * Отправить чанк большого result на phone.
 * Возвращает true если все чанки залиты успешно (для последнего — flags.LAST=1).
 */
static bool send_push_chunk_series(uint32_t msg_id, const uint8_t* bytes, uint32_t total) {
    const uint16_t chunk_max = 240;
    uint32_t sent = 0;
    while (sent < total) {
        uint16_t to_send = (total - sent) > chunk_max ? chunk_max : (uint16_t)(total - sent);
        uint8_t flags = (sent + to_send == total) ? 0x01 : 0x00;
        
        uint8_t apdu_data[15 + 256];
        memcpy(apdu_data + 0, &msg_id, 4);
        memcpy(apdu_data + 4, &sent, 4);
        memcpy(apdu_data + 8, &total, 4);
        apdu_data[12] = flags;
        memcpy(apdu_data + 13, &to_send, 2);
        memcpy(apdu_data + 15, bytes + sent, to_send);
        
        uint8_t apdu[5 + 15 + 256 + 1];
        apdu[0] = 0x00; apdu[1] = 0xC4; apdu[2] = 0x00; apdu[3] = 0x00;
        apdu[4] = (uint8_t)(15 + to_send);
        memcpy(apdu + 5, apdu_data, 15 + to_send);
        apdu[5 + 15 + to_send] = 0x00;
        
        uint8_t resp[2];
        size_t rlen = apdu_exchange(apdu, 5 + 15 + to_send + 1, resp, sizeof(resp));
        if (rlen < 2 || resp[0] != 0x90 || resp[1] != 0x00) return false;
        
        sent += to_send;
    }
    return true;
}

/**
 * Главный цикл сессии.
 */
void run_tap_session() {
    if (!send_push_info()) {
        Serial.println("[TAP] PUSH_INFO failed");
        return;
    }
    
    uint8_t prev_result[512];
    size_t prev_len = 0;
    
    uint8_t* large_result_buf = nullptr;
    uint32_t large_result_msg_id = 0;
    bool prev_was_large = false;
    
    while (true) {
        // FETCH с prev_result
        uint8_t encoded_prev[260];
        size_t enc_len;
        if (prev_was_large) {
            // REFERENCE mode
            encoded_prev[0] = 0xFF; encoded_prev[1] = 0xFF;
            memcpy(encoded_prev + 2, &large_result_msg_id, 4);
            enc_len = 6;
            prev_was_large = false;
            free(large_result_buf);
            large_result_buf = nullptr;
        } else if (prev_len == 0) {
            encoded_prev[0] = 0; encoded_prev[1] = 0;
            enc_len = 2;
        } else {
            encoded_prev[0] = (uint8_t)(prev_len);
            encoded_prev[1] = (uint8_t)(prev_len >> 8);
            memcpy(encoded_prev + 2, prev_result, prev_len);
            enc_len = 2 + prev_len;
        }
        
        uint8_t status, inner_opcode;
        uint32_t msg_id = 0, total_len = 0;
        uint8_t first_chunk[260];
        size_t first_chunk_len = 0;
        
        if (!send_fetch(encoded_prev + 2, prev_len,
                        &status, &inner_opcode, &msg_id, &total_len,
                        first_chunk, &first_chunk_len)) {
            Serial.println("[TAP] FETCH failed");
            return;
        }
        
        if (status == FETCH_STATUS_NO_OP) {
            // Send END
            uint8_t end_apdu[6] = {0x00, 0xC5, 0x00, 0x00, 0x00, 0x00};
            uint8_t resp[2];
            apdu_exchange(end_apdu, 6, resp, 2);
            return;
        }
        
        if (status == FETCH_STATUS_ERROR) {
            Serial.println("[TAP] phone reported error, restart");
            return;  // либо реконнект через PUSH_INFO
        }
        
        // Получить полный payload операции
        uint8_t* op_payload = nullptr;
        size_t op_payload_len = total_len;
        
        if (status == FETCH_STATUS_OP_SINGLE) {
            op_payload = first_chunk;
            op_payload_len = first_chunk_len;
        } else if (status == FETCH_STATUS_OP_CHUNKED) {
            op_payload = (uint8_t*)malloc(total_len);
            if (!op_payload) return;
            memcpy(op_payload, first_chunk, first_chunk_len);
            
            uint32_t offset = first_chunk_len;
            while (offset < total_len) {
                uint16_t chunk_len = 0;
                bool last = false;
                if (!send_read_chunk(msg_id, offset, 240,
                                      op_payload + offset, &chunk_len, &last)) {
                    free(op_payload);
                    return;
                }
                offset += chunk_len;
                if (last) break;
            }
        }
        
        // Диспатчеризация операции
        uint8_t result_buf[512];
        size_t result_len = 0;
        
        switch (inner_opcode) {
          case 0x01: result_len = op_access(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          case 0x11: result_len = op_fdi(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          case 0x12: result_len = op_time_sync(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          case 0x13: result_len = op_filter_update(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          case 0x14: result_len = op_blacklist(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          case 0x15: result_len = op_revoke(op_payload, op_payload_len, result_buf, sizeof(result_buf)); break;
          default:   break;
        }
        
        if (status == FETCH_STATUS_OP_CHUNKED) free(op_payload);
        
        // Если result большой — залить через PUSH_CHUNK серию, переключить prev_was_large
        if (result_len > 252) {
            uint32_t new_msg_id = esp_random();
            if (!send_push_chunk_series(new_msg_id, result_buf, result_len)) {
                return;
            }
            // На следующем FETCH — REFERENCE
            large_result_msg_id = new_msg_id;
            prev_was_large = true;
            prev_len = 0;
        } else {
            memcpy(prev_result, result_buf, result_len);
            prev_len = result_len;
        }
    }
}
```

### 8.3 Практические ограничения PN532 + elechouse lib

Использование PN532 как **reader** (initiator) через HSU, Android как **HCE target** — стандартная и хорошо отлаженная схема.

**Буфер APDU в elechouse/PN532:** `PN532_PACKBUFFSIZE = 64` по умолчанию (константа в PN532.h). Для наших APDU до 256 B **необходимо увеличить** до минимум 300:

```cpp
// Отредактировать lib/PN532/PN532/PN532.h после установки:
#define PN532_PACKBUFFSIZE 300
```

Если этого не сделать — длинные APDU (PUSH_INFO 146 B, FETCH с ACCESS result, PUSH_CHUNK) будут обрезаться.

**MTU в INFO.max_apdu_size:** **256 B**. На стороне PN532 + elechouse это работает при PACKBUFFSIZE = 300+.

**Проверка при первой сборке:**
1. Прошить тестовый скетч с `nfc.getFirmwareVersion()` — должен вернуть что-то отличное от 0 (`PN532 firmware 1.6` или подобное).
2. Если 0 — последовательно проверить:
   - Перемычки SEL0, SEL1 на PN532-модуле (оба должны быть в "0" — это HSU-режим).
   - Питание: PN532 запитывается от 5V (не от 3.3V), при этом пины TX/RX модуля 3.3V-tolerant.
   - Пины: TX ESP32 идёт в RX PN532 и наоборот (сигнал перекрёстный).
   - Baud rate: 115200 — дефолт, другие скорости требуют отдельных AT-команд.

## 9. Handler'ы операций

### 9.1 Общий интерфейс

```cpp
// src/ops/ops.h

// Возвращает длину result bytes (включая result_marker как первый байт); 0 при ошибке.
// Все handlers следуют шаблону: parse operation → verify → execute → serialize result.

size_t op_access(const uint8_t* op_payload, size_t op_len, uint8_t* result, size_t result_max);
size_t op_time_sync(const uint8_t* op_payload, size_t op_len, uint8_t* result, size_t result_max);
size_t op_filter_update(const uint8_t* op_payload, size_t op_len, uint8_t* result, size_t result_max);
size_t op_fdi(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_blacklist(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_revoke(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
```

Диспетчеризация в transfer layer по `push_inner_opcode`.

### 9.2 op_info (см. shared §5.2)

```
1. Parse: inner_opcode=0x10, client_preamble (4 B).
2. Build response:
   - opcode = 0x90
   - client_preamble_echo
   - reader_id (from state)
   - reader_time = RTC.now() as unix seconds LE
   - protocol_version = 1
   - max_apdu_size = 240
   - filter_version = state.filter_version
   - filter_delivered_at = state.delivery_received_at
   - blacklist_count = state.blacklist_count
   - fresh_nonce = generate_random(32)
3. issue_nonce(fresh_nonce)  // добавить в ring
4. sign with DOMAIN_INF + bytes[0:82] → signature into bytes[82:146]
5. return 146 B
```

### 9.3 op_access (см. shared §5.3, §11)

Строго по алгоритму в shared §11. Весь request 256 B. Response VERDICT 42 B.

После успешного verdict → запустить lock (async через FreeRTOS task или timer).

### 9.4 op_time_sync (см. shared §5.13, §10)

```cpp
// Request: 289 B (opcode + grant 148 + statement 140)
// Response: OP_RESULT 49 B (17 header + 32 next_nonce ext)

size_t op_time_sync(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (req_len != 289) return make_op_result(resp, 0x12, 0x92, RES_BAD_FORMAT);
    
    const uint8_t* grant = req + 1;       // 148 B
    const uint8_t* statement = req + 149; // 140 B
    
    // Parse grant
    uint8_t grant_reader_id[16];     memcpy(grant_reader_id, grant + 1, 16);
    uint8_t grant_authority_pubkey[32]; memcpy(grant_authority_pubkey, grant + 17, 32);
    uint8_t grant_authority_id[16];  memcpy(grant_authority_id, grant + 49, 16);
    uint64_t grant_issued_at = read_u64le(grant + 65);
    uint64_t grant_expires_at = read_u64le(grant + 73);
    uint8_t grant_kind = grant[81];
    
    // Parse statement
    uint8_t st_reader_id[16];        memcpy(st_reader_id, statement + 1, 16);
    uint8_t st_authority_id[16];     memcpy(st_authority_id, statement + 17, 16);
    uint64_t st_new_time = read_u64le(statement + 33);
    uint8_t st_used_nonce[32];       memcpy(st_used_nonce, statement + 41, 32);
    uint8_t st_kind = statement[73];
    
    if (grant[0] != 1 || statement[0] != 1)
        return make_op_result(resp, 0x12, 0x92, RES_BAD_FORMAT);
    if (memcmp(grant_reader_id, g_state.reader_id, 16) != 0)
        return make_op_result(resp, 0x12, 0x92, RES_WRONG_READER);
    if (memcmp(st_reader_id, g_state.reader_id, 16) != 0)
        return make_op_result(resp, 0x12, 0x92, RES_WRONG_READER);
    if (memcmp(st_authority_id, grant_authority_id, 16) != 0)
        return make_op_result(resp, 0x12, 0x92, RES_BAD_FORMAT);
    if (st_kind != grant_kind)
        return make_op_result(resp, 0x12, 0x92, RES_BAD_FORMAT);
    if (!consume_nonce(st_used_nonce))
        return make_op_result(resp, 0x12, 0x92, RES_BAD_NONCE);
    
    uint64_t now = rtc_now();
    if (grant_expires_at <= now)
        return make_op_result(resp, 0x12, 0x92, RES_NOT_AUTHORIZED);
    
    // Soft/hard policy (shared §10)
    if (grant_kind == 0x02) {  // HARD
        // apply unconditionally (after signature check)
    } else if (grant_kind == 0x01) {  // SOFT
        if (g_state.time_sync_last_at == 0)
            return make_op_result(resp, 0x12, 0x92, RES_NOT_AUTHORIZED);
        uint64_t days = (now - g_state.time_sync_last_at) / 86400;
        if (days < 1) days = 1;
        int64_t window = 5 * (int64_t)days;
        int64_t diff = (int64_t)now - (int64_t)st_new_time;
        if (diff < 0) diff = -diff;
        if (diff > window)
            return make_op_result(resp, 0x12, 0x92, RES_TIME_REGRESSION);
    } else {
        return make_op_result(resp, 0x12, 0x92, RES_BAD_FORMAT);
    }
    
    // Verify grant signature
    uint8_t to_sign_grant[16 + 84];
    memcpy(to_sign_grant, DOMAIN_TGR, 16);
    memcpy(to_sign_grant + 16, grant, 84);
    if (!ed25519_verify(g_state.server_ed_pub, to_sign_grant, 100, grant + 84))
        return make_op_result(resp, 0x12, 0x92, RES_BAD_SIGNATURE);
    
    // Verify statement signature
    uint8_t to_sign_st[16 + 76];
    memcpy(to_sign_st, DOMAIN_TIM, 16);
    memcpy(to_sign_st + 16, statement, 76);
    if (!ed25519_verify(grant_authority_pubkey, to_sign_st, 92, statement + 76))
        return make_op_result(resp, 0x12, 0x92, RES_BAD_SIGNATURE);
    
    // Apply
    rtc_set(st_new_time);
    g_state.time_sync_last_at = st_new_time;
    memcpy(g_state.time_sync_last_authority_id, grant_authority_id, 16);
    g_state.time_sync_last_kind = grant_kind;
    save_time_sync_state_to_nvs();
    
    // Build response
    return make_op_result_with_nonce(resp, 0x12, 0x92, RES_OK);
}
```

### 9.5 op_filter_update (см. shared §12)

**Упрощение для MVP:** буферизировать в RAM (если фильтр помещается) или реализовать streaming через flash.

Для стартовой реализации **buffered** (размер фильтра ограничить `TRANSFER_BUFFER_CAP = 16 KB` в MVP; реальные фильтры до этого размера покрывают до ~8K отозванных ключей — достаточно).

Позже можно расширить до streaming, сохранив интерфейс handler'а.

Алгоритм:
```
1. Request layout: [inner_opcode=0x13][courier_id 16B][filter_package variable].
2. Parse header из filter_package (56 B).
3. Validate:
   - format_version == 1
   - reader_id == self
   - filter_version > current (STALE check)
   - generated_at <= now + SKEW
   - filter_bytes_len == m_bits / 8
   - whitelist_count <= 256
   - blacklist_delta_count <= 256
4. Compute expected total size:
   header(56) + filter_bytes_len + whitelist_count*24 + bl_delta_count*16 + signature(64)
   Должно совпадать с (req_len - 1 - 16).
5. Verify server_signature:
   Build message = DOMAIN_FLT || bytes[0 до signature) of filter_package
   ed25519_verify(server_ed_pub, message, signature).
   Если нет — BAD_SIGNATURE.
6. Apply:
   - Erase inactive flash slot, write new filter to it.
   - Update NVS: filter_version, m_bits, k_hashes, hash_seed, filter_generated_at, current_slot toggle.
   - Reload in-RAM bloom_bytes, whitelist.
   - Apply blacklist_delta: для каждого key_id — если в local_blacklist, убрать.
   - Update delivery_record: courier_id, received_at = now.
7. Build delivery_receipt (shared §5.7):
   - reader_id || applied_filter_version || applied_at || courier_id || signature(64)
   - sign(reader_ed_priv, DOMAIN_RCP || first 48 B).
8. Build response: OP_RESULT with ext = delivery_receipt || next_nonce (144 B ext).
```

### 9.6 op_fdi (см. shared §5.8)

Не требует nonce. Cleartext fields + encrypted_courier_blob + signed + next_nonce.

### 9.7 op_blacklist (см. shared §5.9)

Не требует nonce. Формирует plaintext по §5.9.1, шифрует sealed box, подписывает cleartext envelope.

Если `num_entries > 200` — response превысит один APDU, transport layer автоматически перейдёт в PULL-режим.

### 9.8 op_revoke (см. shared §5.10, §13)

Строго по алгоритму shared §13.

## 10. Lock control

### 10.1 Актуатор — `src/hw/lock.cpp`

Один GPIO-сигнал (`LOCK_SIGNAL_PIN=26`, `LOCK_SIGNAL_ACTIVE_HIGH=true`), снаружи заведённый
на реле/MOSFET/драйвер электрозамка. Открытие — **импульс** заданной длины в фоновой
FreeRTOS-таске (возврат немедленный, основной цикл не блокируется). `lock_task` —
**чистый актуатор страйка**: он больше НЕ зажигает onboard-LED (индикация развязана и
живёт в `hw/led`, см. §10.3), поэтому замена актуатора не гасит световой фидбэк:

```cpp
static void lock_task(void* arg) {
    uint16_t duration_ms = (uint16_t)(uintptr_t)arg;
    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? HIGH : LOW);
    vTaskDelay(duration_ms / portTICK_PERIOD_MS);
    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? LOW : HIGH);
    s_busy = false;
    vTaskDelete(NULL);
}
void lock_trigger_open(uint16_t duration_ms) {         // re-entrancy guard: s_busy
    if (s_busy) return;
    s_busy = true;
    xTaskCreate(lock_task, "lock", 2048, (void*)(uintptr_t)duration_ms, 1, nullptr);
}
```

Длительность — `g_state.lock_duration_ms` (дефолт `DEFAULT_LOCK_SIGNAL_DURATION_MS=3000`,
кламп `[LOCK_DURATION_MIN_MS .. LOCK_DURATION_MAX_MS]`, задаётся provisioning `SET-LOCK-DURATION`).

### 10.2 Точка адаптации для интеграторов — `src/integration/access_hooks.{h,cpp}`

«Что физически делать на решении о доступе» вынесено в **единственную точку расширения** —
две функции-хука. Это поддерживаемый шов для тех, кто разворачивает ридер под себя
(электрозащёлка, маглок, турникет, шлагбаум, зуммер, внешняя релейная плата, site-local лог):

```cpp
void on_access_granted(uint16_t lock_duration_ms);   // GRANTED: что открыть/сделать
void on_access_denied(uint8_t result_code);          // DENIED:  как отреагировать (RES_*)
```

**Разделение ответственности:**

| | Платформа (мы — НЕ трогать ради деплоя) | Интегратор (вы — правите `access_hooks.cpp`) |
|---|---|---|
| Проверка ACCESS | 7 проверок в `op_access` (nonce/replay, reader_id, время, blacklist, bloom+whitelist, подпись сервера, подпись телефона) | — |
| Вызов хука | ровно 1 раз на вердикт, с **главного цикла** (B3-safe), под cooldown-гейтом | — |
| Anti-retap cooldown | `cd_grant`/`cd_end` (от config + `lock_duration_ms`) | — |
| **Действие** | — | **что делает `on_access_*`** (замок/турникет/лог/…) |

Иначе говоря: **платформа гарантирует, что хук дёрнется в правильный момент с доверенным
решением; деплой решает, что хук делает.** Контракт (когда вызывается, что гарантировано,
требование «вернуться быстро / тяжёлое — в фоновую таску») — в шапке `access_hooks.h`.

**Вызов (платформенный диспетчер)** — `transfer.cpp::apply_access_result`, общий для NFC и BLE
(фикс L1: раньше actuation дублировался и пути расходились). На `RES_OK` (и не в cooldown):
`on_access_granted(lock_duration_ms)` + установка grant-cooldown; на любой deny:
`on_access_denied(result_code)` + end-cooldown. result-байты при этом не трогаются.

**Дефолт (`access_hooks.cpp`) — demo-kit:** `on_access_granted` → `lock_trigger_open(...)`
(импульс на замок) **И** `led_feedback_granted(lock_duration_ms)` — две **независимые** побочки,
так что замена актуатора не гасит индикацию; `on_access_denied` → `led_feedback_deny()`
(короткая вспышка). Эти тела и предназначены к замене под конкретную инсталляцию —
**без правки платформенного кода**, который верифицирует ACCESS и зовёт хуки.

> Cooldown после GRANTED считается от `lock_duration_ms`. Если ваш актуатор отрабатывает
> дольше (медленный барьер) — поднимите `lock_duration_ms` (или `cd_grant`) через provisioning,
> чтобы anti-retap окно покрывало всё движение (платформа не пере-вызовет `on_access_granted`,
> пока идёт cooldown).

### 10.3 Индикация — `src/hw/led.cpp` (неблокирующая, tick-driven)

Один монохромный onboard-LED (`LED_ONBOARD_PIN`); SUCCESS/FAIL различаются **паттерном**, не цветом.
Фидбэк армится из хуков и крутится `led_tick()` из главного цикла — **не** detached-таской:

```cpp
void led_feedback_granted(uint32_t window_ms);  // solid ON на окно, затем авто-OFF
void led_feedback_deny();                        // 3×120/120 мс мигание, самозавершается
bool led_feedback_active();                      // true пока паттерн играет — гейт сна для power_idle()
void led_tick();                                 // зовётся каждую итерацию loop(); millis()-gated, не блокирует
```

Почему tick, а не fire-and-forget таска: в будущем `battery_nfc`-профиле (`docs/13` §3.7) `power_idle()`
зовёт `esp_deep_sleep_start()` = reset чипа, который убил бы detached-таску на полупаттерне и оставил
LED залипшим. Tick-модель отдаёт `led_feedback_active()`, по которому `power_idle()` гейтит сон явно
(рядом с `lock_is_busy()`), и переживает light-sleep, т.к. расписание на `millis()`. OFF-edge granted-окна
живёт здесь же (а не в `lock_task`), поэтому индикация развязана с замком.

## 11. Main loop

`src/main.cpp`:

```cpp
#include <Arduino.h>
#include <Wire.h>
#include "config.h"
#include "state/immutable.h"
#include "state/authoritative.h"
#include "state/local.h"
#include "hw/rtc.h"
#include "hw/led.h"
#include "transport/apdu.h"
#include "transport/transfer.h"
#include "provisioning/serial_cmd.h"

void setup() {
    Serial.begin(115200);
    delay(200);
    
    // GPIO setup
    pinMode(LOCK_SIGNAL_PIN, OUTPUT);
    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? LOW : HIGH);
    pinMode(LED_RED_PIN, OUTPUT);
    pinMode(LED_GREEN_PIN, OUTPUT);
    led_set_red();
    pinMode(PROVISIONING_BUTTON_PIN, INPUT_PULLUP);
    
    // I2C bus (только для DS3231)
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ);
    
    // Load immutable config
    if (!load_immutable_config()) {
        Serial.println("[PROVISIONING MODE] Device not provisioned. Use Serial CLI.");
        led_provisioning_blink();
        while (true) {
            handle_provisioning_serial();
            delay(100);
        }
    }
    
    // Check provisioning button at boot (hold to enter provisioning)
    if (digitalRead(PROVISIONING_BUTTON_PIN) == LOW) {
        Serial.println("[PROVISIONING MODE] entered by button.");
        while (true) {
            handle_provisioning_serial();
            delay(100);
        }
    }
    
    // Load authoritative + local state
    load_authoritative_state();
    load_local_state();
    load_filter_from_flash();
    
    // Init DS3231 RTC через I2C
    if (!rtc_init()) {
        Serial.println("[WARN] DS3231 init failed; time will not advance reliably");
        // НЕ fatal — можно продолжить работу, но time_sync становится критичен
    }
    
    // Init PN532 через HSU (UART2)
    if (!apdu_init()) {
        Serial.println("[FATAL] PN532 HSU init failed; check SEL0/SEL1 jumpers and wiring");
        led_fatal_blink();
        while (true) delay(1000);
    }
    
    Serial.println("[READY]");
}

void loop() {
    // Опрашиваем PN532 на появление HCE-цели.
    // apdu_detect_target возвращает true когда цель найдена И SELECT AID прошёл успешно.
    if (apdu_detect_target(100 /* ms */)) {
        // Один физический контакт с телефоном = одна tap-сессия.
        // Внутри run_tap_session: PUSH_INFO → цикл FETCH/READ_CHUNK → END.
        // Возвращается когда phone прислал NO_OP, либо произошёл разрыв.
        run_tap_session();
    }
    
    // Housekeeping
    cleanup_stale_nonces();
    expire_local_blacklist_entries();
    
    delay(10);
}
```

**Важные детали.** `run_tap_session()` — единственная точка входа в обмен с телефоном. Внутри она сама вызывает `apdu_exchange()` с нужными командами (PUSH_INFO, FETCH, READ_CHUNK, PUSH_CHUNK, END). Никаких `handle_apdu`/`apdu_exchange_send` не нужно — они были частью старой симметричной модели и больше не применяются.

При физическом разрыве (телефон убран) — `apdu_exchange()` в какой-то команде вернёт 0 байт, `run_tap_session()` завершится, loop() вернётся к поллингу.

## 12. Provisioning через Serial

Когда устройство не прошито (`provisioned_flag != 1`) или удерживается кнопка при загрузке, включается provisioning-режим. CLI через Serial на 115200 baud.

### 12.1 Команды

```
HELP                            — список команд
GEN-KEYPAIR                     — сгенерировать новую пару ridader Ed25519
                                  (сохраняет в RAM, не применяет)
SHOW-PUBKEY                     — вывести сгенерированный pubkey в hex
SET-READER-ID <hex16>           — установить reader_id
SET-GROUP-ID <hex16>            — установить reader_group_id  
SET-SERVER-ED-PUB <hex32>       — server Ed25519 pubkey
SET-SERVER-X-PUB <hex32>        — server X25519 pubkey
SET-LOCK-DURATION <ms>          — duration замка
COMMIT                          — записать всё в NVS, выставить provisioned_flag=1
RESET                           — стереть immutable config, отменить
SET-TIME <unix_sec>             — установить RTC (для первого времени)
STATUS                          — показать текущее состояние config
```

### 12.2 Процедура (CLI-скрипт `tools/provisioner.py`)

```python
#!/usr/bin/env python3
"""
Provisioning CLI for SCUD reader.

Usage:
    provisioner.py --port /dev/ttyUSB0 --server https://scud.example.com --admin-api-key sk_admin_...
"""

import argparse
import serial
import requests
import uuid
import time
import base64

def send_cmd(ser, cmd, timeout=5):
    ser.write((cmd + "\n").encode())
    time.sleep(0.2)
    response = ser.read_all().decode()
    return response

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--admin-api-key", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--group-id", required=True)  # UUID string
    parser.add_argument("--description", default="")
    args = parser.parse_args()
    
    ser = serial.Serial(args.port, 115200, timeout=2)
    time.sleep(2)  # ESP32 reboot settle
    
    print("1. Generating reader keypair...")
    send_cmd(ser, "GEN-KEYPAIR")
    
    resp = send_cmd(ser, "SHOW-PUBKEY")
    # parse hex from resp
    reader_pubkey_hex = extract_hex(resp, "pubkey=")
    reader_pubkey = bytes.fromhex(reader_pubkey_hex)
    
    reader_id = uuid.uuid4().bytes  # 16 B
    reader_id_hex = reader_id.hex()
    
    print(f"2. Enrolling reader on server...")
    r = requests.post(
        f"{args.server}/api/v1/admin/readers/enroll",
        headers={"X-Api-Key": args.admin_api_key},
        json={
            "reader_id": reader_id_hex,
            "reader_pubkey": base64.b64encode(reader_pubkey).decode(),
            "reader_group_id": args.group_id,
            "display_name": args.display_name,
            "description": args.description
        }
    )
    r.raise_for_status()
    enrollment = r.json()
    
    server_ed_pub = base64.b64decode(enrollment["server_ed25519_pubkey"])
    server_x_pub = base64.b64decode(enrollment["server_x25519_pubkey"])
    
    print("3. Flashing config to reader...")
    send_cmd(ser, f"SET-READER-ID {reader_id_hex}")
    send_cmd(ser, f"SET-GROUP-ID {uuid.UUID(args.group_id).bytes.hex()}")
    send_cmd(ser, f"SET-SERVER-ED-PUB {server_ed_pub.hex()}")
    send_cmd(ser, f"SET-SERVER-X-PUB {server_x_pub.hex()}")
    send_cmd(ser, f"SET-TIME {int(time.time())}")
    
    print("4. Committing...")
    resp = send_cmd(ser, "COMMIT")
    if "OK" not in resp:
        print("ERROR:", resp)
        return 1
    
    print(f"\nReader provisioned: {reader_id_hex}")
    print("Reboot the device now.")

if __name__ == "__main__":
    main()
```

## 13. Тесты и критерии приёмки

### 13.1 Unit tests (on-device через `test/` в PlatformIO)

- `test_bloom_roundtrip`: добавить ключи в bloom на Python reference, проверить contains() на устройстве даёт True.
- `test_key_id_computation`: известные входы → известный выход (сравнение с Python reference).
- `test_ed25519_verify`: прошить server_ed_pub, проверить подпись от соответствующего privkey.
- `test_sealed_box_encrypt_decrypt`: зашифровать сообщение X25519-парой, расшифровать на Python.

### 13.2 Integration

С реальным Android-приложением или mock-телефоном (Android emulator + HCE):

1. Провижнировать ридер через CLI.
2. Скомпилировать Android app с тем же server_url.
3. Создать пользователя, permit.
4. Выпустить issued_key на телефон.
5. Поднести к ридеру — дверь открывается.
6. Отозвать ключ (через сервер), сгенерировать новый filter, скачать на другой телефон, доставить ридеру.
7. Проверить что старый ключ не проходит (REVOKED_FILTER).

### 13.3 Критерии приёмки

- Ридер собирается из исходников через PlatformIO без warnings.
- При первом запуске (не прошитый) выводит в Serial "PROVISIONING MODE".
- CLI-утилита успешно прошивает ридер и он после reboot выходит в [READY].
- INFO-ответ правильно подписан и парсится на стороне Android (можно проверить через приложение).
- ACCESS с валидным ключом открывает замок в течение 3 секунд от касания телефона.
- FILTER_UPDATE применяется, delivery_receipt возвращается.
- REVOKE_KEY добавляет запись в local_blacklist, при GET_BLACKLIST она присутствует.

## 14. Ключевые замечания

- **RTC backup battery обязательна** для DS3231, иначе время сбрасывается при пропадании питания.
- **При потере питания во время FILTER_UPDATE** — неактивный слот может быть частично записан. После boot: проверить что активный слот (по current_slot в NVS) цел (signature verify). Если нет — фатальная ошибка.
- **ESP32 Arduino random:** `esp_random()` даёт криптостойкий RNG, использовать его для fresh_nonce.
- **PN532 через HSU, не I2C:** I2C-режим у этого чипа известен проблемами (clock stretching, коллизии). HSU надёжнее. Перемычки SEL0=0, SEL1=0 на модуле.
- **DS3231 через Wire I2C (GPIO21/22):** отдельная шина от PN532.
- **BLE отключён в MVP:** закомментирован в коде, готовность к подключению позже.
- **Heap fragmentation:** фильтр освобождать/перевыделять через `heap_caps_malloc_prefer(..., MALLOC_CAP_SPIRAM)` если есть PSRAM; иначе стандартный heap.
- **Wire-протокол Reader ↔ Phone — query-response** (см. shared §4): Reader единственный инициатор APDU, Phone (HCE target) отвечает. Последовательность SELECT AID → PUSH_INFO → цикл FETCH/READ_CHUNK/PUSH_CHUNK → END. Это согласуется с физической ролью PN532 как NFC initiator и Android HCE как target.
