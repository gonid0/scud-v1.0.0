# Трекер ремедиации транспорта (ветка `feature/transport-hardening`)

Отслеживает закрытие этапов плана из [`08_transport_plan.md`](08_transport_plan.md).
Работа идёт в **едином монорепозитории** на ветке `feature/transport-hardening`
(baseline `f35f804` → ESP32 вшит коммитом `424fd2b`). Прошивка теперь — обычные
tracked-файлы под `ESP32/`, а не отдельный git.
Последняя волна (B4 / X2 / N3 / N2-B6 / handover / X3 / X1-partial / desktop Phase 4)
приземлилась на ветке `feature/transport-compile-only` (**compile-only + host-proven**,
рантайм на реальном ESP32 + телефоне ещё не верифицирован). Более ранние пункты
действительно закрыты на `feature/transport-hardening`.

Статусы: ✅ закрыто · 🔄 в работе · ⬜ не начато.

## T0 — корректность/устойчивость (low-risk, без изменения протокола)

| ID | Что | Репо/файл | Статус |
|---|---|---|---|
| **B1** | Сериализация BLE chunked-write по `onCharacteristicWrite` (нет молчаливой потери кадров) | android · `ble/BleSession.kt` | ✅ |
| **FW-ARC-01** | Кламп `chunk_len` в READ_CHUNK (heap-overflow) | firmware · `transport/transfer.cpp` | ✅ |
| **B2** | Single-central (отклоняем 2-й коннект → per-connection буферы не нужны) | firmware · `ble/ble_channel.cpp` | ✅ |
| **B3** | Диспетчер: доступ к `g_state` перенесён на главный цикл (`ble_loop_tick`) — гонка NFC↔BLE снята без мьютекса | firmware · `ble/ble_channel.cpp` | ✅ |
| **B5** | Idle-watchdog в `ble_loop_tick` (30с) | firmware · `ble/ble_channel.cpp`, `config.h` | ✅ |
| **B8** | `ble_enabled` runtime-gate: поле в g_state + NVS `scud_imm:ble_en` + CLI `BLE-ENABLE/DISABLE` + gate в setup() | firmware · `reader_state.h`,`immutable.cpp`,`main.cpp`,`serial_cmd.cpp`,`ble/ble_channel.cpp` | ✅ |
| **N5** | Дедлайн NFC-сессии (8с, внешний+READ_CHUNK циклы); task-WDT — отдельным follow-up'ом (зависит от версии IDF) | firmware · `transport/transfer.cpp`, `config.h` | ✅ |
| **N6** | `§4.2` исправлен: short-APDU ≤255 (=MAX_APDU_DATA_SIZE 240), убран ложный «256/extended/патч PN532» | doc · `docs/00` | ✅ |
| **N4** | Deferred-response: HCE-работа (DAO+Keystore) ушла с NFC binder-потока в корутину; `synchronized`→kotlinx `Mutex`; `processCommandApdu`→`null`+`sendResponseApdu` | android · `hce/TapController.kt`, `hce/ScudHceService.kt` | ✅ |

> **T0 ЗАКРЫТ ПОЛНОСТЬЮ** (B1, FW-ARC-01, B2, B3, B5, B8, N4, N5, N6 + REPO-H-01).
> **Сборка проверена** (`pio run -e esp32dev` ✅, `esp32dev_ble` ✅, `gradlew :app:compileDebugKotlin` ✅).
> По ходу исправлены 2 pre-existing поломки baseline (не из плана): `ops/passage.cpp`
> (нет `#include state/local.h` → `issue_nonce`), `ui/common/Components.kt`
> (нет `import …runtime.getValue`). Без них прошивка/Android вообще не собирались.
>
> ⚠️ **Caveat:** проверена только КОМПИЛЯЦИЯ. Рантайм/железо (особенно N4 —
> рестрактур core access-path, и B3 — модель задач BLE) **требуют проверки на
> реальном ESP32 + телефоне** перед доверием. Без устройств здесь не закрыть.
>
> **Дальше:** T1 (msg_id-корреляция — меняет провод, нужны синхронные правки в
> 3 реализациях), затем T2 (потоковая верификация фильтра — keystone).

## T1 — корреляция/фрейминг (малое изменение протокола, lockstep)

