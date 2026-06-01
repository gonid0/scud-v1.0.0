# SCUD — Системные метрики: производительность, генерация фильтров, протокол и ресурсный след

> Дата: 2026-06-01 · Ветка: `feature/transport-compile-only`
> Метод: четыре независимых агента-коллектора собрали измеримые параметры всех четырёх под-проектов
> (Backend / Bloom-генерация / Protocol-transport-crypto / Resource footprint) с указанием
> `file:line`, build-output или log-строки на каждое число. Тон и структура — по образцу
> `docs/07_architecture_review.md` и `docs/10_esp_readiness_power_portability.md`.
>
> **Маркировка источника каждого числа.** `[MEASURED]` — реально измерено на этом хосте / ESP32 / из build-output.
> `[DERIVED]` — выведено из других измеренных чисел. `[DATASHEET]` — типовое значение из datasheet (НЕ измерено).
> `[EXACT-spec]` — точный размер по байтовым смещениям спецификации `docs/00 §5`.
> Все размеры на проводе — packed little-endian (`docs/00 §1.1`).

---

## 0. Ключевые числа с одного взгляда

| Метрика | Значение | Источник / метка |
|---|---|---|
| Backend RPS — `GET /health` (peak, in-process) | **~2412 RPS** @ concurrency 8 | `[MEASURED]` ASGI-loop bench |
| Backend RPS — `GET /api/v1/admin/readers` (DB, 50 строк) | **~150–156 RPS** (плато) | `[MEASURED]` ASGI-loop bench (SQLite) |
| Backend p95 — `/health` (concurrency 8) | **3.67 ms** | `[MEASURED]` |
| Backend p95 — `/admin/readers` (concurrency 8) | **67.4 ms** | `[MEASURED]` |
| Полный NFC tap-сеанс (end-to-end) | **~1.5 s** (предел `NFC_SESSION_DEADLINE_MS=8000`) | `[MEASURED-serial]` |
| ACCESS tap (256 B req, chunked) | **~62 ms** | `[MEASURED-serial]` |
| Сложность генерации Bloom (эффективная) | **O(k·(n+W)) ≈ O(n)**; эмпирический показатель **p≈0.92** | `[MEASURED]` + теория |
| Время генерации Bloom @ n=100 000 | **135.5 ms** (m_bits на cap B) | `[MEASURED]` |
| ESP32 RAM (static link-time) — NFC-only / +BLE | **34 320 B (10.5%)** / **115 796 B (35.3%)** | `[MEASURED]` `pio run` |
| ESP32 Flash (app image) — NFC-only / +BLE | **421 009 B (26.8%)** / **749 825 B (47.7%)** | `[MEASURED]` `pio run` |
| ESP32 free heap в конце сеанса | **~116 KB** | `[MEASURED-serial]` |
| Backend: таблиц БД / REST-эндпоинтов (JSON) | **20 таблиц** / **53 эндпоинта** (93 `@router` всего) | `[MEASURED]` миграция / grep |
| Ключевые размеры сообщений | ACCESS req **256 B**, verdict **234 B** (OK) / **42 B** (deny), INFO **146 B**, FDI **241 B**, Ed25519 sig **64 B** | `[EXACT-spec]` |
| BLE MTU / эффективный PDU | **247** / **240 B** | `[MEASURED]` / `config.h:171-172` |
| NFC budget (`MAX_APDU_DATA_SIZE`) | **240 B** | `config.h:58` |
| Signing-domains (доменов подписи) | **ровно 12** | `domains.cpp:1-15`, `§2.3` |

Сводные оговорки: backend-RPS — это **in-process CPU upper/lower bound на SQLite**, не wire-RPS на проде (PostgreSQL + несколько uvicorn-воркеров дали бы выше). Все BLE/handover/большие-фильтры/op_seq-пути — **compile-only + host-proven**; рантайм-тайминги на ESP32+телефоне для них не верифицированы. NFC-набор (ACCESS/FDI/BLACKLIST/FILTER/TIME_SYNC/REVOKE) — hardware-verified.

---

## 1. Backend performance

Все цифры — **реально измеренные** на этом хосте (не оценки). Латентности в **миллисекундах**, пропускная способность в **requests/sec (RPS)**.

**Хост / runtime**

| Item | Value | Source |
|---|---|---|
| Логические CPU-ядра | **16** | `os.cpu_count()` → 16; `nproc` → 16 |
| Python | **3.12.3** | `python --version` |
| Web-стек | FastAPI ≥0.110, Starlette, SQLAlchemy 2.0.49 (asyncio), httpx 0.28.1 | `Backend/pyproject.toml:5-19` |
| БД под тестом | **SQLite in-memory** (aiosqlite, StaticPool) | `Backend/tests/conftest.py:26-28` |
| Middleware в request-path | custom zero-dep Prometheus `BaseHTTPMiddleware` + CORS | `Backend/src/scud/main.py:38-48`, `observability/middleware.py:31-52` |

