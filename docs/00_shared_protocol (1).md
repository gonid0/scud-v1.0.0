# 00. Shared Protocol — общий контракт

**Этот документ — единый источник правды по:**
- Байтовым форматам всех объектов, передаваемых между компонентами.
- Криптографическим примитивам и параметрам.
- Опкодам и transfer layer.

Любое отклонение в любой из трёх реализаций от этого документа — баг. Любое изменение здесь требует синхронной правки в backend, firmware и android.

---

## 1. Общие правила

### 1.1 Byte order

Вся сериализация полей — **little-endian**, packed (без выравнивания).

Многобайтные числа (`uint32`, `uint64`) кодируются LE. Строки — raw bytes, без NUL-терминатора если не указано. UUID — raw 16 bytes (не ASCII).

### 1.2 Кодирование в HTTP API

Бинарные объекты (issued_key, filter_package, grants) в JSON-теле передаются как **base64-стандарт** (без URL-safe-варианта, с паддингом `=`).

### 1.3 Таймстемпы

Все `*_at` поля в байтовых структурах — **uint64 unix timestamp в секундах** с 1970-01-01 UTC.

В JSON API — ISO-8601 строка (`2026-04-20T10:00:00Z`).

### 1.4 ID

| ID | Размер | Формат |
|---|---|---|
| `reader_id` | 16 B | UUID v4 raw bytes |
| `permit_id` | 16 B | UUID v4 raw bytes |
| `courier_id` | 16 B | детерминированный UUID v5 от `(user_id, device_id)` |
| `authority_id` | 16 B | UUID v4 raw bytes |
| `key_id` | 16 B | BLAKE2s-128 производное, см. §5.1 |
| `reader_group_id` | 16 B | UUID v4 raw bytes |

---

## 2. Криптографические примитивы

### 2.1 Алгоритмы

| Назначение | Алгоритм | Размер |
|---|---|---|
| Подпись Ed25519 | RFC 8032 | pubkey 32 B, privkey 32 B, signature 64 B |
| ECDH | X25519 (RFC 7748) | pubkey 32 B, privkey 32 B, shared 32 B |
| AEAD | ChaCha20-Poly1305 (RFC 8439) | nonce 12 B (extended to 24 B для sealed-box), tag 16 B |
| Hash (длинный) | BLAKE2b | variable, используем 24 B для sealed-box nonce |
| Hash (короткий) | BLAKE2s | 16 B для key_id |
| Bloom hashing | MurmurHash3 x86_32 (seed32) | 32-bit output |

### 2.2 Библиотеки

| Язык | Рекомендуемая библиотека |
|---|---|
| Python (backend) | `PyNaCl` (libsodium bindings) + `mmh3` |
| Kotlin (android) | `BouncyCastle` (Ed25519, X25519), встроенный Android Keystore для AES-GCM, `mmh3` из JVM-порта |
| C++/C (firmware, ESP32 Arduino) | `mbedTLS` (встроен в ESP32 Arduino core) для Ed25519/X25519/ChaCha20-Poly1305/BLAKE2b; `MurmurHash3` reference implementation |

### 2.3 Signing domains

Каждая подпись в системе — это `Ed25519_sign(privkey, domain_tag || payload)`. Префикс разделяет пространства подписей, предотвращая cross-protocol attacks.

Все domain tags — **16 B ASCII, дополнены `\x00` до 16 байт**:

```c
"RDR-KEY-v1\0\0\0\0\0\0"  // issued_key
"RDR-INF-v1\0\0\0\0\0\0"  // INFO response signed by reader
"RDR-RSP-v1\0\0\0\0\0\0"  // access_response signed by phone
"RDR-FLT-v1\0\0\0\0\0\0"  // filter_package signed by server
"RDR-RCP-v1\0\0\0\0\0\0"  // delivery_receipt signed by reader
"RDR-BLK-v1\0\0\0\0\0\0"  // get_blacklist response signed by reader
"RDR-FDI-v1\0\0\0\0\0\0"  // filter_delivery_info signed by reader
"RDR-TGR-v1\0\0\0\0\0\0"  // time_authority_grant signed by server
"RDR-TIM-v1\0\0\0\0\0\0"  // time_sync_statement signed by phone
"RDR-REV-v1\0\0\0\0\0\0"  // revoke_key request signed by phone
"RDR-PSG-v1\0\0\0\0\0\0"  // passage_receipt signed by reader  (§15)
"RDR-BLE-v1\0\0\0\0\0\0"  // BLE session_token signed by reader (§17)
```

### 2.4 Sealed box (X25519 + ChaCha20-Poly1305)

Используется для шифрования данных от ридера к серверу через недоверенного курьера (Android).

> ⚠️ **Это кастомная схема, НЕ libsodium-совместимая.** Похожа на `crypto_box_seal` по
> идее (эфемерный X25519 + AEAD), но KDF другой: **ключом AEAD служит `BLAKE2b(shared, 32)`**
> (libsodium прогнал бы X25519-секрет через HSalsa20). То есть реализация «как в libsodium» НЕ
> расшифрует эти блоки, и наоборот. Схема безопасна в данной модели угроз (ридер
> доверенный, шифрует серверу; курьер лишь переносит opaque-блоб и без server-priv не
> вскроет/не подделает; **свежий ephemeral на каждый блоб** → нет переиспользования
> ключа/nonce).
>
> **`CRYPTO-05` (KDF-«блендер») — СДЕЛАНО.** Раньше ключом AEAD служил СЫРОЙ X25519-секрет
> без KDF; теперь применяется равномерный ключ `key = BLAKE2b(shared, output_len = 32)`.
> KDF применён ИДЕНТИЧНО на обеих сторонах, касающихся блоба: backend (decrypt + reference
> encrypt) и firmware (reader encrypt). Android не участвует (он лишь переносит opaque-блоб).
> Nonce-деривация (`BLAKE2b(eph_pub || server_pub, 24)[:12]`) и layout блоба
> (`eph_pub(32) || ct_and_tag`) — БЕЗ изменений. Interop зафиксирован golden-вектором
> с фиксированным ephemeral-ключом (`Backend/tests/test_sealed_box.py`).

**Шифрование (на ридере):**

```
ephemeral_priv = X25519_generate()
ephemeral_pub  = X25519_basepoint_mult(ephemeral_priv)

shared = X25519(ephemeral_priv, server_x25519_R_pub)

key = BLAKE2b(input = shared, output_len = 32)   // KDF «блендер» (CRYPTO-05)

nonce_24 = BLAKE2b(input = ephemeral_pub || server_x25519_R_pub,
                   output_len = 24)

ct_and_tag = ChaCha20-Poly1305(key = key,
                                nonce = nonce_24,   // 24-byte XChaCha-style, но для ChaCha20-Poly1305 используем первые 12 байт
                                aad = empty,
                                plaintext = plaintext)

sealed_blob = ephemeral_pub || ct_and_tag    // 32 + |plaintext| + 16 байт
```

**Уточнение по nonce:** используется ChaCha20-Poly1305 (не XChaCha20-Poly1305). Nonce — 12 B. Из 24-байтного BLAKE2b-output берутся **первые 12 байт** как nonce. Оставшиеся 12 байт не используются — они остались в спецификации как артефакт изначальной XChaCha20-конвенции libsodium. При реализации: `chacha20poly1305_nonce = BLAKE2b(ephemeral_pub || server_x25519_R_pub, 24)[:12]`.

**Расшифровка (на сервере):**

```
ephemeral_pub = sealed_blob[0:32]
ct_and_tag   = sealed_blob[32:]

shared = X25519(server_x25519_R_priv, ephemeral_pub)

key = BLAKE2b(shared, 32)   // KDF «блендер» (CRYPTO-05) — идентично encrypt-стороне

nonce = BLAKE2b(ephemeral_pub || server_x25519_R_pub, 24)[:12]

plaintext = ChaCha20-Poly1305-decrypt(key, nonce, ct_and_tag, aad = empty)
```

Аутентификация обеспечивается Poly1305-tag'ом внутри AEAD.

### 2.5 MurmurHash3 для bloom

Reference: https://github.com/aappleby/smhasher/blob/master/src/MurmurHash3.cpp (MurmurHash3_x86_32).

Использование в фильтре:

```
for i in [0, k_hashes):
    h = MurmurHash3_x86_32(key_id, seed = hash_seed + i)
    bit_position = h % m_bits
```

Критично: все три реализации должны давать идентичный результат для одних и тех же входов.

### 2.6 Argon2id для паролей (только backend)

Параметры: `argon2id`, time_cost=3, memory_cost=64 MiB, parallelism=4. Через библиотеку `argon2-cffi` для Python. Хранится в `users.password_hash` как строка полного encoded format (`$argon2id$...`).

---

## 3. Opcode space

## 3. Opcode space

Здесь важно различать **inner opcodes** (семантика операций в логике протокола — ACCESS, TIME_SYNC и т.п.) и **wire commands** (формат APDU между ридером и телефоном на уровне транспорта). Wire commands описываются в §4.

### 3.1 Inner opcodes (семантические операции)

Это байт, идентифицирующий тип содержимого операции. Используется:
- на телефоне при формировании операции в ответ на FETCH;
- на ридере при dispatch'е handler'а.