| ID | Что | Репо | Статус |
|---|---|---|---|
| **B4** | `msg_id`/`op_seq` корреляция op↔result (1-байтовый `op_seq`-префикс внутри собранного сообщения; §16.5.1/§16.6) | android+firmware+doc | ✅ (compile-only) |
| **B7** | INFO один раз, framed, на INFO_NOTIFY (чинил битую доставку + отравление resultChannel); §16.3 поправлен | firmware+doc | ✅ |
| **B10** | `short_reader_id = BLAKE2s(reader_id)[0:6]` (§16.2, приватность); единый source для device-name и adv | firmware+android-comment | ✅ |
| **X4** | Golden-векторы протокола: корпус `docs/test_vectors/protocol_v1.json` (12 доменов, key_id, BLAKE2s, Ed25519, murmur3, bloom) + conformance-тесты во **всех трёх** реализациях + CI-гейтинг | docs+backend+android+firmware+ci | ✅ |

> **X4 — ДОКАЗАН прогоном по ВСЕМ ТРЁМ реализациям** (не компиляцией):
> backend `pytest` **6/6** ✅, Android `testDebugUnitTest` **4/4** ✅ (полный suite 13/13),
> firmware host-build (gcc/MSVC) **20/20** ✅. Три разные крипто-библиотеки
> (PyNaCl / BouncyCastle / Monocypher) дают **байт-идентичные** outputs, включая
> Ed25519-подписи и MurmurHash3 — phone-ACCESS пройдёт verify на ридере,
> reader-квитанция на сервере. Это #1 архитектурная рекомендация, закрытая
> доказательно (класс бага B7).
> **CI-гейтинг:** backend.yml (`pytest`), firmware.yml (job `conformance`, gcc на
> ubuntu — заодно покрыло `REPO-H-05`), android.yml (`testDebugUnitTest` —
> заодно закрыло «нет Android-тестов в CI», `TESTIN-05`).
> Регенерация: `generate.py` (JSON из backend) → `gen_c_header.py` (C-хедер).
> **Сериализация структур — 6 закрыто** (все три impl парсят блоб по
> точным offset'ам и проверяют подпись, прогоном): `issued_key` 151B (server-sig
> DOMAIN_KEY), `time_grant` 148B (DOMAIN_TGR), `passage_receipt` 192B
> (**reader-sig** DOMAIN_PSG), `INFO` 146B (**reader-sig** DOMAIN_INF),
> `delivery_receipt` 112B (**reader-sig** DOMAIN_RCP над bytes[0:48]),
> `FDI`/filter_delivery_info 241B (**reader-sig** DOMAIN_FDI над bytes[0:145];
> `encrypted_courier_blob`@33 = sealed box, в векторе детерминированный филлер,
> проверяется как opaque; `next_nonce`@209 вне подписи). Reader-built структуры
> строятся manual pack+sign (backend имеет parser'ы, не packer'ы). Финальные
> счётчики: backend **12/12**, Android **10/10**, firmware host **0 failures**.
> Layout'ы выверены adversarial extract+reconcile workflow'ом по doc+3 impl
> (оба — `consistent: true`, **0 blocker'ов**, только косметика имён; ранее тот
> же класс ловил рассинхрон INFO и блокирующий дефект include-пути). Коммит `8defbf3`.
>
> **Framing-вектора BLE `[seq][flags][total_len]` (§16.5) — закрыты** (коммиты
> `86d1b49`/`6d478c6`/`a24ec0c`). Транспортный фрейминг — reader↔phone, backend'а
> в проводе нет, поэтому **референс = генератор** (`ble_frame`/`ble_reassemble`,
> round-trip self-checked). 7 векторов пиннят off-by-one-границы (single PDU,
> `234=max_pdu-6` → 1 PDU vs `235` → 2 PDU, ACCESS 256→2, 500→3, малый MTU 20,
> stress 1000). Продьюс-математику вынес в **чистые модули** на обеих сторонах:
> firmware `src/ble/ble_frame.cpp` (`ble_frame_message`, `notify_chunked` теперь
> только sink) + Android `BleFraming.frame` (`writeChunked` сохранил B1-flow
> control). Доказано прогоном: firmware host **+6 framing-проверок, 0 fail**,
> Android `ConformanceVectorsTest` **12/12** (incl. `bleFramingProduce` +
> `bleFramingReassemble` — реассембл гоняет РЕАЛЬНЫЙ `ReassemblyBuf`), полный
> suite **22/22**. Оба PlatformIO-env собираются (RAM/Flash без изменений —
> рефактор byte-equivalent). CI/README build-команды обновлены (+`ble_frame.cpp`).
> **Consume-сторона тоже вынесена** (коммит `cc0e5ff`): inbound reassembly из
> `OpWrite`-колбэка → чистый `ble_reasm_feed` (зеркало Android `ReassemblyBuf`);
> `g_inbound`/`on_op_complete` не тронуты (поля по указателю). Host **+7 проверок**
> (6 round-trip + seq-gap reject), 0 fail; оба env собираются, RAM/Flash без
> изменений. Теперь **обе стороны BLE-фрейминга (produce+consume) — чистые модули
> и протестированы** на firmware и Android.
>
> **APDU/NFC framing (§3.2/§4) — закрыто** (коммиты `878776b`/`eae0f59`/`3ecc555`).
> Reader(PN532)↔phone(HCE), backend'а в проводе нет → генератор = референс. 6
> data-body framing'ов в корпусе: `OP_CHUNKED`/`OP_SINGLE` (FETCH-ответы),
> `READ_CHUNK` cmd+resp (+APDU-wrapper `00 C3..`), `PUSH_CHUNK` cmd (+`00 C4..`),
> `REFERENCE` (`FF FF|msg_id`). Layout'ы выверены **5-агентным extract+reconcile**
> по spec+firmware+android — все 6 `consistent`, **0 discrepancies** (producer/
> consumer для каждого зафиксирован). Доказано прогоном: firmware host **+26
> offset-проверок, 0 fail**; Android `apduFraming` **13/13**. Оба impl проверяют
> **один golden-корпус** → согласие с golden ⇒ согласие байт-в-байт.
> **Follow-up:** вынести build/parse `transfer.cpp` + `TapController` в чистые
> модули (`apdu_frame`/`ApduFraming`, как `ble_frame`/`BleFraming`) — чтобы юнит-
> тест гонял ЖИВОЙ код, а не offset-реплику (NFC/HCE-ядра намеренно не тронуты:
> несут FW-ARC-01/N5/N4, без железа не верифицируются); `[env:native]`+Unity
> (`TESTIN-02`).
>
> **T1 закрыт (compile-only).** B7, B10, X4 закрыты ранее; **B4** теперь тоже
> реализован (compile-only) — wire-change корреляции op↔result через 1-байтовый
> `op_seq`-префикс внутри собранного сообщения (firmware `ble_channel` + Android
> `BleSession`; `docs/00 §16.5.1/§16.6`). Реальная op↔result-корреляция по радио
> — hardware-only.

