# SCUD Reader firmware (ESP32, Arduino/PlatformIO)

Прошивка ридера СКУД для ESP32. Реализует протокол из `docs/00_shared_protocol.md` и hardware-спецификацию из `docs/02_firmware_spec.md`.

## Hardware

- ESP32 (WROOM-32E 4 MB минимум)
- PN532 NFC-модуль — подключается через **UART (HSU)**, перемычки SEL0/SEL1 в положение 0/0
- DS3231 RTC через I2C
- Реле/транзистор на GPIO26 для замка
- Двуцветный LED (красный GPIO27, зелёный GPIO14)

См. `src/config.h` — конкретные пины.

## Сборка

1. Установить PlatformIO (VSCode extension или CLI).
2. Положить Monocypher (см. `lib/Monocypher/README.md`).
3. PN532-драйвер уже вендорится в `lib/PN532/` и `lib/PN532_HSU/` (вырезано из монорепы [elechouse/PN532](https://github.com/elechouse/PN532) — LDF PlatformIO не умеет корректно выбрать нужную подпапку при прямом клоне). Дополнительно ставить ничего не нужно.
4. Увеличить `PN532_PACKBUFFSIZE` в `lib/PN532/PN532.h` до **300**. Обязательно — иначе длинные APDU будут обрезаться.
5. `pio run --target upload`
6. `pio device monitor` — увидеть `[PROVISIONING] device not provisioned.`

Крипто-примитивы (X25519 + IETF ChaCha20-Poly1305 для sealed-box, BLAKE2b, Ed25519) целиком берутся из Monocypher — mbedTLS в Arduino-сборке ESP32 собран без `MBEDTLS_CHACHAPOLY_C`, поэтому AEAD собран вручную из `crypto_chacha20_ietf` + `crypto_poly1305` по RFC 8439.

## Провижининг

```bash
cd tools
pip install pyserial requests
python provisioner.py \
    --port /dev/ttyUSB0 \
    --server https://scud.example.com \
    --admin-api-key sk_admin_xxx \
    --group-id 12345678-1234-1234-1234-123456789abc \
    --display-name "Entrance"
```

После `COMMIT` — ребут (RST) — ридер выходит в `[READY]` и готов принимать телефоны.

## Структура кода

```
src/
├── main.cpp                       setup()/loop()
├── config.h                       пины, константы
├── crypto/                        Ed25519, X25519, BLAKE2s, sealed-box, bloom
├── state/                         immutable (NVS) + authoritative (SPIFFS+NVS) + local
├── hw/                            RTC, lock, LED
├── transport/                     PN532 HSU + FETCH/PUSH_CHUNK session
├── ops/                           handlers для всех inner opcodes
└── provisioning/                  UART CLI
lib/
├── Monocypher/                    external crypto (положить вручную)
├── PN532/                         elechouse/PN532 core (вендорится)
└── PN532_HSU/                     elechouse/PN532 HSU transport (вендорится)
tools/
└── provisioner.py                 CLI-утилита enroll + flash
```

## Критерии приёмки

- `pio run` проходит без warnings.
- Первый boot в provisioning-режиме, CLI-команды принимаются.
- После `provisioner.py` и ребута — `[READY]`.
- Valid access_request открывает замок в пределах 3 секунд.
- FILTER_UPDATE применяется, delivery_receipt подписан.
- REVOKE_KEY добавляет key_id в local blacklist, он возвращается в GET_BLACKLIST.

## Известные упрощения MVP

- FILTER_UPDATE буферизуется в RAM (ограничение `TRANSFER_BUFFER_CAP = 16 KB`). Streaming через flash — follow-up.
- BLE отключён.
- WiFi не используется (radio не стартует).