| Код | Операция | Подписывает phone |
|---|---|---|
| 0x01 | ACCESS | yes |
| 0x10 | (зарезервировано — INFO, формируется ридером, не передаётся как операция) | — |
| 0x11 | GET_FILTER_DELIVERY_INFO | no |
| 0x12 | TIME_SYNC | yes (authority) |
| 0x13 | FILTER_UPDATE | server signature внутри |
| 0x14 | GET_BLACKLIST | no |
| 0x15 | REVOKE_KEY | yes |
| 0x16 | *(удалён — GET_PASSAGE_RECEIPT; receipt теперь в хвосте ACCESS_VERDICT, §5.4/§15)* | — |
| 0xF0–0xFF | provisioning / debug (UART only) | — |

Коды 0x80+ убраны — в query-response модели response-opcodes не нужны (результат возвращается ридером через PUSH_CHUNK либо в prev_result поле FETCH).

### 3.2 Wire commands (APDU команды Reader → Phone)

| INS | Имя | Назначение |
|---|---|---|
| 0xA4 (P1=04) | SELECT AID | стандартная ISO/IEC 7816-4 активация сервиса |
| 0xC1 | PUSH_INFO | Reader отдаёт свой подписанный INFO телефону |
| 0xC2 | FETCH | Reader запрашивает следующую операцию, передавая prev_result |
| 0xC3 | READ_CHUNK | Reader читает следующий чанк операции, начатой ранее |
| 0xC4 | PUSH_CHUNK | Reader отдаёт чанк большого результата |
| 0xC5 | END | Reader завершает сессию |

CLA всегда `0x00`, P2 всегда `0x00`. Все APDU — стандартные (не extended); полная длина data ограничена MTU (см. §4.2).

---

## 4. Transfer layer (query-response)

Вся логика обмена построена на том, что **Reader (PN532 initiator) — единственный инициатор APDU**, а Phone (HCE target) отвечает на команды. Это соответствует физической роли NFC-устройств в связке PN532 + Android HCE.

### 4.1 Жизненный цикл tap-сессии

```
1. SELECT AID                           — phone отвечает 0x9000
2. PUSH_INFO (INFO 146 B)               — phone парсит/верифицирует, строит operations queue
3. цикл {
      FETCH (prev_result)               — phone возвращает next_op ИЛИ no_op ИЛИ error
      if no_op: break
      if op_single: обработать op, сформировать result
      if op_chunked: READ_CHUNK до конца, обработать op, сформировать result
      if result большой: PUSH_CHUNK-серия; следующий FETCH с prev_result=reference
      else: следующий FETCH с prev_result=bytes
   }
4. END                                  — phone очищает сессию
```

### 4.2 MTU

Транспорт — **стандартные short APDU** (ISO 7816-4): длина data-части (Lc/Le)
кодируется одним байтом, т.е. **≤ 255 B**. Реализация ридера держит ещё меньший
бюджет — `MAX_APDU_DATA_SIZE = 240 B` (`firmware/src/config.h`): это укладывается
в `uint8_t` responseLength PN532 HAL с запасом под 12-байтовый заголовок
chunked-кадра и 2 байта SW. **Extended APDU и патч PN532_PACKBUFFSIZE не нужны.**

> Историческая правка: ранее здесь значилось «256 B, на 1 байт сверх ISO-лимита,
> нужен extended APDU / патч PN532». Это неверно: на проводе максимум — `uint8_t`
> (`inDataExchange`), и код никогда не шлёт > 240 B в одном APDU. Дизайн —
> чистый short-APDU.

Объекты, не влезающие в один APDU (ACCESS 256 B, FDI 241 B, filter_package,
большой BLK), передаются **по частям**: телефон отдаёт `OP_CHUNKED`, ридер
дочитывает через `READ_CHUNK` (§4.6), а крупный результат ридер льёт через
`PUSH_CHUNK` (§4.7). В один APDU целиком помещаются INFO (146 B),
ACCESS_VERDICT (42 B на отказе / 234 B на RES_OK с приложенной passage_receipt,
§5.4 — оба ≤ 240 B), OP_RESULT с nonce (49 B) и прочие мелкие.

Значение бюджета сообщается телефону в поле `max_apdu_size` структуры INFO
(§5.2); оно **≤ 255** и на практике равно `MAX_APDU_DATA_SIZE`.

### 4.3 SELECT AID

Стандартный ISO/IEC 7816-4 APDU:
```
CLA INS P1 P2 Lc  Data                       Le
00  A4  04 00 06  F0 53 43 55 44 01          00
```

Phone отвечает `0x9000`.

### 4.4 PUSH_INFO

Reader отправляет подписанный INFO телефону сразу после SELECT AID.

**Request:**
```
CLA INS P1 P2 Lc    Data                Le
00  C1  00 00 0x92  INFO 146 B          00
```

**Response:** `0x9000`.

Phone обязан в этот момент:
- Распарсить INFO (формат §5.2).
- Верифицировать `reader_signature` через свой кэш `readers_known[reader_id].reader_pubkey`.
- Если reader_id неизвестен — записать его как "unknown reader", не верифицировать подпись (такой режим нужен для курьерства на незнакомый ридер).
- Сохранить `fresh_nonce` в своём tap_session.
- Построить operations queue через дерево решений (подробнее — в android-spec).

### 4.5 FETCH

Основная команда обмена. Reader в каждой итерации tap-цикла отправляет результат предыдущей операции и запрашивает следующую.

**Request:**
```
CLA INS P1 P2 Lc    Data                  Le
00  C2  00 00 var   prev_result encoded   00
```

`prev_result encoded` — один из трёх форматов:

| Формат | Байт 0-1 | Байты 2+ | Смысл |
|---|---|---|---|
| EMPTY | `0x00 0x00` | нет | нет результата (первый FETCH после INFO или после no-result op) |
| INLINE | `len 2B LE` (1 ≤ len ≤ 252) | `result_bytes` | короткий результат в этом же APDU |
| REFERENCE | `0xFF 0xFF` | `msg_id 4B` | результат уже передан серией PUSH_CHUNK |

**Response:**
```
[status 1B] [payload variable] SW1 SW2=9000
```

Максимальная длина response (включая SW) — **256 B**.

**status values:**

| Status | Имя | Payload |
|---|---|---|
| 0x00 | NO_OP | пусто. Phone сообщает что operations queue пуст. Reader может делать END. |
| 0x01 | OP_SINGLE | `[inner_opcode 1B] [op_len 2B LE] [op_bytes]`. Полная операция в одном APDU. `op_len + 4 ≤ 252`. |
| 0x02 | OP_CHUNKED | `[inner_opcode 1B] [msg_id 4B] [total_len 4B LE] [first_chunk_len 2B LE] [first_chunk_bytes]`. Операция не помещается; reader должен читать остальное через READ_CHUNK. |
| 0x03 | ERROR | `[reason 1B]`. См. значения ниже. |

**Error reasons (status=0x03):**

| Reason | Имя | Смысл |
|---|---|---|
| 0x01 | BAD_PREV_RESULT | prev_result не смог быть обработан (битый формат / неизвестная структура) |
| 0x02 | SESSION_LOST | phone потерял tap_session (перезапустился, reset) — reader должен начать сначала с PUSH_INFO |
| 0x03 | INTERNAL_ERROR | внутренняя ошибка на phone |

### 4.6 READ_CHUNK

Чтение следующего чанка операции, которая пришла как OP_CHUNKED.

**Request:**
```
CLA INS P1 P2 Lc   Data                                         Le
00  C3  00 00 0A   msg_id 4B + offset 4B + max_chunk_len 2B     00
```

- `msg_id` — идентификатор из FETCH/OP_CHUNKED.
- `offset` — оффсет от начала `op_bytes` (первый READ_CHUNK использует offset = `first_chunk_len`, т.е. сразу после first_chunk, полученного в FETCH).
- `max_chunk_len` — максимум, сколько reader может принять в этом APDU (≤ 252).

**Response:**
```
[chunk_len 2B LE] [flags 1B] [chunk_bytes] SW 9000
```

- `flags` bit0 = LAST (больше нет чанков).
- После `flags.LAST=1` reader должен полностью обработать операцию.

Если phone не знает msg_id (например, tap_session был сброшен между APDU) — возвращает SW `0x6A88` (Referenced data not found); reader должен начать сначала с FETCH.

### 4.7 PUSH_CHUNK

Передача большого результата от reader к phone в несколько APDU. Используется, когда `prev_result` не помещается в один FETCH (> 252 B). Применимо к большим BLK-response, возможно FDI при больших N.

**Request:**
```
CLA INS P1 P2 Lc   Data                                                                  Le
00  C4  00 00 var  msg_id 4B + offset 4B + total 4B + flags 1B + chunk_len 2B + chunk   00
```

- `msg_id` — локальный идентификатор этого результата (reader генерирует random uint32).
- `offset` — оффсет chunk в полном result.
- `total` — полная длина result.
- `flags` bit0 = LAST.
- `chunk_len` — длина chunk_bytes.

**Response:** `0x9000` (принято) или `0x6A80` (bad params).

После того, как все чанки залиты (последний с flags.LAST=1), reader делает `FETCH` с `prev_result = REFERENCE(msg_id)` — phone знает, что result надо взять из своего буфера PUSH_CHUNK (который собирался параллельно с получением APDU).

**Phone-side:** phone держит до 2 активных msg_id для PUSH_CHUNK одновременно (на случай переотправки после ошибки). Старший msg_id вытесняет предыдущий при переполнении.

### 4.8 END

```
CLA INS P1 P2 Lc  Data    Le
00  C5  00 00 00  empty   00
```

Response: `0x9000`.

Phone очищает tap_session, освобождает все msg_id буферы.

Формально END необязателен — если телефон убирают от ридера без END, HCE-сервис получает `onDeactivated`, сессия сбрасывается автоматически. END используется в нормальном сценарии, когда phone вернул NO_OP и reader решил завершить.