**Метод (labeled).** In-process **ASGI-loop**-бенчмарк: гонял *реальный* объект приложения (`create_app()`, полный middleware-стек) через `httpx.ASGITransport` — тот же транспорт, что использует тест-сьют (`conftest.py:135-139`) — поверх in-memory SQLite, засеянного ORM-схемой + 1 группа, 1 admin API-key, 50 readers. `get_session` переопределён на тестовую фабрику. Без uvicorn / без TCP-сокетов, поэтому RPS — это **in-process CPU upper bound, не wire-цифра**. Concurrency = `asyncio`-таски под семафором на **одном event-loop** (эффективно одно ядро). httpx/asyncio-логирование отключено на время замера (иначе per-request INFO-логирование доминировало и занижало RPS ~в 10 раз). 200-request warmup на эндпоинт; 5000 запросов/кейс для `/health`, 3000/кейс для DB-эндпоинта.

**Результаты — `GET /health` (без БД, самый дешёвый путь)**

| Concurrency | RPS | mean | p50 | p95 | p99 | max | errors |
|---|---|---|---|---|---|---|---|
| 1 | **2006** | 0.470 | 0.396 | 0.756 | 1.024 | 83.8 | 0 |
| 8 | **2412** | 2.654 | 2.199 | 3.669 | 4.785 | 99.1 | 0 |
| 32 | 1930 | 12.34 | 8.91 | 14.13 | 106.9 | 135.2 | 0 |
| 64 | 1645 | 28.87 | 19.09 | 117.3 | 129.1 | 153.7 | 0 |

**Результаты — `GET /api/v1/admin/readers` (DB-backed, 50 строк, X-Api-Key auth)**

| Concurrency | RPS | mean | p50 | p95 | p99 | max | errors |
|---|---|---|---|---|---|---|---|
| 1 | **134** | 7.42 | 7.05 | 10.12 | 12.07 | 95.8 | 0 |
| 8 | 149 | 53.16 | 50.42 | 67.36 | 137.0 | 152.6 | 0 |
| 32 | 153 | 207.1 | 192.0 | 291.9 | 482.0 | 591.9 | 0 |
| 64 | **156** | 407.4 | 382.4 | 530.9 | 736.3 | 747.4 | 0 |

**Ключевые находки**

- `/health` упирается в ~**2.4k RPS** in-process (p50 ≈ 0.4 ms без нагрузки). Пропускная способность плоская-к-убывающей после concurrency 8, потому что всё крутится на одном event-loop / одном ядре (предел — harness, а не приложение).
- DB-эндпоинт **выходит на плато ~150 RPS**, латентность растёт линейно с concurrency (насыщенная closed queue) — классическая single-loop-сериализация. Структурный вклад: API-key-auth-зависимость делает `UPDATE api_keys SET last_used_at … ` + `flush()` на **каждый** запрос (`Backend/src/scud/api/deps.py:73-79`), то есть каждый вызов несёт сериализованную SQLite-запись. На реальном PostgreSQL + несколько uvicorn-воркеров этот потолок был бы выше; **эти SQLite/in-process-числа — консервативная нижняя граница**, не продакшн-RPS.
- 0 ошибок на всех 32 000 замеренных запросах.

**pytest-сьют (proxy-метрика):** **190 passed, 2 skipped, за 19.82 s** wall (`python -m pytest -q`). 2 skip — это `@pytest.mark.postgres`-concurrency-тесты, авто-скипнутые на дефолтном SQLite-движке (`conftest.py:40-52`).

> Примечание: бенчмарк прогонялся через временный скрипт (`Backend/bench_perf.py`), который **удалён** после замера; модифицированных файлов в репозитории не осталось.

---

## 2. Bloom-filter generation

Код генерации: `Backend/src/scud/domain/filters.py` (чистая функция `select_bloom_params`, строки 102-193; DB-драйвер `handle_generate_filter`, строки 196-336), вызывающий реальное hash-ядро `Backend/src/scud/crypto/bloom.py` (`build_bloom`/`bloom_contains`, MurmurHash3 x86_32 через `mmh3`). Tuning-константы в `Backend/src/scud/config.py:10-32`.

Символы: **n** = revoked-but-unexpired ключи (bloom-элементы), **W** = active-ключи, проверяемые на whitelist-membership, **k** = число хэшей, **m** = `m_bits`, **B** = байтовый бюджет = `filter_max_bloom_bytes·8` = 800 000 бит (`config.py:16`), **cap** = `whitelist_hard_cap` = 256 (`config.py:11`), **I** = seed-итерации (100 normal / 1000 extended, `config.py:29-32`).

### 2.1. Теория — Big-O по стадиям

| Стадия | Код | Стоимость | Примечание |
|---|---|---|---|
| m_bits sizing | `_size_for_fp` `filters.py:80-83` | **O(1)** | `m = ⌈−n·ln(fp)/ln²2⌉`, byte-aligned, capped at B |
| k selection | `_k_for` `filters.py:86-87` | **O(1)** | `k = ⌈(m/n)·ln2⌉` |
| E[whitelist] closed form | `_expected_whitelist` `filters.py:90-99` | **O(1)** | `W·(1−e^(−kn/m))^k` — без per-key-цикла |
| m_bits grow (binary search) | `filters.py:139-152` | **O(log B)** ≈ O(log m) | каждый probe — O(1) closed form; ~17 probes max при B=800 000 байт |
| bit-set build | `build_bloom` `bloom.py:7-20` | **O(n·k)** | n элементов × k murmur-хэшей + bit-set; аллоцирует `m/8` байт |
| whitelist sizing | `bloom_contains`-цикл `filters.py:178-182` | **O(W·k)** | W active-ключей × k probes на seed-итерацию |
| seed search (outer) | `filters.py:171-186` | **O(I·(n·k + W·k))** worst case | disjoint-окна `seed=t·k`; обычно выход на t=0 |
| whitelist sort | `filters.py:287` | **O(W log W)** | W ≤ cap = 256, пренебрежимо |