## T2 — bulk-путь (ядро)

| ID | Что | Статус |
|---|---|---|
| **N2/B6** | Потоковая Ed25519-верификация + снятие 16-КБ потолка приёма. **Сделано:** унифицированный `op_sink` (PURE `transport/op_sink.{h,cpp}`: `ram_sink` + `verify_filter_sink_sig`), `flash_slot_sink` + `commit_filter_from_flash` в `authoritative.cpp` (стрим прямо в неактивный A/B-слот, two-pass verify чтением из flash, атомарный свап), роутинг больших FILTER_UPDATE на flash-путь в `transfer.cpp` (NFC) и `ble_channel.cpp` (BLE), `op_filter_update_from_flash`. Оба env собираются; verify-from-flash host-доказан на RAM-backed fake-flash (`test_op_sink.cpp`, 8/8). **Hardware-only:** реальный SPIFFS I/O, BLE-host-task стрим, `malloc(100КБ)` активного bloom под NimBLE. | ✅ |
| **X2** | Роутинг по типу пакета (caps в INFO/adv) | ✅ (compile-only) |
| **§3.4** | Per-reader размер фильтра / blacklist-delta (backend) + budget-cap | ✅ |
| **N3** | Per-chunk retry / resume-offset на NFC | ✅ (compile-only) |