### 4.9 Timeouts и recovery

- TTL tap-сессии на phone: **30 секунд** с последнего APDU. По истечении — сессия сбрасывается. На следующем FETCH phone вернёт `ERROR(SESSION_LOST)`.
- Reader при ERROR(SESSION_LOST) — делает PUSH_INFO заново и начинает цикл.
- При физическом разрыве (телефон убрали) — `apdu_exchange` вернёт ошибку/0 байт. Reader очищает свою сессию.

### 4.10 Streaming на ридере для FILTER_UPDATE

Для FILTER_UPDATE (до ~127 KB) phone возвращает на FETCH `OP_CHUNKED`. Reader затем:

1. Парсит первые 56 B (header filter_package, из first_chunk).
2. Валидирует header: format_version, reader_id, filter_version > current, generated_at.
3. Если header ок — инициализирует streaming SHA-512 verifier (часть Ed25519 verify flow).
4. Стирает неактивный слот flash.
5. Пишет first_chunk в flash с оффсета 0.
6. В цикле READ_CHUNK: читает следующие чанки, пишет в flash, обновляет verifier.
7. На LAST — finalize verify через last 64 B (signature).
8. Если верификация прошла — atomic swap активного слота, обновление NVS, применение blacklist_delta, формирование delivery_receipt.
9. Next FETCH с prev_result = delivery_receipt (112 B inline или через PUSH_CHUNK, если потребуется).

---

## 5. Форматы объектов

Все структуры — packed little-endian. Указаны размеры в байтах.

### 5.1 issued_key (151 B)

Ключ прохода. Подписан `server_ed25519_R_priv`.

```
Offset  Size  Field
0       1     format_version    (= 0x01)
1       16    reader_id
17      32    phone_pubkey
49      8     issued_at
57      8     expires_at
65      16    permit_id
81      4     serial            (uint32, счётчик в рамках permit)
85      2     payload           (forward-compat, ридер не интерпретирует)
87      64    server_signature  (Ed25519 над domain_KEY || bytes[0:87])
```

**domain_KEY** = `b"RDR-KEY-v1\x00\x00\x00\x00\x00\x00"` (16 B).

**Вычисление key_id (16 B):**

```
key_id = BLAKE2s(
    input = reader_id ‖ phone_pubkey ‖ issued_at_8LE ‖ serial_4LE,
    output_len = 16
)
```

Важно: `issued_at` и `serial` сериализуются little-endian перед хешированием. Результат — 16 байт. Должен совпадать между backend (при выпуске) и firmware (при верификации).

### 5.2 INFO (146 B)

Структура, которую reader передаёт телефону через APDU `PUSH_INFO` (§4.4) сразу после SELECT AID. Содержит всё, что нужно phone'у для формирования операций: reader_id, текущее время, текущую версию filter'а, fresh_nonce для signed-операций.

```
Offset  Size  Field
0       1     format_version         (= 0x01)
1       16    reader_id
17      8     reader_time
25      1     protocol_version       (= 0x01)
26      2     max_apdu_size          (uint16 LE)
28      8     filter_version         (uint64 LE)
36      8     filter_delivered_at
44      2     blacklist_count        (uint16 LE)
46      32    fresh_nonce
78      4     session_seq            (uint32 LE, инкрементирующийся счётчик tap-сессий с момента boot; для дедупликации)
82      64    reader_signature       (Ed25519 над domain_INF || bytes[0:82])
```

Размер: 146 B.

**Изменение от предыдущей ревизии:**
- Убраны поля `opcode` и `client_preamble_echo` (нет request-response пары, это просто структура данных).
- Добавлен `session_seq` — позволяет phone'у определить, когда reader "перезапустился" и нужно начать всё сначала (например, после power cycle nonce ring очищается).
- `format_version` в начале для forward-compat.

Размер тот же 146 B, все offset сдвинуты на -1 относительно старого формата.

**domain_INF** = `b"RDR-INF-v1\x00\x00\x00\x00\x00\x00"` (16 B).

### 5.3 ACCESS request (256 B)

Phone предъявляет issued_key для прохода.

```
Offset  Size  Field
0       1     inner_opcode         (= 0x01)
1       151   issued_key           (§5.1)
152     32    used_nonce           (fresh_nonce из предыдущего ответа ридера)
184     8     reader_time_echo
192     64    phone_signature
```

**phone_signature** = `Ed25519_sign(phone_privkey, domain_RSP || reader_id || used_nonce || reader_time_echo || key_id)`

Размер request: 256 B.

### 5.4 ACCESS_VERDICT result (42 B на отказе / 234 B на RES_OK)

Формируется reader'ом после обработки ACCESS. Возвращается в следующем FETCH как `prev_result` (inline, помещается в один APDU).

42-байтовый префикс **вердикта** (build_verdict) байт-в-байт неизменен:

```
Offset  Size  Field
0       1     result_marker   (= 0x81, для dispatch на phone)
1       1     result          (см. §7; 0x00 = RES_OK, иначе deny-код)
2       8     reader_time      (uint64 LE = rtc_now())
10      32    next_nonce
```

**На успехе (`result == RES_OK`)** к хвосту ответа **дописывается** 192-байтовая
passage_receipt — та же структура, что раньше отдавал отдельный op_passage (§15.3):

```
Offset  Size  Field
0       1     result_marker          (= 0x81, MARK_ACCESS_VERDICT)
1       1     result                 (= 0x00 RES_OK)
2       8     reader_time
10      32    next_nonce
42      192   passage_receipt        (§15.3; ПРИСУТСТВУЕТ ⇔ result == RES_OK)
                                      total = 234 B на RES_OK
```