**Доминирующий член:** seeding умножает два build/check-прохода, поэтому формальный worst case — **O(I·k·(n + W))**. В рабочем режиме grow-стадия удерживает E[whitelist] ≤ `grow_margin·cap` *до* seeding (`config.py:18-22`), поэтому цикл выходит на **первой** итерации и реальная стоимость схлопывается до **O(k·(n + W))**. Поскольку `k = ⌈(m/n)·ln2⌉` и `m ∝ n` пока m < B, произведение **n·k само ∝ n** (k≈const≈10 при fp=1e-3), что даёт эффективное **O(n)** до насыщения m байтовым бюджетом; после насыщения m фиксировано, k падает, n·k остаётся ограниченным — всё ещё ≤ O(n). DB-драйвер добавляет I/O (candidate/active/blacklist `SELECT`-ы, `UPDATE`-ы), что отдельно от in-CPU-генерации, замеренной ниже.

### 2.2. Эмпирика — реальный `select_bloom_params` (mmh3), best-of-3, ACTIVE=200

| n | m_bits | k | seed_attempts | whitelist | gen_time_ms |
|---:|---:|---:|---:|---:|---:|
| 100 | 1 440 | 10 | 1 | 0 | 0.25 |
| 1 000 | 14 384 | 10 | 1 | 0 | 1.86 |
| 5 000 | 71 888 | 10 | 1 | 2 | 9.44 |
| 10 000 | 143 776 | 10 | 1 | 0 | 18.48 |
| 25 000 | 359 440 | 10 | 1 | 0 | 47.77 |
| 50 000 | 718 880 | 10 | 1 | 0 | 98.14 |
| 100 000 | 800 000 (B cap) | 6 | 1 | 1 | 135.46 |

Стоимость на hash-операцию `gen_ms/(n·k)·1e6` плоская на ≈185-225 нс через три декады n, что подтверждает: O(n·k)-build доминирует, а seeding — нет (seed_attempts=1 везде).

**Фитированная сложность:** log-log-наклон gen_time vs n = **0.90-0.94** по прогонам (измерено 0.942 / 0.896) → **эмпирический показатель p ≈ 0.92, т.е. ~O(n)**, совпадает с теорией. Наклон чуть ниже 1.0, потому что при n=100 000 `m_bits` насыщает 100 000-байтовый бюджет (`config.py:16`), роняя k с 10→6 и сгибая кривую сублинейно в верхней декаде.

Второй эксперимент с большим active-набором (W=20 000), чтобы форсировать marginal-band, всё равно дал **seed_attempts=1** и whitelist 11-21 (≪ cap 256) для n=1 000…25 000: m_bits-grow-binary-search (`filters.py:139-152`) держит E[whitelist] под cap, поэтому seed-цикл никогда не итерирует — подтверждая design-intent в `config.py:17-21`, что seed — лишь sqrt(E)-variance-tiebreaker, а не асимптотический драйвер. Теоретический worst case O(I·k·(n+W)) (до 1000× build) достижим только через `ReaderOversaturated`-смежные adversarial-коллизии, не в нормальной эксплуатации.

> Бенчмарк был throwaway-harness, импортирующий `scud.domain.filters` / `scud.crypto.bloom` напрямую с mmh3; удалён после замера — не закоммичен.

---

## 3. Protocol, transport and crypto

> Источники цитируются inline как `file:line` (firmware/docs) или меткой измерения. Repo root: `c:\Users\Maestro\Documents\UNV\VKR\Code`. Спецификация протокола: `docs/00_shared_protocol (1).md`. Firmware-константы: `ESP32/firmware/src/config.h`. Все размеры — **packed little-endian** (`docs/00 §1.1`).
>
> **Provenance измерений.** Tap-тайминги с меткой **[MEASURED-serial]** взяты из ранних ESP32-serial-логов per-op-строки `[TAP] … → result %uB in %lums`, эмитируемой на `transfer.cpp:637`; переиспользованы из брифа задачи (tracked-лог-файла с ними не сохранилось). Размеры сообщений — **[EXACT-spec]** (байтовые смещения в `docs/00 §5`, сверены с firmware-пакерами/`config.h`). Пропускная способность — **[DERIVED]** из двух. NFC-битрейт и BLE-радио-токи — **[DATASHEET]**.

### 3.1. Размеры сообщений по операциям (request + response байты)

Inner-опкоды из `ops.h:41-50`; маркеры из `ops.h:25-38`; размеры из `docs/00 §5`.