> **T2:** keystone-**примитив** (streaming Ed25519 verify) сделан и доказан на хосте
> (52/52, в т.ч. чанками == one-shot, негатив, реальный issued_key-sig чанками);
> применён в `op_filter_update`. **Полное снятие потолка теперь реализовано
> (compile-only + host-proven)** — унифицированный flash `op_sink`: большой
> `filter_package` стримит прямо в неактивный SPIFFS A/B-слот (вместо 16-КБ
> RAM-буфера), two-pass verify чтением из flash, атомарный свап только при валидной
> подписи; роутинг по `total_len > TRANSFER_BUFFER_CAP` для inner 0x13 на NFC
> (`transfer.cpp`) и BLE (`ble_channel.cpp`). Host-тест `test_op_sink.cpp` 8/8 на
> RAM-backed fake-flash + golden-пакет. **flash/SPIFFS I/O + NimBLE-flash-стрим —
> hardware-only** (подробности — в задаче `N2/B6-EXEC` ниже). **X2** (роутинг по
> caps `chooseTransport`/`TransportRouter` + adv-бит `BLE_CAP_BULK` 0x01) и **N3**
> (bounded per-chunk retry на NFC READ_CHUNK/PUSH_CHUNK, `READ_CHUNK_RETRIES`=3,
> тот же offset, deadline-bounded) — реализованы compile-only; реальный выбор радио
> и RF-ретраи — hardware-only.
>
> **§3.4 — ЗАКРЫТ (backend, доказан прогоном).** Per-reader sizing уже был в
> `handle_generate_filter` (`m_bits` из популяции отзывов *этого* ридера `n`,
> §8-оптимум; + blacklist-delta). Добавлен недостающий рычаг — конфиг-бюджет
> `filter_max_bloom_bytes` (def 100 КБ): кап `m_bits` так, чтобы per-reader пакет
> всегда влезал в транспорт; выше капа FP растёт мягко (whitelist поглощает
> редкие FP активных ключей), а не выпускается недоставляемый oversize-фильтр.
> **Закрыл `TESTIN-04`** (генерация фильтра была без тестов): `tests/test_filters.py`
> — per-reader sizing масштабируется с `n`, budget-cap срабатывает, blacklist-delta
> несёт reader-revoked ключи, версия инкрементится, **каждый пакет проходит
> server-verify**. Backend-suite **117 passed**. Коммит `43f9097`.
>
> ---
> ### 📋 Задача `N2/B6-EXEC` — унифицированный приём больших пакетов (снятие 16-КБ потолка приёма)
> **Статус:** ✅ реализовано (compile-only + host-proven verify-from-flash; SPIFFS/BLE-flash-стрим — hardware-verified) · **Зависит от:** `ed25519_verify_stream_*` ✅ (готово, T2) · **Только firmware** (протокол / Android / backend-код не трогаются).
>
> **Реализация:** `transport/op_sink.{h,cpp}` — PURE-интерфейс приёмника (`write`/`read`/`len`) + `ram_sink` + `verify_filter_sink_sig` (two-pass: sig из хвоста, тело перечитывается из sink чанками; байт-идентично one-shot verify). `flash_slot_sink` (File-backed) + `commit_filter_from_flash` живут в `authoritative.cpp` (SPIFFS за интерфейсом): стрим в неактивный A/B-слот, verify чтением из flash, apply blacklist_delta + атомарный свап только при валидной подписи (иначе `RES_BAD_SIGNATURE`, активный фильтр не тронут). `verify_filter_file_sig` (boot-load, FW-ARC-03) переведён на тот же `verify_filter_sink_sig` — один verifier. Роутинг по `total_len > TRANSFER_BUFFER_CAP` для inner_opcode 0x13: `transfer.cpp` (NFC READ_CHUNK-цикл) и `ble_channel.cpp` (отдельный flash-режим реассемблера по первому PDU; `ble_reasm_feed` не тронут — golden-векторы валидны). `op_filter_update_from_flash` — header-валидации в RAM по первым 56 B, тело/подпись/свап из flash. Малые/горячие опы (ACCESS) — на прежнем RAM-пути без изменений. Host-тест `test_op_sink.cpp` (RAM-backed fake flash_slot_sink, golden filter_package) — 8/8 (valid→VALID, порча тела/подписи/домена→INVALID, undersize→reject).
>
> **Проблема:** потолок 16 КБ (`TRANSFER_BUFFER_CAP`) только на **приёме/сборке** (`transfer.cpp:399` reject + статический `g_inbound.buf[16384]`). Активный bloom уже грузится в RAM до `MAX_FILTER_BYTES` 127 КБ (`load_filter_from_flash` malloc), ACCESS читает из неё. → Правится **только путь приёма+верификации**; хранение и ACCESS большой фильтр уже тянут.
>
> **Дизайн — унифицировать, не делать filter-only.** Абстракция приёмника `op_sink` (`write(chunk)` + `read(off,len)` back-read), две реализации:
> - `ram_sink` — буфер ≤16 КБ (мелкие опы; ACCESS — горячий путь, обязан остаться RAM-быстрым, поэтому полностью «всё в flash» нельзя);
> - `flash_slot_sink` — стримит **прямо в неактивный SPIFFS A/B-слот** (без лишней копии scratch→слот).
> Выбор sink'а — по `total_len` (`> TRANSFER_BUFFER_CAP → flash_slot_sink`). Транспорт (NFC `transfer.cpp` + BLE `ble_channel.cpp`/`ble_frame.cpp`) кормит чанки в один `sink`; handler читает оп через единый `read()`. Зеркалит уже сделанную **produce**-sink-абстракцию (`ble_frame`).
>
> **Verify (two-pass из flash):** после приёма — `ed25519_verify_stream_init(sig, server_ed_pub)` (sig из хвоста слота) → `update(DOMAIN_FLT,16)` → тело `[0:len-64]` читаем из слота чанками → `update`/`final`. Header-валидации (`reader_id`, монотонность версии, `m_bits`, размеры) — по первому чанку, ранний reject до записи всего. При успехе — атомарный свап `filter_current_slot` (A/B уже атомарен: активный фильтр не страдает до свапа, поэтому «verify-before-store» из 3-й схемы избыточен).
>
> **Файлы:** `state/authoritative.{h,cpp}` (`flash_slot_sink` + verify-from-flash + commit-swap), `transport/transfer.cpp` (роутинг + снять `>CAP`-reject для flash-пути), `ble/ble_channel.cpp`+`ble/ble_frame.cpp` (flash-sink режим в реассемблере по `op[0]==0x13`/размеру), `ops/filter_update.cpp` (ветка «из flash»). **Конфиг:** reject → `MAX_FILTER_BYTES`; `partitions.csv` — слоты A/B ≥128 КБ; **backend** `filter_max_bloom_bytes` обратно до ~100 КБ (одна строка — теперь доставляемо).
>
> **Не меняется:** байт-точный протокол → **conformance-векторы те же**; backend-код; Android.
> **Опц. (RAM для 100-КБ фильтров):** активный bloom при 100 КБ = 100 КБ RAM (malloc, риск фрагментации). Снять — raw-партиция + `esp_partition_mmap`, `bloom_contains` по mapped-указателю без RAM-копии. Не обязательно для v1.
> **Тестируемость:** SPIFFS за интерфейсом `op_sink` → host-тест two-pass verify на **RAM-backed fake-flash** + golden `filter_package` (valid→OK, порченый→reject). Реальный SPIFFS I/O / BLE-flash-sink / `malloc(100КБ)` под NimBLE — **hardware-required**.
>
> ---