- На **любом отказе** receipt **отсутствует** → ответ ровно 42 B (как раньше).
- `next_nonce` остаётся на `[10:42]` в **обоих** случаях (не сдвигается receipt'ом).
- Байты `[0:42]` — это существующий вывод build_verdict, **байт-идентичный и неизменный**.
- Подписанная область receipt'а — `response[42:170]` (signed под `DOMAIN_PSG`),
  подпись — `response[170:234]` (см. §15.3, raw-смещения receipt'а 0:128 / 128:192).
- **Backend-контракт неизменен:** phone заливает на сервер только «голое» 192-B тело
  receipt'а (`response[42:234]`) под существующим report-type `"passage_receipt"`;
  парсинг/верификация/дедуп на сервере (§15.6) — байт-идентичны.

Размер: **42 B на отказе, 234 B на `RES_OK`**.

**Phone side:** парсит prev_result по первому байту (result_marker):
- 0x81 → ACCESS_VERDICT, обновить UI "Дверь открыта / отказ", сохранить next_nonce.
  При `result == RES_OK` и длине 234 B — извлечь passage_receipt из `[42:234]` и
  положить в outgoing_reports (§15). На отказе (42 B) receipt'а нет.
- 0x91 → FDI, см. §5.8.
- 0x93 → FILTER_UPDATE result (OP_RESULT + delivery_receipt), см. §5.7 и §6.
- 0x94 → BLK, см. §5.9.
- 0x92 / 0x95 → OP_RESULT (TIME_SYNC / REVOKE_KEY).

### 5.5 filter_package (variable, до ~127 KB)

Подписан `server_ed25519_R_priv`.

**Header (56 B):**

```
Offset  Size  Field
0       1     format_version           (= 0x01)
1       16    reader_id
17      8     filter_version
25      8     generated_at
33      4     m_bits                   (uint32 LE, кратно 8)
37      1     k_hashes
38      4     hash_seed                (uint32 LE)
42      4     filter_bytes_len         (= m_bits / 8)
46      2     whitelist_count          (uint16 LE, ≤ 256)
48      2     blacklist_delta_count    (uint16 LE, ≤ 256)
50      6     padding                  (zeroed)
```

**Body:**

```
[filter_bytes_len]    bloom_bytes
[whitelist_count × 24] whitelist[]        // отсортирован по key_id
[blacklist_delta_count × 16] blacklist_delta[]  // неотсортирован
[64]                  server_signature    // Ed25519 над domain_FLT || header || body (без подписи)
```

**whitelist entry (24 B):**
```
Offset  Size  Field
0       16    key_id
16      8     expires_at
```

**blacklist_delta entry (16 B):**
```
Offset  Size  Field
0       16    key_id
```

### 5.6 FILTER_UPDATE operation

Операция, которую phone возвращает ридеру через FETCH → OP_CHUNKED (inner_opcode=0x13). Полный размер — до ~127 KB. Reader читает остальные чанки через READ_CHUNK.

```
Offset  Size        Field
0       1           inner_opcode   (= 0x13)
1       16          courier_id
17      ...         filter_package (§5.5, до ~127 KB)
```

`courier_id` не подписан сервером, используется ридером только для `filter_delivery_record` и `delivery_receipt`.

**Result (returned by reader):** `OP_RESULT` (см. §6) с `result_marker = 0x93`, ext содержит `delivery_receipt || next_nonce` (144 B). Итого prev_result в следующем FETCH = 13 (OP_RESULT header) + 144 (ext) = **157 B**. Помещается в INLINE.

### 5.7 delivery_receipt (112 B, в ext OP_RESULT)

Подписан `reader_ed25519_priv`.

```
Offset  Size  Field
0       16    reader_id
16      8     applied_filter_version
24      8     applied_at
32      16    courier_id
48      64    reader_signature    (Ed25519 над domain_RCP || bytes[0:48])
```

### 5.8 FDI result (241 B)

**Operation (from phone):** 1 B, просто `[inner_opcode = 0x11]`. Передаётся через FETCH → OP_SINGLE, op_len=1.

**Result (from reader), 241 B:**

```
Offset  Size  Field
0       1     result_marker               (= 0x91)
1       16    reader_id
17      8     filter_version
25      8     filter_delivered_at
33      104   encrypted_courier_blob      (§5.8.1)
137     8     reader_time_now
145     64    reader_signature            (Ed25519 над domain_FDI || bytes[0:145])
209     32    next_nonce
```

241 B — впритык для MTU 256. Помещается inline в prev_result следующего FETCH.

#### 5.8.1 encrypted_courier_blob

Sealed box (§2.4) на `server_x25519_R_pub`.

**Plaintext (56 B):**

```
Offset  Size  Field
0       16    reader_id
16      8     filter_version
24      16    courier_id
40      8     received_at
48      8     reader_time
```

**Blob:** `ephemeral_x25519_pub (32) || ct_and_tag (56 + 16 = 72)` = **104 B**.

### 5.9 GET_BLACKLIST result

**Operation (from phone):** 1 B, `[inner_opcode = 0x14]`. Через FETCH → OP_SINGLE.

**Result (from reader), cleartext envelope, 123 B + blob + next_nonce:**

```
Offset  Size       Field
0       1          result_marker          (= 0x94)
1       16         reader_id
17      8          reader_time
25      2          num_entries            (uint16 LE)
27      2          blob_len               (uint16 LE)
29      blob_len   encrypted_blob         (§5.9.1)
29+blob_len  64    reader_signature       (Ed25519 над domain_BLK || bytes[0:29+blob_len])
...     32         next_nonce
```

Итоговый размер: `29 + blob_len + 64 + 32` = **125 + blob_len**.

Для N=0: 125 + 82 = **207 B** (помещается inline).
Для N=1: 125 + 82 + 32 = **239 B** (помещается inline, впритык).
Для N=2: 125 + 82 + 64 = **271 B** (не помещается в 256 B; **требует PUSH_CHUNK-серию**).
Для N=256 (максимум): 125 + 82 + 32×256 = 8399 B (~33 PUSH_CHUNK APDU по 252 B).

#### 5.9.1 encrypted_blob

Sealed box (§2.4) на `server_x25519_R_pub`.

**Plaintext (34 + 32×N B):**

```
Offset  Size        Field
0       16          reader_id
16      8           reader_time
24      8           last_applied_filter_version
32      2           num_entries
34      32×N        entries[]
```

**Entry (32 B):**

```
Offset  Size  Field
0       16    key_id
16      8     revoked_at
24      8     expires_at
```

**Примечание:** `reason` (например, `0x01 = BY_USER`) хранится **локально** на ридере и не передаётся в blob. Размер entry строго 32 B; это согласуется с формулой полного размера response `207 + 32×N B`, используемой в §5.9.

**Blob size:** 32 + (34 + 32×N) + 16 = **82 + 32×N B**.

**Полный response size:** 29 + (82 + 32×N) + 64 + 32 = **207 + 32×N B**. Для N=256: 8399 B.

### 5.10 REVOKE_KEY operation (407 B)

Операция, которую phone возвращает reader'у через FETCH → OP_CHUNKED (inner_opcode=0x15, помещается в два чанка APDU). Для самоотзыва: `requester_issued_key == target_issued_key`.

```
Offset  Size  Field
0       1     inner_opcode               (= 0x15)
1       151   requester_issued_key       (§5.1)
152     151   target_issued_key          (§5.1)
303     32    used_nonce
335     8     reader_time_echo
343     64    phone_signature
```

**phone_signature** = `Ed25519_sign(requester_phone_privkey, domain_REV || reader_id || used_nonce || reader_time_echo || requester.key_id || target.key_id)`

**Chunking:** 407 B > MTU (256). Phone отдаёт через FETCH → OP_CHUNKED, first_chunk ~240 B payload. Остальное через READ_CHUNK.

**Result (from reader):** `OP_RESULT` с `result_marker = 0x95`, ext содержит `next_nonce` (32 B). Итого prev_result = 13 + 32 = **45 B** (inline).

### 5.11 time_authority_grant (148 B)

Подписан `server_ed25519_R_priv`.

```
Offset  Size  Field
0       1     format_version       (= 0x01)
1       16    reader_id
17      32    authority_pubkey     (phone_pubkey телефона-authority)
49      16    authority_id
65      8     issued_at
73      8     expires_at
81      1     kind                 (0x01 = soft, 0x02 = hard)
82      2     padding              (zeroed)
84      64    server_signature     (Ed25519 над domain_TGR || bytes[0:84])
```

### 5.12 time_sync_statement (140 B)

Подписан `authority_phone_privkey`.

```
Offset  Size  Field
0       1     format_version       (= 0x01)
1       16    reader_id
17      16    authority_id
33      8     new_time
41      32    used_nonce
73      1     kind                 (echo из grant)
74      2     padding              (zeroed)
76      64    authority_signature  (Ed25519 над domain_TIM || bytes[0:76])
```

### 5.13 TIME_SYNC operation (289 B)

Операция, которую phone возвращает reader'у через FETCH → OP_CHUNKED (inner_opcode=0x12).

```
Offset  Size  Field
0       1     inner_opcode   (= 0x12)
1       148   grant          (§5.11)
149     140   statement      (§5.12)
```

**Chunking:** 289 B > MTU (256). Phone отдаёт через FETCH → OP_CHUNKED, first_chunk ~240 B. Остальное через READ_CHUNK.

**Result:** `OP_RESULT` с `result_marker = 0x92`, ext = next_nonce (32 B). Итого prev_result = 45 B (inline).

---

## 6. Generic OP_RESULT

Базовый формат результата для операций, которые не имеют собственной структуры result (TIME_SYNC 0x92, REVOKE_KEY 0x95, FILTER_UPDATE 0x93).

Используется как `prev_result` в следующем FETCH.

```
Offset  Size       Field
0       1          result_marker          // 0x92, 0x93, 0x95
1       1          in_reply_to            // 0x12, 0x13, 0x15
2       1          result                 // см. §7
3       8          reader_time
11      2          ext_len                (uint16 LE)
13      ext_len    ext                    // op-specific
```

**Для FILTER_UPDATE (0x93):** ext = `delivery_receipt (112 B) || next_nonce (32 B)`, ext_len = 144. Полный размер OP_RESULT = 13 + 144 = **157 B** (inline в FETCH).

**Для TIME_SYNC (0x92) и REVOKE_KEY (0x95):** ext = `next_nonce (32 B)`, ext_len = 32. Полный размер = 13 + 32 = **45 B** (inline).

---

## 7. Коды result

**Базовые (могут появиться в любом OP_RESULT):**

| Код | Имя | Смысл |
|---|---|---|
| 0x00 | OK | успех |
| 0x01 | BAD_SIGNATURE | подпись не прошла верификацию |
| 0x02 | BAD_FORMAT | парсинг/версия/неизвестный payload |
| 0x03 | BAD_NONCE | nonce не совпал / replay / истёк |
| 0x04 | NO_SLOT | нет места (blacklist / transfer buffer / параллельная операция) |
| 0x05 | NOT_AUTHORIZED | нет прав (soft time sync при last_sync=0) |
| 0x06 | STALE | версия ниже текущей (filter_version) |
| 0x07 | WRONG_READER | объект адресован другому ридеру |
| 0x08 | TIME_REGRESSION | new_time вне допустимого окна |
| 0x09 | UNKNOWN_OPCODE | ридер не умеет операцию |
| 0xFF | INTERNAL_ERROR | всё остальное |

**Access-specific (в ACCESS_VERDICT 0x81):**

| Код | Имя | Смысл |
|---|---|---|
| 0x20 | EXPIRED | `expires_at ≤ now_rtc` |
| 0x21 | REVOKED_BLACKLIST | `key_id` в local_blacklist |
| 0x22 | REVOKED_FILTER | bloom hit и не в whitelist |

---

## 8. Bloom filter параметры

Рекомендуемые параметры для поддержки до 65 000 отзывов:

- False positive rate p = **0.001** (1 на 1000).
- m_bits / n ≈ **14.4** (≈ 1.8 байта на элемент).
- k_hashes ≈ **10**.
- Whitelist hard cap = **256 записей** (сервер варьирует `hash_seed` до попадания в cap).

**Формула:**
```
m_bits = ceil(-n * ln(p) / (ln 2)^2)
k_hashes = ceil(m_bits / n * ln 2)
```

**Алгоритм выбора hash_seed (на backend):**

```python
for seed in range(0, max_attempts):
    bloom = build_bloom(candidates, m_bits, k_hashes, seed)
    whitelist = [k for k in active_keys if bloom.contains(k)]
    if len(whitelist) <= 256:
        return seed, bloom, whitelist
raise Exception("cannot fit whitelist under cap")
```

---

## 9. Nonce management (ridader)

- Ridader выпускает `fresh_nonce` (32 B, криптостойкий random) в каждом ответе.
- Ring-buffer ёмкостью **8** nonce, TTL **10 секунд** на каждый.
- При использовании в signed-запросе (`used_nonce` поле) — атомарно consume'ится из ring.
- При истечении TTL — удаляется из ring.
- Ring хранится только в RAM, не персистится.

**Используется в:** TIME_SYNC, ACCESS_RESPONSE, REVOKE_KEY.

**Не используется в:** INFO, GET_FILTER_DELIVERY_INFO, GET_BLACKLIST, FILTER_UPDATE.

---

## 10. TIME_SYNC политика применения

```
if statement.used_nonce not in ring_buffer or expired:
    return BAD_NONCE

if grant.expires_at <= now_rtc:
    return NOT_AUTHORIZED

verify grant.server_signature
verify statement.authority_signature via grant.authority_pubkey

if grant.kind == HARD:
    apply unconditionally

elif grant.kind == SOFT:
    if time_sync_state.last_sync_at_local == 0:
        # Первая SOFT-синхронизация: широкое окно, чтобы привести часы
        # свежепровижиненного ридера (DS3231 стартует с ~2001) без HARD-гранта.
        window = TIME_SYNC_BOOTSTRAP_WINDOW_S   # default 86400 s (provisioned per reader)
    else:
        days   = max(1, (now_rtc - last_sync_at_local) // 86400)
        window = TIME_SYNC_DRIFT_S_PER_DAY * days   # default 10 s/day (provisioned per reader)
    
    if abs(now_rtc - statement.new_time) > window:
        return TIME_REGRESSION

# apply
RTC = statement.new_time
time_sync_state.last_sync_at_local = statement.new_time
time_sync_state.last_sync_authority_id = grant.authority_id
time_sync_state.last_sync_kind = grant.kind
return OK
```

---

## 11. Access verification (ridader)

Порядок проверок — дешёвые раньше Ed25519.

```
SKEW = 60 seconds

1. parse request
   if format_version != 1: return BAD_FORMAT
2. if used_nonce not in ring_buffer or expired: return BAD_NONCE
   consume nonce
3. if issued_key.reader_id != self.reader_id: return WRONG_READER
4. if issued_key.expires_at <= now_rtc: return EXPIRED
5. if issued_key.issued_at > now_rtc + SKEW: return BAD_FORMAT
6. key_id = BLAKE2s(...)  # see §5.1
   if key_id in local_blacklist: return REVOKED_BLACKLIST
7. if bloom.contains(key_id):
       if key_id not in whitelist: return REVOKED_FILTER
8. verify issued_key.server_signature via server_ed25519_R_pub
   if invalid: return BAD_SIGNATURE
9. verify phone_signature via issued_key.phone_pubkey
   if invalid: return BAD_SIGNATURE
10. # success
    send VERDICT {result = OK}
    activate lock (GPIO high, LED red→green, timer, then LOW)
```

---

## 12. FILTER_UPDATE verification (ridader)

Streaming handler. Incremental SHA-512 (внутри Ed25519 streaming verify).

```
1. first chunk arrives (≥ 56 B)
2. parse header:
   if format_version != 1: return BAD_FORMAT
   if reader_id != self.reader_id: return WRONG_READER
   if filter_version <= current_filter.version: return STALE
   if generated_at > now_rtc + SKEW: return BAD_FORMAT
   if filter_bytes_len != m_bits / 8: return BAD_FORMAT
   if whitelist_count > 256: return BAD_FORMAT
   if blacklist_delta_count > 256: return BAD_FORMAT
   if filter_bytes_len > MAX_FILTER_BYTES: return BAD_FORMAT
3. erase inactive flash slot, init write position = 0
   init incremental Ed25519 verifier with domain_FLT
4. for each chunk:
   write to flash
   update verifier
5. on LAST:
   finalize verifier
   read last 64 bytes (signature) from flash
   verify via server_ed25519_R_pub
   if invalid:
     erase inactive slot
     return BAD_SIGNATURE
6. apply blacklist_delta:
   for key_id in blacklist_delta:
     if local_blacklist contains key_id:
       remove key_id from local_blacklist
7. atomic swap: switch NVS pointer to new slot
8. update in-RAM filter copy from new slot
9. record filter_delivery_record = {filter_version, courier_id, received_at = now_rtc}
10. build delivery_receipt (sign with reader_ed25519_priv, domain_RCP)
11. return OK with ext = delivery_receipt || next_nonce
```

---

## 13. REVOKE_KEY verification (ridader)

```
1. parse request
   if format_version of either key != 1: return BAD_FORMAT
2. if used_nonce not in ring_buffer or expired: return BAD_NONCE
   consume nonce
3. if requester.reader_id != self: return WRONG_READER
   if target.reader_id != self: return WRONG_READER
4. if requester.permit_id != target.permit_id: return NOT_AUTHORIZED
5. if requester.expires_at <= now_rtc: return EXPIRED
6. requester_key_id = BLAKE2s(...)
   if requester_key_id in local_blacklist: return REVOKED_BLACKLIST
7. if bloom.contains(requester_key_id):
     if requester_key_id not in whitelist: return REVOKED_FILTER
8. verify requester.server_signature: if invalid return BAD_SIGNATURE
9. verify target.server_signature: if invalid return BAD_SIGNATURE
10. verify phone_signature via requester.phone_pubkey: if invalid return BAD_SIGNATURE
11. target_key_id = BLAKE2s(...)
    if target_key_id in local_blacklist: return OK (idempotent)
12. if local_blacklist is full: return NO_SLOT
13. append (target_key_id, revoked_at=now_rtc, expires_at=target.expires_at, reason=BY_USER) to local_blacklist
14. return OK with ext = next_nonce
```

---

## 14. Key status state machine (backend)

```
          issued → active
                    |
        ┌───────────┼───────────┐
        ↓           ↓           ↓
  revoked_by_server │   revoked_by_reader
        \           |           /
         \          |          /
          ↓ committed_filter_version ≤ applied_filter_version ↓
                         ↓
                  revoked_in_bloom

  (any non-expired) -- time passes --> expired
```

**is_active = true** когда `status ∈ {active, revoked_by_server}`.

**Переходы:**

| From | To | Trigger |
|---|---|---|
| - | `active` | выпуск ключа |
| `active` | `revoked_by_server` | `POST /app/keys/{id}/revoke-on-server` или admin action |
| `active` | `revoked_by_reader` | приход BLK с этим key_id |
| `revoked_by_server` | `revoked_by_reader` | приход BLK |
| `revoked_by_server` | `revoked_in_bloom` | delivery_receipt или FDI с `applied_filter_version ≥ committed_filter_version` |
| `revoked_by_reader` | `revoked_in_bloom` | -//- |
| any non-expired | `expired` | `now > expires_at` (periodic task) |

**Инвариант:** `committed_filter_version` устанавливается один раз и более не меняется.

---

## 15. PASSAGE_RECEIPT (учёт проходов / условное посещение)

Подписанная ридером квитанция о факте успешного прохода. Выдаётся только в текущей tap-сессии после успешного `ACCESS` (нет ключа → нет квитанции). Делать что-то с квитанцией — в интересах владельца устройства: пока пакет не доставлен на сервер, на стороне сервера прохода как бы и не было (для учёта посещений, нарядов, табелей).

Тем самым достигается «условный контроль посещаемости»: сервер не знает о факте прохода до тех пор, пока phone не доставит receipt, а ридер не хранит лог сам (он офлайн, и flash экономится).

### 15.1 Жизненный цикл

Receipt **больше не запрашивается отдельной операцией**. Он формируется ридером
синхронно с успешным вердиктом и **дописывается в хвост того же ACCESS_VERDICT**
(§5.4) — одним сообщением, в том же tap-цикле:

```
1. Phone выполняет ACCESS (§5.3/§11).
2. При result == RES_OK ридер формирует passage_receipt (§15.3) и дописывает её
   к 42-байтовому вердикту → ответ 234 B (verdict[0:42] ‖ receipt[42:234]).
   На любом отказе receipt не формируется → ответ 42 B (§5.4).
3. Phone, разобрав ACCESS_VERDICT (0x81) длиной 234 B, извлекает receipt из [42:234]
   и сохраняет в очередь outgoing_reports как type="passage_receipt".
   Каждый успешный ACCESS даёт ровно одну квитанцию (свежий receipt_nonce → уникальна).
4. При следующей синхронизации с сервером phone заливает квитанцию.
5. Сервер верифицирует подпись, проверяет (receipt_nonce, reader_id) на дубль,
   записывает в таблицу passage_events.
```

Нет ACCESS=OK — нет квитанции (отдельного «PASSAGE_NONE» больше не существует: на
отказе ACCESS_VERDICT просто 42 B без хвоста).

### 15.2 Опкоды и маркеры

- Отдельного inner_opcode для receipt **нет** (бывший `0x16` GET_PASSAGE_RECEIPT
  и его state-машина `g_passage_cache`/`op_passage` удалены целиком).
- Receipt едет внутри ACCESS_VERDICT (result_marker `0x81`, §5.4); собственных
  маркеров `0x96`/`0x97` больше нет.
- domain tag: `b"RDR-PSG-v1\x00\x00\x00\x00\x00\x00"` (16 B) — неизменен.

### 15.3 passage_receipt (192 B)

Подписана `reader_ed25519_priv`.

```
Offset  Size  Field
0       1     format_version         (= 0x01)
1       16    reader_id
17      16    receipt_nonce          (cryptographic random — backend dedup key)
33      16    key_id                 (BLAKE2s of issued_key, §5.1)
49      8     passed_at              (uint64 LE — RTC при выдаче receipt)
57      1     direction              (0 = unknown, 1 = entry, 2 = exit; зависит от пина)
58      1     verdict                (= 0x00 OK; зарезервирован для denial-вариантов в v2)
59      4     session_seq            (echo из INFO §5.2)
63      1     flags                  (bit0 = реальный pass, bit1 = тест)
64      32    phone_pubkey           (для backend → device lookup)
96      16    permit_id              (echo из issued_key)
112     8     issued_key_issued_at   (chain-of-custody)
120     4     issued_key_serial
124     4     padding                (zeroed)
128     64    reader_signature       (Ed25519 над domain_PSG || bytes[0:128])
```

Размер: 192 B.

### 15.4 Где едет receipt (хвост ACCESS_VERDICT)

Отдельного envelope больше нет — receipt находится **в ACCESS_VERDICT** (§5.4). На
успехе ответ ровно 234 B:

```
Offset  Size  Field
0       1     result_marker          (= 0x81, MARK_ACCESS_VERDICT)
1       1     result                 (= 0x00 RES_OK)
2       8     reader_time
10      32    next_nonce
42      192   passage_receipt        (§15.3, полный, со своим format_version)
                                      total = 234 B (только при RES_OK)
```

- Подписанная область receipt'а в координатах ответа — `response[42:170]`
  (signed под `DOMAIN_PSG`), подпись — `response[170:234]`. В собственных координатах
  receipt'а это `[0:128]` / `[128:192]` (§15.3) — те же байты.
- На **любом отказе** хвоста нет → ACCESS_VERDICT = 42 B, квитанции не возникает.

### 15.5 Размер вместе с FETCH

234-байтовый ACCESS_VERDICT ≤ MAX_APDU_DATA_SIZE=240 ридера и помещается inline в
один FETCH-ответ без PUSH_CHUNK (`prev_result` отдаётся целиком). Это укладывается в
тот же бюджет, что и прежний 225-B passage envelope, — отдельного round-trip больше
не нужно (receipt приходит вместе с вердиктом).

### 15.6 Verification на сервере

```
1. parse 192 B receipt
   if format_version != 1: reject BAD_FORMAT
2. lookup reader by reader_id
   if not found: reject UNKNOWN_READER
3. verify reader_signature: ed25519_verify(reader.reader_pubkey,
                                            domain_PSG || receipt[0:128],
                                            receipt[128:192])
   if invalid: reject BAD_SIGNATURE
4. sanity: |passed_at - now()| < MAX_PASSAGE_SKEW   (24 h, чтобы старые квитанции тоже принимались — устройство могло долго быть оффлайн)
5. dedup: SELECT 1 FROM passage_events WHERE reader_id=? AND receipt_nonce=?
   if hit: idempotent OK
6. lookup issued_key by key_id (optional — для денормализации в outbox)
7. INSERT passage_events {reader_id, receipt_nonce, key_id, permit_id,
                          phone_pubkey, passed_at, direction, session_seq,
                          delivered_by_user_id=current_user, raw_bytes}
8. return OK
```

### 15.7 Dedup ключ и фрод-сценарии

- **Replay**: `receipt_nonce` уникален → backend дедуплицирует.
- **Подделка**: квитанция подписана `reader_ed_priv` (ключ хранится только в NVS ридера, недоступен ни phone'у, ни серверу). Phone-side подделать невозможно.
- **Сокрытие**: владелец может не доставить квитанцию. Это его право (приватность), но и его проблема (нет учёта). Сервер периодически сверяет offline-устройства с активными permit'ами — недоставка приводит к "missed passage" сигналу в админке (опционально).
- **Чужой телефон делает ACCESS моим ключом**: не сценарий — без `phone_privkey` подписать ACCESS нельзя.

### 15.8 Хранение на сервере

Таблица `passage_events`:

```sql
CREATE TABLE passage_events (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reader_id         BYTEA NOT NULL CHECK (length(reader_id) = 16) REFERENCES readers(reader_id),
    receipt_nonce     BYTEA NOT NULL CHECK (length(receipt_nonce) = 16),
    key_id            BYTEA NOT NULL CHECK (length(key_id) = 16),
    permit_id         UUID NOT NULL,
    user_id           INT REFERENCES users(user_id),     -- resolved via key_id → permit → user
    phone_pubkey      BYTEA NOT NULL CHECK (length(phone_pubkey) = 32),
    passed_at         TIMESTAMPTZ NOT NULL,
    direction         SMALLINT NOT NULL DEFAULT 0,
    session_seq       INTEGER NOT NULL,
    verdict           SMALLINT NOT NULL DEFAULT 0,
    flags             SMALLINT NOT NULL DEFAULT 0,
    delivered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_by      INT REFERENCES users(user_id),
    raw_receipt       BYTEA NOT NULL,
    UNIQUE (reader_id, receipt_nonce)
);
CREATE INDEX idx_passage_user_time ON passage_events(user_id, passed_at DESC);
CREATE INDEX idx_passage_reader_time ON passage_events(reader_id, passed_at DESC);
```

### 15.9 Endpoint

`POST /app/reports/submit` уже принимает массив квитанций. Расширяем `VALID_REPORT_TYPES`:

```
"passage_receipt" → min size 192 B → bind to existing dispatch_report
```

И добавляем admin-endpoint `GET /admin/passages` (см. backend-spec).

---

## 16. BLE-канал: транспорт и роли

Параллельный NFC-канал. **Включается только на mains-powered ридерах** (шлагбаумы, турникеты с постоянным питанием). На батарейных конфигурациях BLE radio убивает автономность.

### 16.1 Принципы

- Reader = BLE peripheral + GATT server. Advertise всегда включён в `ble_enabled` режиме.
- Phone = BLE central. Сканирует, видит активные SCUD-устройства, кликает на одно для активации потока обмена.
- Семантика операций — **идентична NFC** (§3, §5). Меняется только транспорт. Никаких BLE-специфичных опкодов: ACCESS, TIME_SYNC, FDI, REVOKE_KEY, FILTER_UPDATE доступны те же самые. passage_receipt едет в хвосте ACCESS_VERDICT (§5.4), а не отдельной операцией.
- Аутентификация выполняется на уровне приложения (Ed25519 поверх). **BLE-уровень не использует pairing/bonding** — link cleartext, что упрощает UX и не несёт рисков (наша крипта end-to-end).

### 16.2 Advertising payload (legacy 31 B)

```
| AD-flags 3 B | Service UUID 16 B (128-bit) | Manuf data 11 B |
```

- **Flags**: `0x02, 0x01, 0x06` (LE General Discoverable + BR/EDR not supported).
- **Service UUID** (full 128-bit, см. §16.3): `0x07 (len) | 0x06 (type) | <16 B UUID LE>`.
- **Manufacturer Data**: `0x0A (len) | 0xFF (type) | 0xC0DE (mfg id LE) | short_reader_id 6 B | caps 1 B`.
  - `short_reader_id` = первые 6 B от `BLAKE2s(reader_id, 16)`.
  - `caps` (1 B, **транспортные capability-флаги ридера**, item X2):
    - `bit0 BLE_CAP_BULK` (0x01) — ридер принимает bulk-операции по BLE (BLE-канал поднят);
    - `bit1 BLE_CAP_HANDOVER` (0x02) — ридер поддерживает NFC→BLE handover (реализовано, см. §17.1 и §4.3 плана 08);
    - `bit2..7` — reserved (0).

Поле `caps` **не подписано** и **не входит в conformance-корпус** (в отличие от INFO/146 B); это лишь подсказка для роутинга на телефоне. Безопасность держит E2E-крипта в INFO: phone верифицирует `reader_signature` после подключения и не доверяет caps как авторизации.

**Routing policy (item X2, §3.3 плана 08; обновлено унификацией транспорта H1).**
Phone выбирает транспорт по типу пакета, но **граница транспорта — больше не
security-boundary ридера**: ридер принимает все эти операции на обоих каналах
(§16.8). `chooseTransport` остаётся как UX/пропускной хинт (мелкое — по уже
поднятому каналу, bulk — на BLE при наличии caps):

| Класс | Опкоды | Транспорт |
|---|---|---|
| state-changing / телеметрия | FDI(0x11), TIME_SYNC(0x12), REVOKE_KEY(0x15) | NFC **или BLE** — оба разрешены ридером (X3 NFC-only снят). Relay-риск **принят** владельцем; BLE-митигейшн — confirm-session на телефоне (§16.8) |
| ACCESS (открытие двери) | ACCESS(0x01) | NFC **или BLE** — осознанный клик + крипто-привязка к `reader_id`; см. §16.8. Для шлагбаумов (§18) BLE — основной |
| bulk (пакет подписан сервером, версия монотонна — relay-безопасен) | FILTER_UPDATE(0x13), большой GET_BLACKLIST(0x14) | **BLE**, если ридер заявил `BLE_CAP_BULK`; иначе fallback на **NFC** |

passage_receipt не маршрутизируется отдельно — она возвращается в хвосте
ACCESS_VERDICT (§5.4) на том же канале, по которому пришёл ACCESS.

Решение — чистая функция `chooseTransport(inner_opcode, reader_supports_bulk)` на телефоне (firmware/backend её не видят: caps живут только в adv). Фактическое переключение потока (NFC-тап + хендовер на BLE) — отдельный handover-item (§4.3 плана 08), описан в §17.1 (`handover_token`, реализовано compile-only).

Полный `reader_id` (16 B) ридер отдаёт уже в INFO после подключения. Совпадение `short_reader_id` с известным на phone достаточно для отображения display_name.

Local Name **не передаётся** в advertising (нет места) — экономим байты на manuf data. Phone делает scan_response read только при близком rssi.

### 16.3 GATT Service

Service UUID: `5C0DA001-5C0D-4D11-8001-000000000000`
(«5C0D» = «SCUD» в HEX-арте; A001 — application v1.)

| Char | UUID | Properties | Описание |
|---|---|---|---|
| INFO_NOTIFY | `5C0DA001-5C0D-4D11-8001-000000000001` | NOTIFY | ридер push'ит INFO (146 B) **framed-сообщением** (§16.5, тот же фрейминг, что OP_WRITE/RESULT_NOTIFY); при MTU ≥ 155 — один PDU. INFO — unsolicited, **без `op_seq`-префикса** (§16.5.1). |
| OP_WRITE | `5C0DA001-5C0D-4D11-8001-000000000002` | WRITE_NO_RESP | phone заливает операцию (см. §16.5 framing). Сообщение несёт `op_seq`-префикс для корреляции (§16.5.1). |
| RESULT_NOTIFY | `5C0DA001-5C0D-4D11-8001-000000000003` | NOTIFY | ридер push'ит результат (см. §16.5 framing). Несёт эхо `op_seq` соответствующей операции (§16.5.1). |
| CONTROL | `5C0DA001-5C0D-4D11-8001-000000000004` | WRITE | sub-commands: `0x01 RESET`, `0x02 END` (аналог NFC END §4.8). |

CCCD дескрипторы у двух notify-характеристик стандартные.

### 16.4 MTU и chunking

Сразу после connect phone выполняет `exchangeMtu(247)`. ESP32 NimBLE поддерживает MTU до 517 B, но реально стек phone'а часто отдаёт 247.

Полезных байт на ATT PDU: `MTU - 3` (3 байта ATT header). При MTU 247 это 244 B. При 517 — 514 B.

### 16.5 Application framing

GATT-уровень не гарантирует целостность сообщений в смысле «сообщение пришло целиком в один WRITE». На наш уровень кладём явный фрейминг — единый для OP_WRITE и RESULT_NOTIFY:

```
Каждое PDU:
  [seq 1 B] [flags 1 B] [chunk_data N B]
```

- `seq` — монотонный счётчик в рамках сообщения, начиная с 0.
- `flags`:
  - bit0 = LAST (последний чанк сообщения).
  - bit1 = HAS_TOTAL_LEN (первое PDU несёт `uint32 LE total_len` после flags; см. ниже).
- `chunk_data` — фрагмент логического сообщения.

**Layout первого PDU (seq=0):**
```
[0x00] [flags = HAS_TOTAL_LEN] [total_len 4 B LE] [first_chunk_bytes]
```

**Layout последующих:**
```
[seq] [flags = LAST? 0x01 : 0x00] [chunk_bytes]
```

Receiver буферизует до получения PDU с `LAST=1` и `seq == seq_first + total_chunks - 1`. На ошибки seq/total — RESET (отправка `CONTROL=0x01`).

### 16.5.1 Корреляция op↔result (`op_seq`) — B4

PDU-фрейминг (§16.5) гарантирует, что *одно* сообщение собирается целиком, но не
связывает запрос с ответом. Раньше phone сопоставлял result позиционно (ждал
RESULT_NOTIFY сразу после write OP_WRITE) — переупорядочивание или потеря result
рассинхронизировали op↔result. Поэтому **reassembled-сообщение** (то, что получается
ПОСЛЕ снятия PDU-фрейминга) на OP_WRITE и RESULT_NOTIFY несёт 1-байтовый префикс
`op_seq`:

```
OP_WRITE       reassembled message = [op_seq 1 B] [op_bytes ...]
RESULT_NOTIFY  reassembled message = [op_seq 1 B] [result_bytes ...]
```

- `op_seq` — 1 байт, монотонный счётчик phone'а в рамках сессии, оборачивается на
  256. Назначается на каждую новую операцию.
- Ридер запоминает `op_seq` собранной операции, диспатчит op **без** этого префикса
  (`op_bytes` = `inner_opcode`-сообщение, как и раньше), а в RESULT_NOTIFY эхо'ит тот
  же `op_seq` первым байтом сообщения.
- Phone держит `map<op_seq → pending>` и комплитит именно ту операцию, чей `op_seq`
  пришёл в result. Result для неизвестного `op_seq` (поздний/дубль) отбрасывается.
- **INFO_NOTIFY — БЕЗ `op_seq`**: это unsolicited push, а не ответ на op. Идёт по
  отдельному пути (infoChannel на phone), реассемблируется как обычное framed-сообщение.

**Важно:** `op_seq` — *прикладной* префикс ВНУТРИ реассемблированного сообщения; он
лежит НАД PDU-фреймингом `[seq][flags][total_len]` (§16.5). Сам PDU-фрейминг и его
golden-векторы (`ble_framing`) НЕ меняются — фрейминг просто нарезает сообщение,
которое теперь на 1 байт длиннее. `op_seq` не имеет отношения к `msg_id` NFC-пути
(PUSH_CHUNK/FETCH, §4.7): это независимый BLE-транспортный идентификатор.

### 16.6 Сеанс обмена

```
Phone connect → exchangeMtu(247) → enable notify on INFO_NOTIFY, RESULT_NOTIFY
                                ↓
              Reader pushes INFO (146 B → один framed message).
                                ↓
   Phone parses INFO (§5.2). Строит operations queue (как в TapDecisionTree).
                                ↓
   for each op:
       op_seq = next_op_seq++           (1 B, wraps; §16.5.1)
       pending[op_seq] = waiter
       framed write to OP_WRITE         message = [op_seq][op_bytes]
       wait framed notify RESULT_NOTIFY message = [op_seq][result_bytes]
         → match by op_seq, не позиционно: complete pending[op_seq]
       parse result, обновить state (next_nonce и т.п.)
                                ↓
   Phone → CONTROL=0x02 END → reader сбрасывает session.
```

Корреляция по `op_seq` (§16.5.1), а не позиционная: переупорядоченный/потерянный
result не рассинхронизирует обмен.

Idle timeout на ридере — 30 секунд без активности → принудительный disconnect.

### 16.7 PASSAGE_RECEIPT и BLE

Отдельной операции для receipt'а нет (§15) — он приходит **в хвосте ACCESS_VERDICT**
(§5.4). На BLE это значит: phone пишет ACCESS в OP_WRITE, ридер отвечает через
RESULT_NOTIFY сообщением `[op_seq][0x81 …]` длиной 234 B при RES_OK (verdict ‖
receipt) либо 42 B на отказе. Phone извлекает receipt из `[42:234]`, сохраняет в
outgoing_reports так же, как в NFC-сценарии, и доставляет на сервер тем же
эндпоинтом. Reassembly-фрейминг (§16.5) просто переносит на 192 B длиннее ответ —
golden-векторы фрейминга не меняются (один пакет, не несколько).

### 16.8 Безопасность

BLE link **не шифруется** (no pairing/bonding) и имеет дальность ~10 м — это
осознанный выбор: вся безопасность держится на крипто-подписях прикладного слоя,
а не на свойствах радиоканала. Ключевое различие, определяющее всю модель:

**Relay ≠ Replay.**
- **Replay** (повтор старого сообщения) бьётся *nonce*: каждый ACCESS подписан над
  `used_nonce`, выданным ридером в INFO этой сессии. Старый/чужой ACCESS → `BAD_NONCE`.
  handover_token (§17.1) — one-shot (`pending_handover` гасится при первом PRESENT).
- **Relay** (ретрансляция *свежего* валидного сообщения на расстояние — «прокинуть»
  тап телефона жертвы на удалённый ридер) nonce **не** ловит: сообщение настоящее и
  свежее. На NFC (~4 см) relay физически дорог (нужно поднести два активных устройства
  вплотную к жертве и к ридеру одновременно); на BLE (~10 м) — тривиален.

**Принятая модель (унификация транспорта H1).** Раньше спецификация делала
NFC-близость *security-boundary* и запрещала state-changing операции по BLE
(X3-enforcement на ридере). **Этот reader-side boundary снят.** Согласованное
решение: **все операции едут по обоим каналам** (NFC и BLE), а relay/wormhole-риск
BLE **явно принят** владельцем устройства. Единственный BLE-специфичный митигейшн —
**Android per-reader confirm-session** (юзер подтверждает конкретный ридер по имени/
`reader_id` перед обменом). Это **UX-контроль на недоверенном эндпоинте, а не
граница безопасности ридера**: кастомный BLE-клиент может писать в `CHR_OP_WRITE`
напрямую, минуя любой экран подтверждения. Мы фиксируем компромисс честно:

- **Что отдаётся:** ~4 см NFC-проксимити как доказательство присутствия для
  state-changing операций (FDI, TIME_SYNC, REVOKE_KEY) больше **не гарантируется** —
  по BLE их можно ретранслировать с ~10 м (или дальше через активный relay).
- **ACCESS(0x01)** — по NFC и по BLE. Открытие требует осознанного клика и
  крипто-привязано к ридеру: телефон верифицирует **подписанный INFO**
  (`DOMAIN_INF`), узнаёт настоящий `reader_id`, а `phone_signature` считается над
  `reader_id ‖ used_nonce ‖ key_id` (access.cpp) — ACCESS для ридера A **не откроет**
  ридер B. **Handover-гейта на ACCESS нет** — по BLE он открыт (владелец принял
  relay-риск). Для шлагбаумов/турникетов (§18) BLE — основной канал.
- **FDI / TIME_SYNC / REVOKE_KEY** — теперь по NFC **и** по BLE (X3 NFC-only-reject
  снят на ридере). Хендлеры transport-agnostic; результат по BLE байт-идентичен
  NFC-результату (инвариант dispatch_op).
- **Bulk** (FILTER_UPDATE(0x13), большой GET_BLACKLIST(0x14)) — relay-safe by
  construction (пакет подписан сервером, версия монотонна): уходят на BLE при
  `BLE_CAP_BULK`. Опциональный гейт `handover_required` (§17.1) на FILTER_UPDATE
  оставлен **как есть** (soft, default off).
- **passage_receipt** — больше не операция; приходит в хвосте ACCESS_VERDICT (§5.4)
  по тому же каналу, что и ACCESS.

**Что по-прежнему держится на обоих транспортах** (BLE ничего из этого не ослабляет):
- **Нет подделки.** Все операции подписаны Ed25519 (`phone_privkey` / server-signature
  внутри bulk); BLE-клиент без ключа не сфабрикует валидную операцию.
- **Нет cross-reader.** Объект, адресованный другому ридеру, отвергается
  `RES_WRONG_READER` (привязка к `reader_id`).
- **Нет replay.** Per-session `next_nonce`: старый/повторный ACCESS → `BAD_NONCE`.
- **REVOKE ограничен своей permit-группой** держателя — нельзя отозвать чужой доступ.
- **Время нельзя подделать.** TIME_SYNC требует server-grant
  (`time_authority_grant`, `DOMAIN_TGR`); BLE-relay не создаёт новый grant.

То есть relay по BLE может в худшем случае выполнить *настоящую, свежую, подписанную*
операцию держателя «не там, где он стоит» — но не подделать её, не перенаправить на
чужой ридер, не повторить и не превысить права держателя. Этот остаточный риск принят.

**Enforcement (после H1).**
- *Телефон* (`chooseTransport`, X2): остаётся как UX/пропускной хинт (bulk → BLE при
  caps), но **уже не является границей безопасности** — это лишь маршрутизация.
- *Ридер*: BLE-диспетчер **принимает те же операции, что и NFC** (ACCESS, FDI,
  TIME_SYNC, REVOKE_KEY, FILTER_UPDATE, GET_BLACKLIST, HANDOVER_PRESENT). Прежние
  NFC-only-reject'ы для FDI/TIME_SYNC/REVOKE_KEY сняты; единая transport-policy
  живёт в `dispatch_op` (item X1). Per-reader confirm-session — на стороне Android.

**Остальные угрозы BLE-канала:**
- **Eavesdrop**: видны байты operations/results. ACCESS содержит phone_pubkey и
  подпись — пассивно подсмотреть нечего нового (issued_key и так живёт на ридере как ID);
  filter_package — opaque server-signed блоб.
- **MITM / фальш-ридер** с тем же `short_reader_id`: phone верифицирует
  `reader_signature` в INFO через кэш `readers_known[reader_id].reader_pubkey` —
  фальшивка не пройдёт верификацию, и phone не отдаст подписанный ACCESS.
- **Spoofing** service UUID + manufacturer data разрешён, потому что **полный bind** к
  ридеру делается только после крипто-верификации INFO (adv — лишь рандеву-хинт).
- **DoS** (заваливание коннектами / кадрами): single-central (B2) + idle-watchdog (B5) +
  fail-closed reassembly. Доступ при этом не выдаётся — деградация, не компрометация.

### 16.9 Энергопотребление и ограничения

- BLE peripheral mode на ESP32 расходует **~45 mA average** при advertising interval 200 ms. Питание ≥ 250 mA по 5V обязательно.
- Ридер с battery_only conf'ом **отказывается включать BLE** даже по команде provisioning (см. §16.10).

### 16.10 NVS-флаг `ble_enabled`

В NVS namespace `scud_imm` добавляется булевое поле `ble_en`. Включается командой provisioning CLI:
```
ble enable
ble disable
ble status
```

При `ble_en=false` ридер не инициализирует BLE-стек (экономия RAM ~25 KB + flash overlay).

---

## 17. BLE session_token (опционально, для дальних шлагбаумов)

В сценариях, где нужно открыть шлагбаум **до подъезда** (radio range ~10 m, машина едет 30 km/h), используется пред-выпускаемый `ble_session_token`. Phone подписывает обычный ACCESS, получает VERDICT=OK, но дополнительно ридер выдаёт **session_token** — короткоживущую подписанную метку, по предъявлению которой за следующие 60 секунд можно открыть шлагбаум повторно без полного crypto-cycle.

Это **отдельная фича уровня "v1.1"**, ниже только сигнатура. Реализация — позже.

```
session_token (129 B, marker 0x98):
  format_version (1) | reader_id (16) | issued_to_phone_pubkey (32) |
  issued_at (8) | expires_at (8) | reader_signature (64)
```

domain_BLE = `b"RDR-BLE-v1\0\0\0\0\0\0"`.

---

## 17.1. NFC→BLE handover_token (реализовано, compile-only)

Для bulk-доставки (фильтр) на mains+BLE-ридер курьер сначала проходит **физический NFC-tap** (ACCESS), а затем отдаёт фильтр по BLE. Чтобы BLE-поток был авторизован именно этим тапом (анти-relay для bulk-канала), ридер при тапе выдаёт **handover_token** — короткоживущую reader-подписанную метку, привязанную к `tap_nonce` (fresh_nonce, действовавшему в момент тапа). Телефон предъявляет токен по BLE на новом соединении; ридер проверяет подпись + привязку и авторизует FILTER-поток **на этом соединении** (one-shot).

Это часть плана `08_transport_plan.md §4.3` (item T3). Backend **в этом проводе не участвует** (как и в BLE-фрейминге §16.5): провод reader↔phone. Генератор golden-векторов (`docs/test_vectors/generate.py`) — **референсный packer** (как для delivery_receipt/INFO/FDI).

**Опкоды (shared §3.1):**
- `INNER_HANDOVER_ISSUE = 0x17` — **NFC**, phone→reader: «прошёл ACCESS, хочу отдать фильтр по BLE — выдай токен». Payload: `inner_opcode(1) + phone_pubkey(32)` = 33 B. Ридер отвечает 167-байтовым handover_token. Над BLE опкод отвергается (`RES_UNKNOWN_OPCODE`).
- `INNER_HANDOVER_PRESENT = 0x18` — **BLE**, phone→reader: payload `inner_opcode(1) + handover_token(167)` = 168 B. Ридер верифицирует токен и авторизует FILTER-поток. Над NFC опкод отвергается.

**Пакет `handover_token` (167 B, reader-signed, marker 0x99):**
```
offset size field
0      1    format_version = 0x99
1      16   reader_id
17     32   issued_to_phone_pubkey   (phone Ed25519 identity)
49     6    reader_ble_addr          (BLE MAC, к которому подключается телефон)
55     32   tap_nonce                (fresh_nonce, действовавший в момент тапа — привязка к тапу)
87     8    issued_at   (LE uint64, reader clock seconds)
95     8    expires_at  (LE uint64; issued_at + HANDOVER_TOKEN_TTL_S, по умолчанию 60 с)
103    64   reader_signature = sign_reader(DOMAIN_BLE, bytes[0:103])
```
Все многобайтовые целые — little-endian (как session_token §17 и существующие квитанции). Подписываемый диапазон = `bytes[0:103]`. `domain_BLE = b"RDR-BLE-v1\0\0\0\0\0\0"`.

**Правило привязки (verify на PRESENT, fail-closed):**
1. `marker == 0x99` и длина == 167;
2. `reader_signature` над `DOMAIN_BLE || bytes[0:103]` валидна против **собственного** reader-pubkey (токен reader-signed; ридер проверяет свою же подпись — это доказывает целостность/неподделываемость 3-й стороной между двумя радио);
3. `token.tap_nonce == pending_handover.tap_nonce` И `token.phone_pubkey == pending_handover.phone_pubkey` (привязка к тому же тапу и тому же телефону);
4. `reader_now ≤ token.expires_at` И `pending_handover.valid` (свежесть; `rtc_now()==0` → отказ);
5. при успехе — `handover_authorized = true` на этом соединении и `pending_handover` гасится (one-shot, защита от replay).

**Gate (soft, default off):** reader-config флаг `handover_required` (CLI `SET-HANDOVER-REQUIRED 0|1`, NVS `ho_req`, default 0). При `1` И `INNER_FILTER_UPDATE` по BLE — требуется `handover_authorized` на этом соединении, иначе `RES_NOT_AUTHORIZED` (fail-closed). При `0` — поведение прежнее (item X2 BLE-filter не регрессирует). Cap `BLE_CAP_HANDOVER (0x02)` в adv manuf-data (§16.2) объявляет поддержку.

> **Статус:** реализовано во всех трёх impl (firmware op_handover_issue/present + dispatch + gate; Android HandoverToken build/parse/verify + HandoverOrchestrator; golden-вектор `handover_token` в корпусе, проверяется backend/Android/firmware-host). **Host-proven:** байтовый layout + reader-подпись + binding-правило. **Hardware-only (compile-only):** реальный two-radio rendezvous (NFC-issue → BLE-connect к MAC из токена → per-connection authorize), не верифицируется без телефона+ридера.

---

## 18. Архитектура «mains-only» режимов

| Mode | Питание | NFC | BLE | passage_receipt |
|---|---|---|---|---|
| `door_battery` | 2× AA / Li-Ion | yes | **disabled** | yes |
| `door_mains` | 12 V PSU | yes | yes (optional) | yes |
| `gate_mains` | 12 V PSU | yes (low-range tap) | **yes** | yes |
| `barrier_mains` | 12 V PSU | optional | **yes** (primary) | yes |

Provisioning CLI команда `mode set <mode>` пишет в NVS `device_mode`. Firmware при boot читает этот флаг и инициализирует подсистемы соответственно.