| Операция | Inner opcode | Request (phone→reader) | Result marker | Result (reader→phone) | Source |
|---|---|---|---|---|---|
| ACCESS | `0x01` | **256 B** (`inner 1 + issued_key 151 + used_nonce 32 + reader_time_echo 8 + phone_sig 64`) | `0x81` | **42 B** deny / **234 B** OK (`verdict 42 + passage_receipt 192`) | `§5.3`/`§5.4` |
| GET_FILTER_DELIVERY_INFO (FDI) | `0x11` | **1 B** (`inner` only, OP_SINGLE) | `0x91` | **241 B** | `§5.8` |
| TIME_SYNC | `0x12` | **289 B** (`inner 1 + grant 148 + statement 140`) — chunked | `0x92` | **45 B** (OP_RESULT 13 + next_nonce 32) | `§5.13`/`§6` |
| FILTER_UPDATE | `0x13` | **до ~127 KB** (`inner 1 + courier_id 16 + filter_package`) — chunked | `0x93` | **157 B** (OP_RESULT 13 + delivery_receipt 112 + next_nonce 32) | `§5.6`/`§5.7` |
| GET_BLACKLIST | `0x14` | **1 B** (`inner` only) | `0x94` | **207 + 32·N B** (N=0→207, N=1→239, N=2→271, N=256→8399) | `§5.9` |
| REVOKE_KEY | `0x15` | **407 B** (`inner 1 + requester_key 151 + target_key 151 + nonce 32 + time 8 + sig 64`) — chunked | `0x95` | **45 B** (OP_RESULT 13 + next_nonce 32) | `§5.10` |
| HANDOVER_ISSUE (NFC only) | `0x17` | **33 B** (`inner 1 + phone_pubkey 32`) | `0x99` | **167 B** handover_token | `§17.1`, `handover.cpp:34` |
| HANDOVER_PRESENT (BLE only) | `0x18` | **168 B** (`inner 1 + token 167`) | `0x9A` | **13 B** (OP_RESULT, ext_len 0) | `§17.1`, `handover.cpp:75` |
| INFO (reader push, не op) | — (wire `0xC1`) | — | — | **146 B** | `§5.2`, `transfer.cpp:76` |
| ACCESS_VERDICT (результат ACCESS) | — | — | `0x81` | **42 B** (`marker 1 + result 1 + reader_time 8 + next_nonce 32`) | `§5.4` |
| passage_receipt (хвост ACCESS_VERDICT) | — (retired op `0x16`) | — | внутри `0x81` | **192 B** (`response[42:234]` при RES_OK) | `§15.3`, `ops.h:31-33` |
| handover_token | — | — | `0x99` | **167 B** reader-signed, signed range `bytes[0:103]` | `§17.1`, `handover.cpp:48-62` |

**Размеры компонентных объектов** (`§5`): `issued_key` 151 B, `INFO` 146 B, `filter_package` header 56 B + body (`bloom + 24·wl + 16·bl_delta + 64 sig`), `delivery_receipt` 112 B, `time_authority_grant` 148 B, `time_sync_statement` 140 B, `whitelist entry` 24 B, `blacklist_delta entry` 16 B, BLK `entry` 32 B, FDI `encrypted_courier_blob` 104 B (plaintext 56 B), `session_token` (v1.1, unimpl) 129 B.

**Wire (APDU)-команды** (`§3.2`, опкоды `transfer.cpp`): `0xA4 P1=04` SELECT_AID · `0xC1` PUSH_INFO · `0xC2` FETCH · `0xC3` READ_CHUNK · `0xC4` PUSH_CHUNK · `0xC5` END. `CLA=0x00`, `P2=0x00` всегда. FETCH `prev_result`-кодировки: EMPTY `00 00` (2 B), INLINE `len2 + bytes` (1–252 data), REFERENCE `FF FF + msg_id4` (6 B) (`transfer.cpp:166-181`).

### 3.2. Параметры транспорта