## T3 — handover + relay-политика

| ID | Что | Статус |
|---|---|---|
| handover | NFC→BLE handover (token, привязка к tap_nonce) | ✅ (compile-only + host-proven) |
| **X3** | Политика ACCESS=NFC; переписать `§16.8`; опц. RTT/RSSI/presence-gate | ✅ (doc + firmware enforcement; RTT/RSSI отложено) |

> **handover — реализовано (compile-only + host-proven).** Точный 167-B `handover_token`
> (marker 0x99, reader-signed над `DOMAIN_BLE ‖ bytes[0:103]`) + опкоды
> `INNER_HANDOVER_ISSUE 0x17` (NFC) / `INNER_HANDOVER_PRESENT 0x18` (BLE) формализованы
> в `docs/00 §17.1` (+ `08 §4.3`). **Lockstep по всем 4 компонентам:**
> firmware (`ops/handover.{h,cpp}`, dispatch NFC/BLE, soft-gate `handover_required`
> default 0 fail-closed, cap `BLE_CAP_HANDOVER` в adv, `pending_handover`/`handover_authorized`
> в g_state, привязка к `last_issued_nonce`); Android (`ble/HandoverToken.kt` build/parse/verify,
> `ble/HandoverOrchestrator.kt` BLE-половина present+stream bounded+guarded, опкоды
> в TransportRouter); backend = reference packer golden-вектора. **Golden-вектор
> `handover_token`** в `protocol_v1.json` проверяется во ВСЕХ трёх conformance-тестах
> (backend pytest, Android `ConformanceVectorsTest`, firmware `test_conformance.cpp`):
> layout по offset'ам + reader-подпись над `[0:103]` под `DOMAIN_BLE` — байт-идентично
> PyNaCl/BouncyCastle/Monocypher. **Host-proven:** token layout + sig + binding-правило.
> **Hardware-only (compile-only seam):** реальный two-radio rendezvous (NFC-issue →
> connectGatt к MAC из токена → per-connection authorize в рантайме) — без телефона+ридера
> не верифицируется. Оба PlatformIO-env собираются; conformance зелёный во всех трёх.
>
> **X3 — реализовано (doc + firmware enforcement).** `§16.8` переписан вокруг
> **relay≠replay**: nonce бьёт replay, но не relay; от relay защищает **физическая
> близость NFC** (~4 см) ⇒ proximity-attested операции (ACCESS/FDI/TIME_SYNC/REVOKE_KEY/
> GET_PASSAGE_RECEIPT) — **всегда NFC**; bulk (FILTER/BLACKLIST) server-signed+монотонны ⇒
> relay-safe ⇒ можно BLE; handover легитимизирует bulk-по-BLE тапом. **Enforcement
> двусторонний:** телефон (`chooseTransport`, X2) не шлёт proximity по BLE, а ридер
> (defense-in-depth) **отвергает** их на BLE-диспетчере (`RES_NOT_AUTHORIZED`,
> независимо от caps) — вредоносный телефон не проведёт ACCESS по BLE. По BLE приняты
> только FILTER_UPDATE / GET_BLACKLIST / HANDOVER_PRESENT; fail-closed. Оба env собираются
> (esp32dev_ble +8 B flash). Опциональный RTT/RSSI presence-gate для §17 — отложен
> (нужно железо для калибровки, рантайм-only).

