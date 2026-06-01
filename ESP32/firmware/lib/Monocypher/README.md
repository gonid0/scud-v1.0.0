# Monocypher

Прошивка требует Monocypher 4.x (single-file crypto library, ISC licence).

## Установка

1. Скачать последнюю стабильную версию с https://monocypher.org/ (или клонировать https://github.com/LoupVaillant/Monocypher).
2. Положить в эту папку **ровно четыре файла**:
   - `monocypher.c` + `monocypher.h` (core, из `src/`)
   - `monocypher-ed25519.c` + `monocypher-ed25519.h` (опциональный модуль из `src/optional/`)

После этого PlatformIO автоматически соберёт библиотеку (она автодискаверится в `lib/`).

## Почему именно 4.x + optional Ed25519

Core `crypto_eddsa_*` в Monocypher построен на **BLAKE2b** и несовместим со стандартным Ed25519 (libsodium / PyNaCl / BouncyCastle). Для interop с сервером и Android-приложением используется модуль `monocypher-ed25519.*`, где SHA-512:
- `crypto_ed25519_key_pair`, `crypto_ed25519_sign`, `crypto_ed25519_check` (RFC 8032 Ed25519)

Также используются из core:
- `crypto_x25519`, `crypto_x25519_public_key` (X25519)
- `crypto_blake2b_*` (BLAKE2b-24 для sealed_box nonce)
- `crypto_wipe`

В 3.x имена других (`crypto_sign` etc.) — прошивка их НЕ поддерживает.

## Не нужно

- ChaCha20-Poly1305 (12-B nonce) — берётся из встроенного mbedTLS ESP32.
- BLAKE2s — собственный reference-impl в `src/crypto/blake2s.*`.