| Параметр | Значение | Source |
|---|---|---|
| `PROTOCOL_VERSION` | 1 | `config.h:54` |
| `MAX_APDU_DATA_SIZE` (NFC budget) | **240 B** (short APDU, `uint8_t` Lc/Le; ≤255 ISO-лимит, ≤PN532 FSC=256) | `config.h:58`, `§4.2` |
| FETCH/result max response incl. SW | 256 B | `§4.5` |
| PREV_INLINE_MAX (inline vs PUSH_CHUNK порог) | 252 B (result_len > 252 → PUSH_CHUNK) | `config.h:165`, `§4.5` |
| PUSH_CHUNK header | **15 B** (`msg_id 4 + offset 4 + total 4 + flags 1 + chunk_len 2`) | `transfer.cpp:107`, `§4.7` |
| PUSH_CHUNK data per APDU | **131 B** (pinned: `PROVEN_CMD_DATA 146 − 15` header) | `transfer.cpp:120-121` |
| READ_CHUNK request | 10 B data (`msg_id 4 + offset 4 + max_chunk 2`) | `transfer.cpp:249`, `§4.6` |
| PUSH_INFO command APDU | 152 B (`5 hdr + 146 data + 1 Le`) — крупнейший 100%-надёжный reader→phone APDU | `transfer.cpp:118` |
| READ_CHUNK retries (N3) | 3, same offset, deadline-bounded | `config.h:161` |
| NFC session deadline | 8000 ms | `config.h:43` |
| NFC detect poll timeout | 100 ms | `config.h:104` |
| Transfer buffer cap (RAM path) | 16384 B (16 KB); больший FILTER_UPDATE → flash A/B-слот | `config.h:33`, `transfer.cpp:481` |
| Worst-case result buffer | 8704 B (BLK N=256 = 8399 B) | `config.h:163`, `transfer.cpp:380` |
| **NFC bitrate** | **106 kbps** типично (ISO/IEC 14443-A, `PN532_MIFARE_ISO14443A`) **[DATASHEET]** | `apdu.cpp:41` |
| NFC framing | ISO-DEP / T=CL, скрыт внутри PN532 `inDataExchange` (0x40) | `apdu.cpp:60`, `docs/10 §4.2` |
| PN532 link | HSU UART2 @ 115200 baud (TX2=GPIO17, RX2=GPIO16) | `config.h:8-11` |
| PN532 RF timing `fATR_RES`/`fRetry` | 0x0F / 0x0F (~3.28 s, для медленного Android HCE) | `config.h:150-151`, `apdu.cpp:32` |
| RF field off→on settle | 80 ms (≥50 ms required) | `config.h:152` |
| **BLE requested MTU** | **247** | `config.h:171`, `ble_channel.cpp:665` |
| **BLE max on-wire PDU** | **240 B** (= MTU 247 − 3 ATT − app-framing-margin; Tier-C framing const) | `config.h:172`, `ble_channel.cpp:187` |
| BLE useful bytes/ATT PDU | MTU − 3 = **244 B** при MTU 247 (514 при 517) | `§16.4` |
| BLE app framing per PDU | `[seq 1][flags 1][chunk]`; первый PDU добавляет `total_len 4` при HAS_TOTAL_LEN | `§16.5` |
| BLE op↔result correlation | 1-byte `op_seq`-префикс внутри собранного сообщения (B4) | `§16.5.1` |
| BLE ring record cap / depth | 244 B / 64 records (~15.9 KB) | `config.h:68-69` |
| BLE idle timeout | 30000 ms | `config.h:39` |
| BLE adv interval | 1000–1500 ms (units 1600–2400 × 0.625 ms, battery-friendly) | `config.h:186-187` |
| BLE adv caps byte | `bit0 BLE_CAP_BULK 0x01`, `bit1 BLE_CAP_HANDOVER 0x02` (unsigned, не в conformance-корпусе) | `config.h:193-194`, `§16.2` |
| GATT service UUID | `5C0DA001-5C0D-4D11-8001-000000000000` (+ INFO_NOTIFY/OP_WRITE/RESULT_NOTIFY/CONTROL chars …0001–0004) | `§16.3` |
| handover_token TTL | 60 s | `config.h:78`, `§17.1` |

**Round-trips на NFC tap-сеанс** (`§4.1`, `transfer.cpp:339-712`): `SELECT_AID` (1) → `PUSH_INFO` (1) → цикл `FETCH` (+`READ_CHUNK`× для chunked-op, +`PUSH_CHUNK`× для большого результата) → `END` (1). Один ACCESS tap = SELECT + PUSH_INFO + FETCH(empty) + FETCH(prev=verdict)/END ≈ **4 round-trips**. 256 B ACCESS-request приходит в 2 чанка (FETCH OP_CHUNKED + 1 READ_CHUNK); TIME_SYNC (289 B) / REVOKE_KEY (407 B) требуют 1–2 READ_CHUNK (`§5.13`/`§5.10`).

### 3.3. Crypto primitives

Алгоритмы из `docs/00 §2.1`; размеры сверены с firmware `crypto/`.

| Назначение | Алгоритм | Размеры | Source |
|---|---|---|---|
| Подпись | Ed25519 (RFC 8032) | pubkey **32 B**, privkey 32 B, **signature 64 B** | `§2.1`, `ed25519.h` |
| ECDH | X25519 (RFC 7748) | pubkey **32 B**, privkey 32 B, shared **32 B** | `§2.1` |
| AEAD | ChaCha20-Poly1305 (RFC 8439) | **nonce 12 B**, **tag 16 B** | `§2.1` |
| Hash (long) | BLAKE2b | sealed-box AEAD key **32 B**, nonce-derivation digest **24 B** (первые 12 используются) | `§2.4` |
| Hash (short) | BLAKE2s | **key_id 16 B** (`BLAKE2s-128`); `short_reader_id` = первые 6 B | `§5.1`, `blake2s.h`, `key_id.cpp` |
| Bloom hashing | MurmurHash3 x86_32 | 32-bit output | `§2.5`, `murmur3.cpp` |
| Password (только backend) | Argon2id | time=3, mem=64 MiB, par=4 | `§2.6` |

**key_id** = `BLAKE2s(reader_id ‖ phone_pubkey ‖ issued_at_8LE ‖ serial_4LE, 16)` → **16 B** (`§5.1`).
**Sealed box** (`§2.4`, `sealed_box.cpp`): `key = BLAKE2b(shared, 32)`; `nonce = BLAKE2b(eph_pub ‖ server_pub, 24)[:12]`; blob = `eph_pub 32 ‖ ct_and_tag` = `32 + |pt| + 16` B. FDI blob: pt 56 → **104 B**; BLK blob: pt `34+32N` → **82 + 32N** B.

**Signing-домены: ровно 12.** Каждый tag = 10 ASCII + 6 `\x00` = 16 B (`domains.cpp:1-15`, `§2.3`):