## T4 — унификация (стратегически)

| ID | Что | Статус |
|---|---|---|
| **X1 (partial)** | **Dispatch унифицирован** — единый источник transport-policy (`transport/op_dispatch.{h,cpp}`). Раньше per-op dispatch + transport-решение **дублировались** в `transfer.cpp::dispatch_op` (NFC) и inline-`switch` в `ble_channel.cpp` (BLE) и уже один раз разошлись («BLE accepted ACCESS»). Теперь одна функция `dispatch_op(inner, op, len, result, max, OpTransport)` — byte-identical обоим прежним местам (X3 proximity-rejects, T3 handover-gate на FILTER_UPDATE, HANDOVER_ISSUE/PRESENT-разводка). Оба env собираются (esp32dev +540 B flash, esp32dev_ble −188 B; RAM 0). | 🟨 |
| **X1 (deferred)** | Унификация framing-FSM + L2CAP-адаптер. NFC/BLE framing/reassembly-ядра и large-filter flash-пути **сознательно не тронуты** — они несут FW-ARC-01/N5/N4/N2 и проверяемы только на железе (hardware-only). | ⬜ |

---

### Известные follow-up'ы, замеченные по ходу (вне текущих коммитов)
- `transfer.cpp` READ_CHUNK: чанк нулевой длины с `!LAST` не двигает offset → потенциально зацикливание; закрывается дедлайном сессии (**N5**) либо отдельным guard'ом «нет прогресса».
- B1: per-frame timeout 3с вложен в общий `runOperation` timeout 5с — для bulk (много кадров) пересмотреть при **T2**; для мелких операций достаточно.

### Репо-координация (REPO-H-01) — ✅ закрыто
ESP32 вшит в монорепо (де-сабмодуль, коммит `424fd2b`): убран висячий gitlink,
прошивка добавлена как обычные файлы, `.pio` build-cache исключён через
`ESP32/firmware/.gitignore`. Свежий клон теперь содержит все четыре компонента;
firmware-фиксы коммитятся прямо в монорепо.
Мелкие follow-up'ы гигиены (вне absorb, на стадию repo-cleanup): в импорте
остались junk-файлы `ESP32/desktop.ini` и `ESP32/firmware/.claude/settings.local.json`
— удалить отдельным коммитом.