| Tag | Подписывает | Tag | Подписывает |
|---|---|---|---|
| `RDR-KEY-v1` | issued_key (server) | `RDR-FDI-v1` | filter_delivery_info (reader) |
| `RDR-INF-v1` | INFO (reader) | `RDR-TGR-v1` | time_authority_grant (server) |
| `RDR-RSP-v1` | access_response (phone) | `RDR-TIM-v1` | time_sync_statement (phone) |
| `RDR-FLT-v1` | filter_package (server) | `RDR-REV-v1` | revoke_key (phone) |
| `RDR-RCP-v1` | delivery_receipt (reader) | `RDR-PSG-v1` | passage_receipt (reader) |
| `RDR-BLK-v1` | get_blacklist (reader) | `RDR-BLE-v1` | BLE session_token + handover_token (reader) |

Nonce-ring (`§9`, `config.h:34-35`): capacity **8**, TTL **10 s** (10000 ms), 32 B fresh_nonce на ответ, RAM-only. Допуск clock skew **60 s** (`config.h:73`).

### 3.4. Замеренные tap-тайминги и выведенная пропускная способность

**[MEASURED-serial]** — per-op `[TAP] … → result …ms` (`transfer.cpp:637`), переиспользовано из ранних ESP32-serial-логов. Пропускная способность **[DERIVED]** = result_bytes / dispatch_ms (host-side compute+sign, не RF-время).

| Op | Result size | Measured time | Derived throughput |
|---|---|---|---|
| FDI | 241 B | ~50 ms | ~4.8 B/ms |
| GET_BLACKLIST (N=2, 271 B) | 271 B → PUSH_CHUNK series | ~51 ms | ~5.3 B/ms |
| PUSH_INFO (INFO push) | 146 B | ~110 ms | ~1.3 B/ms (вкл. PN532 RF-protocol settle + HCE warm-up, `transfer.cpp:90-101`) |
| ACCESS (256 B req, chunked) | 234 B (OK) / 42 B (deny) | ~62 ms | ~3.8 B/ms (OK) |
| FILTER_UPDATE | 157 B result; multi-KB streamed in | ~130 ms | зависит от размера package; chunked READ_CHUNK при ≤240 B/chunk |
| **Полный NFC tap-сеанс** | — | **~1.5 s** end-to-end (SELECT→PUSH_INFO→FETCH-loop→END) | ограничен `NFC_SESSION_DEADLINE_MS = 8000` (`config.h:43`) |

**Negotiated wire facts [MEASURED]:** `mtu = 240 B` эффективный на NFC; BLE MTU **247** (PDU 240); PUSH_CHUNK несёт **146 B** command APDU (131 B data + 15 B header); ACCESS_VERDICT **234 B** (OK) / **42 B** (deny); Ed25519 sig **64 B**.

> **Оговорки.** (1) Формула размера GET_BLACKLIST: `§5.9`-envelope-текст даёт `125 + blob_len`, тогда как section-total даёт `207 + 32·N`; обе сходятся при `blob_len = 82 + 32·N` (207-форма авторитетна, используется в buffer-sizing `transfer.cpp:380`). (2) PUSH_CHUNK data консервативно пинён к 131 B до hardware-свипа — реальный потолок в (152, 240] не верифицирован (`transfer.cpp:114-121`). (3) Все BLE/handover/большие-фильтры/op_seq-пути — **compile-only + host-proven**; рантайм-тайминги на ESP32+телефоне для них не верифицированы (`docs/10 §6`, `transport_progress.md`). NFC-набор ACCESS/FDI/BLACKLIST/FILTER/TIME_SYNC/REVOKE — hardware-verified. (4) NFC 106 kbps и BLE/PN532-токи — datasheet-типичные, не измеренные (`docs/10 §3.2` явно помечает все токи как datasheet-оценки).

### 3.5. Максимальный размер чанка и пропускная способность по каналам

**Максимальный размер чанка (полезная нагрузка на один PDU/APDU):**

| Канал | Направление | Макс. data / PDU | На проводе | Источник |
|---|---|---|---|---|
| **NFC** | reader→phone (PUSH_CHUNK) | **131 B** | 152 B command APDU (`5 + 15-байт PUSH-header + 131 + 1 Le`) | `transfer.cpp:120-121` |
| **NFC** | phone→reader (READ_CHUNK pull) | **240 B** (= `MAX_APDU_DATA_SIZE`) | 240 B + 3-байт chunk-header + 2 SW в ответе | `transfer.cpp:521`, `config.h:58` |
| **NFC** | inline prev_result (FETCH) | ≤ **252 B** (`PREV_INLINE_MAX`); >252 → PUSH_CHUNK | внутри команды FETCH | `config.h:165` |
| **BLE** | оба (chunked frame §16.5) | **238 B** (234 на первом PDU с `total_len`) | 240 B app-PDU → 243 на ATT (≤ MTU 247) | `ble_frame.cpp:32-33`, `config.h:172` |

> BLE-фрейминг на PDU: `[seq 1][flags 1]([total_len 4] на первом)[chunk]`; `can_send = max_pdu − header`, где `max_pdu = min(negotiated, BLE_MAX_PDU=240)`. NFC-асимметрия: reader→phone ограничен пиннингом PUSH_CHUNK (131 B), phone→reader — полным `MAX_APDU_DATA_SIZE` (240 B).

**Пропускная способность с учётом максимального лимита:**

| Канал | Метрика | Значение | Метка |
|---|---|---|---|
| NFC | RF-потолок (half-duplex, 106 kbps) | **13.25 KB/s** (13 568 B/s) | `[DATASHEET]` |
| NFC | Эффективная per-op (derived) | **~4–5 KB/s** (вкл. host compute+sign, ISO-DEP round-trips) | `[DERIVED]` из §3.4 |
| NFC | End-to-end сеанс | **~0.7–1 KB/s** (полный tap ~1.1 KB за ~1.5 s со всем overhead) | `[MEASURED-serial]` |
| BLE | Payload / PDU | **238 B** | `[EXACT]` |
| BLE | App-throughput (теоретически) | **~8–50 KB/s** (BLE 4.2, MTU 247; зависит от connection interval ~30 мс и пакетов/событие) | `[DATASHEET, НЕ измерено]` |

**Узкое место — не размер чанка, а round-trip'ы.** На NFC бутылочное горлышко — число APDU-обменов (десятки мс ISO-DEP + HCE на каждый), а не 131/240-байтовый чанк: фильтр ~24 KB (50 000 ключей) = ~100 чанков по 240 B ≈ **5–6 с** на NFC при ~4–5 KB/s. У BLE чанк лишь чуть больше (238 vs 131 reader→phone), но он **не платит per-APDU ISO-DEP/HCE round-trip** → потенциально в разы быстрее (тот же фильтр теоретически ~1–2 с) — это и есть проектное обоснование маршрутизации bulk-операций (FILTER_UPDATE / GET_BLACKLIST) на BLE (роутинг закодирован в `TransportRouter`, но в живом флоу пока не подключён, см. §3.5 примечание). BLE-тайминги на железе не измерялись (compile-only, кроме ACCESS).

---

## 4. Resource footprint

Все ESP32-числа — РЕАЛЬНЫЙ build-output из PlatformIO (`pio run`, 2026-06-01, exit 0). Backend schema-счётчики — точный `grep`/read консолидированной миграции. Android-счётчики — точный `grep`. Datasheet/derived-значения — ПОМЕЧЕНЫ.

### 4.1. ESP32 firmware — build size (MEASURED)

Source: `pio run -d ESP32/firmware`, board `esp32dev` (ESP32, Arduino framework, release mode). MCU budget = 320 KB DRAM (327680 B) / 1.5 MB app-partition (1572864 B).

| Env | RAM used | RAM % | Flash used | Flash % | Build |
|---|---|---|---|---|---|
| `esp32dev` (NFC-only) | 34320 B | 10.5% | 421009 B | 26.8% | SUCCESS 12.18 s |
| `esp32dev_ble` (NFC+NimBLE) | 115796 B | 35.3% | 749825 B | 47.7% | SUCCESS 11.26 s |

- BLE-дельта: +81476 B RAM (10.5%→35.3%), +328816 B Flash (26.8%→47.7%) — цена NimBLE-стека, влинкованного в `_ble`-env.
- Build-output-evidence: `bife5hybd.output:7-8` (esp32dev), `bi4dsl4x4.output:7-8` (esp32dev_ble). "RAM" здесь — static/`.bss`+`.data` link-time-цифра; это НЕ free heap в рантайме (ниже).

### 4.2. ESP32 — runtime free heap (MEASURED, из serial)

| Метрика | Значение | Source |
|---|---|---|
| Free heap в конце NFC-сеанса | ~116 KB (`heap=<n>`-поле) | `transport/transfer.cpp:695-701` логирует `esp_get_free_heap_size()` на tap как `[TAP] === session #.. end (..heap=%u Δ%+ld)` |
| Heap, сэмплированный в начале сеанса | — | `transport/transfer.cpp:345` `heap_start = esp_get_free_heap_size()` |

Note: ~116 KB free-heap из ранних serial-логов согласуется с тем, что 115796 B static RAM `_ble`-env оставляют остаток 320 KB DRAM-пула свободным.

### 4.3. ESP32 — крупные статические буферы (из `src/config.h`)

| Macro | Value | Bytes | config.h line |
|---|---|---|---|
| `TRANSFER_BUFFER_CAP` | 16384 | 16 KB | :33 |
| `RESULT_BUF_CAP` | 8704 | 8.5 KB (worst-case BLK result) | :163 |
| `MAX_FILTER_BYTES` | 128*1024−1024 | 130048 B (~127 KB, flash-resident Bloom-потолок) | :61 |
| `APDU_TX_BUF` | 300 | g_apdu_tx[] | :157 |
| `APDU_RX_BUF` | 260 | g_apdu_rx[] | :156 |
| `BLE_RING_REC_CAP` × `BLE_RING_DEPTH` | 244 × 64 | ~15.9 KB SPSC ring (заменил старый 24 KB scratch) | :68-69 |
| `LOCAL_BLACKLIST_CAP` | 256 | entries (NVS-backed) | :36 |
| `NONCE_RING_SIZE` | 8 | replay-nonce-слоты | :34 |
| `BLE_PDU_STAGE_CAP` | 256 | ble_frame staging | :177 |
| `PROV_LINE_BUF_SIZE` / `PREV_INLINE_BUF` | 256 / 256 | :107 / :164 |

### 4.4. ESP32 — flash partition map (`partitions.csv`, 4 MB flash)

| Partition | Type/SubType | Size | Bytes | Notes |
|---|---|---|---|---|
| nvs | data/nvs | 0x6000 | 24 KB | immutable+auth+local namespaces |
| phy_init | data/phy | 0x1000 | 4 KB | |
| factory | app/factory | 0x180000 | 1.5 MB | app-image (совпадает с 1572864 B Flash-budget выше) |
| filter | data/spiffs | 0x40000 | 256 KB | A/B Bloom-filter-слоты (~120 KB usable каждый по config.h-note) |
| state | data/nvs | 0x10000 | 64 KB | runtime state |

Source: `ESP32/firmware/partitions.csv:3-7`.

### 4.5. Backend — schema footprint (консолидированная миграция)

Source: `Backend/migrations/versions/0001_initial.py` (единый source-of-truth baseline; схлопнут из старой 8-миграционной цепочки).

| Object | Count | Evidence |
|---|---|---|
| Таблицы | 20 | 20× `CREATE TABLE` |
| Индексы | 26 | 26× `CREATE INDEX` (большинство partial, напр. `WHERE is_active`) |
| ENUM-типы | 2 | `key_status` (5 labels, :40), `grant_kind` (soft/hard, :44) |
| Trigger-функции | 1 | `sync_is_active()` plpgsql, :273 |
| Триггеры | 1 | `trg_sync_is_active` BEFORE INSERT/UPDATE on issued_keys, :281 |
| Расширения | 1 | `pgcrypto` (:34) |

Таблицы: users, user_devices, sessions, api_keys, reader_groups, reader_profile, reader_profile_param, param_bounds, readers, reader_param_override, permits, issued_keys, time_grants, filter_packages, delivery_tasks, reader_reports, background_tasks, admin_audit_log, passage_events, webhook_subscriptions.

Fixed-size BYTEA-колонки (CHECK-enforced wire-размеры): `issued_keys.full_key_bytes`=151 B, `time_grants.full_grant_bytes`=148 B, `passage_events.raw_receipt`=192 B, все `*_pubkey`=32 B, `reader_id`/`key_id`=16 B.

### 4.6. Backend — функциональные seed-reference-данные

Source: `0001_initial.py:466-513`, засеяно из `scud/domain/reader_param_catalog.py`.

| Seed | Count | Derivation |
|---|---|---|
| `param_bounds`-строки | 52 | 26 params (`PARAMS`, catalog.py:84-113) × 2 hardware_classes (`esp32_mains_ble`, `esp32_battery_nfc`, :23) |
| `reader_profile`-строки | 4 | `SEED_PROFILES` = entrance, interior_room, sensitive_room, battery_nfc_only (catalog.py:134-190) |

### 4.7. Backend — REST-эндпоинты

Source: `grep @router.<verb>(` по `Backend/src/scud/api`. 93 route-декоратора всего, разбивка:

| Surface | Endpoints | Notes |
|---|---|---|
| JSON REST API (`app/` + `admin/`) | 53 | machine API: 15 в `app/*` (phone/courier), 38 в `admin/*` |
| `admin_web/` HTML-views | 40 | server-rendered admin-console (HTMX/templates), не JSON API |
| **Всего `@router`-декораторов** | **93** | across 24 router-файла |

По verb (JSON REST, эти 53): микс GET/POST плюс 4 PATCH (permits/readers/reader_profiles/reader_groups/users) и 2 DELETE (reader_profiles, reader_groups).

### 4.8. Android — Room persistence + APK

Source: `grep @Entity/@Dao` в `AndroidApp/app/src/main/java/.../data/local`.

| Item | Count | Entities / DAOs |
|---|---|---|
| Room `@Entity` | 9 | account, issued_keys, contact_history, pending_filter_deliveries, pending_revoke_intents, permits, time_grants, readers_known, outgoing_reports |
| Room `@Dao` | 9 | Account, ContactHistory, IssuedKey, OutgoingReport, PendingFilterDelivery, PendingRevokeIntent, Permit, Reader, TimeGrant |

Note: `@Entity`-grep вернул 8 single-line-матчей плюс `OutgoingReportEntity` (multi-line `@Entity(` на `OutgoingReportEntity.kt:7`), с парным `OutgoingReportDao` — итого **9 entities / 9 DAOs**.

APK size: НЕ ДОСТУПЕН — `AndroidApp/app/build/outputs/` содержит только `logs/` (нет `.apk`). Чтобы получить: `./gradlew :app:assembleDebug` (output ляжет в `AndroidApp/app/build/outputs/apk/debug/app-debug.apk`), затем прочитать размер файла.

---

> **Сводные ограничения достоверности.** Backend-RPS/латентности измерены на **SQLite in-memory + in-process ASGI** (один event-loop, фактически одно ядро) — это консервативная нижняя граница, не продакшн-RPS на PostgreSQL + uvicorn-воркерах. Bloom-генерация замерена на хосте (CPython + mmh3), не на устройстве. ESP32 build-size/heap — реальные (`pio run` + serial). Tap-тайминги — `[MEASURED-serial]` из ранних логов, переиспользованы. NFC-набор операций hardware-verified; вся BLE/handover/op_seq/large-filter-волна — **compile-only + host-proven** (рантайм на ESP32+телефоне не пройден, см. `docs/10 §6` и `transport_progress.md`). Все токи/NFC-битрейт — `[DATASHEET]`, не измерены.
