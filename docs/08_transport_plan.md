# 08. Транспорт (NFC + BLE) — детальный план устранения узких мест

> Фокус: **реализация транспорта**. Узкие места не-транспортного слоя — в
> [`07_architecture_review.md`](07_architecture_review.md) (раздел 9 ниже даёт перекрёстные ссылки).
> Метод: чтение кода (`transport/`, `ble/`, `hce/`, `data/crypto/`) + два мульти-агентных
> разбора с верификацией фактов платформы. Каждое изменение, меняющее байты на проводе,
> обязано приземлиться синхронно в `docs/00` + backend + firmware + android (shared-инвариант).

Severity: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low. Effort: S (<1д) · M (1–3д) · L (>3д).

---

## 1. Карта узких мест транспорта

| ID | Сев | Узкое место | Где | Меняет провод? |
|---|---|---|---|---|
| **N1** | 🔴 | Bulk-фильтр по NFC = взрыв round-trip'ов (≈530 RT, доминирует HCE-латентность) | transfer.cpp:403-413 | нет (роутинг) |
| **N2** | 🔴 | 16 КБ RAM-cap + потоковая верификация не реализована (Monocypher one-shot) → тяжёлые пакеты режутся; противоречит масштабу 65k | transfer.cpp:386; config.h:33; op_filter_update.cpp:76 | нет ✅ **Решено** (compile-only + host-proven через flash `op_sink`: стрим в неактивный A/B-слот + two-pass verify; flash/SPIFFS I/O — hardware-required; см. docs/transport_progress.md) |
| **N3** | 🟠 | Нет per-chunk retry/resume — любой RF-сбой отменяет всю передачу | transfer.cpp:406-410 | да (resume-offset) |
| **N4** | 🟠 | Блокирующий DB/Keystore на NFC-binder-потоке под mutex | TapController.kt:94,226 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; deferred-response + kotlinx Mutex) |
| **N5** | 🟠 | Нет дедлайна сессии/watchdog на ридере (`while(true)`) | transfer.cpp:316; main.cpp:94 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; дедлайн NFC-сессии 8с) |
| **N6** | 🟡 | «MTU 256, 1 над ISO-лимитом» — артефакт спеки (провод ≤252, длина `uint8_t`) | docs/00 §4.2; apdu.cpp:57 | да (доки) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; §4.2 исправлен на ≤255) |
| **B1** | 🔴 | `WRITE_NO_RESPONSE` пачкой без ожидания колбэка → молчаливая потеря кадров на Android | BleSession.kt:219-250 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; сериализация write по onCharacteristicWrite) |
| **B2** | 🟠 | Единый глобальный `g_inbound`/`result_buf`, нет enforcement одного central | ble_channel.cpp:56-63 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; single-central, 2-й коннект отклоняется) |
| **B3** | 🔴 | Гонка `g_state` (nonce-ring/session_seq/passage-cache) между NFC-loop и NimBLE-task без mutex | ble_channel.cpp; main.cpp:94-99 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; доступ к g_state перенесён в главный цикл) |
| **B4** | 🟠 | Позиционная корреляция result (UNLIMITED channel) → рассинхрон op↔result | BleSession.kt:104-109 | да (msg_id) ✅ **Решено** (compile-only; ветка feature/transport-compile-only — см. docs/transport_progress.md; 1-байтовый `op_seq`-префикс в собранном сообщении, §16.5.1/§16.6) |
| **B5** | 🟠 | `ble_loop_tick()` пуст — нет idle-watchdog (спека обещает 30с) | ble_channel.cpp:325-328 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; idle-watchdog 30с в ble_loop_tick) |
| **B6** | 🔴 | 16 КБ cap на `g_inbound.buf` → фильтр не проходит и по BLE | ble_channel.cpp:212 | нет ✅ **Решено** (compile-only + host-proven через flash `op_sink`: BLE-реассемблер стримит большой фильтр прямо в flash; NimBLE-flash-стрим — hardware-required; см. docs/transport_progress.md) |
| **B7** | 🟡 | INFO пушится дважды (одна копия «ломаная» для reassembler'а) | ble_channel.cpp:112-123 | да (упрощение) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; INFO один раз, framed, на INFO_NOTIFY) |
| **B8** | ⚪ | `ble_enabled`/battery-gate не соблюдается (TODO) | ble_channel.cpp:272-275 | нет ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; runtime-gate ble_enabled + NVS + CLI) |
| **B10** | ⚪ | Источник short_reader_id в manufacturer-data расходится спека↔firmware | ble_channel.cpp:307-311 | да (доки) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; `BLAKE2s(reader_id)[0:6]`, единый источник) |
| **X1** | 🟠 | Фрейминг продублирован 4× (NFC vs BLE × ридер/телефон), уже разошёлся | transfer.cpp ↔ ble_channel.cpp; TapController ↔ BleSession | да (унификация) 🟨 **Частично** (compile-only; ветка feature/transport-compile-only — см. docs/transport_progress.md; per-op dispatch унифицирован в `dispatch_op`; framing-FSM + L2CAP — отложено/hardware-only) |
| **X2** | 🔴 | Нет роутинга по типу пакета — всё идёт по тому каналу, что подключён | TapDecisionTree.kt; main.cpp | да (caps) ✅ **Решено** (compile-only; ветка feature/transport-compile-only — см. docs/transport_progress.md; adv-бит `BLE_CAP_BULK` 0x01 + чистый `chooseTransport`/`TransportRouter`) |
| **X3** | 🔴 | Relay-атака на ACCESS по BLE; §16.8 путает replay с relay; нет distance-bounding | docs/00 §16.8, §17 | да (политика) ✅ **Решено** (doc + firmware enforcement; ветка feature/transport-compile-only — см. docs/transport_progress.md; §16.8 переписан relay≠replay, ридер отвергает proximity-ops на BLE; RTT/RSSI отложено) |
| **X4** | 🟠 | Нет golden-векторов фрейминга / кросс-impl тестов транспорта | tests/ | — (тесты) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; golden-векторы фрейминга + conformance во всех 3 impl + CI) |

---

## 2. Целевая архитектура транспорта

**Принципы (end-state):**

1. **Транспорт по типу пакета, а не «всё везде».**
   - **NFC** — мелкие проксимити-операции: `ACCESS`, `FDI`, `TIME_SYNC`, `REVOKE_KEY`, `PASSAGE`. Близость ~4 см = свойство безопасности (анти-relay для открытия двери).
   - **BLE** — bulk: `FILTER_UPDATE`, большой `GET_BLACKLIST`. Доставка фильтра relay-безопасна (пакет подписан сервером, версия монотонна), поэтому слабость BLE к relay здесь не важна.
2. **Тяжёлые пакеты — потоково в flash, без RAM-cap** (инкрементальная Ed25519-верификация). Снимает N2/B6 и противоречие с масштабом 65k.
3. **NFC-тап как «I am here» + хендовер на BLE для bulk** на mains-ридерах: тап делает ACCESS и выдаёт короткоживущий, привязанный к tap-nonce токен рандеву; крупный фильтр льётся по BLE. Bulk уносится с NFC-критического пути, но авторизован физическим тапом.
4. **Единая FSM фрейминга/корреляции** под интерфейсом `Transport` (msg_id везде, один watchdog, один overflow-check). NFC и BLE — адаптеры; L2CAP — будущий адаптер.
5. **Один писатель состояния**: всё мутирование `g_state` сериализовано (mutex или единая задача-диспетчер), один активный central, дедлайны на обоих каналах.

```
            ┌─────────────────── общий слой операций (ops/*) ───────────────────┐
            │  ACCESS · FDI · TIME_SYNC · FILTER_UPDATE · BLACKLIST · REVOKE · PASSAGE  │
            └───────────────────────────▲───────────────────────────────────────┘
                                         │  send_message(bytes) / recv_message()
                          ┌──────────────┴───────────────┐   (одна FSM: framing+msg_id+reassembly+watchdog)
                          │        Transport (iface)      │
              ┌───────────┴──────────┐        ┌───────────┴───────────┐        ┌─────────────┐
              │ NFC-APDU adapter      │        │ BLE-GATT adapter      │  ...   │ BLE-L2CAP    │ (future)
              │ (reader-pull, ≤252B)  │        │ (write/notify chunks) │        │ (stream/CoC) │
              └───────────────────────┘        └───────────────────────┘        └─────────────┘
   роутинг по типу пакета: мелкое → NFC · bulk(FILTER/BLK) → BLE (+ NFC→BLE handover)
```

---

## 3. По каждому узкому месту: варианты и рекомендация

### 3.1 NFC

**N1 — bulk по NFC.** *Улучшить:* per-chunk retry + дедлайн (N3/N5) — паллиатив. *Заменить:* **роутинг bulk на BLE + NFC→BLE handover (X2, §4.3)**. **Рекомендация:** заменить для mains-ридеров; для battery (NFC-only) — держать фильтр маленьким (per-reader sizing + blacklist-delta, §3.4) + потоковая запись (N2). Effort M.

**N2 — 16 КБ cap / нет streaming.** *Заменить буферизацию на потоковую верификацию:* инкрементальный SHA-512 над `R‖A‖M` с записью чанков прямо в неактивный flash-слот; финальная EdDSA-проверка по накопленному хешу (см. §4.1). Убирает `malloc(total_len)` и потолок. mbedTLS уже в core и даёт `mbedtls_sha512_update` (Monocypher one-shot — поэтому и буферизировали). **Рекомендация:** обязательно, это keystone-фикс. Effort M.
✅ **Решено** (compile-only + host-proven; см. docs/transport_progress.md): унифицированный flash `op_sink` снял потолок — большой `filter_package` стримит в неактивный SPIFFS A/B-слот, two-pass verify-from-flash (sig из хвоста, тело re-read чанками через `ed25519_verify_stream_*`), атомарный свап только при валидной подписи. Host-тест `test_op_sink.cpp` 8/8. Реальный SPIFFS I/O — hardware-required.

**N3 — нет resume.** *Улучшить:* per-chunk retry внутри сессии (повтор READ_CHUNK N раз перед abort). *Заменить:* resume-offset через тапы — ридер хранит `(msg_id, offset, sha512_state)` партиала; следующий тап продолжает. Сложно из-за переноса состояния хеша. **Рекомендация:** дешёвый per-chunk retry сейчас; resume-offset — только если battery-ридеры реально упираются (иначе bulk и так на BLE). Wire: добавить «продолжить с offset» в семантику FILTER_UPDATE. Effort S→M.
✅ **Решено** (compile-only — см. docs/transport_progress.md; bounded per-chunk retry на NFC READ_CHUNK/PUSH_CHUNK: `READ_CHUNK_RETRIES`=3, тот же offset, deadline-bounded). Resume-offset через тапы — не делалось (bulk ушёл на BLE). Реальные RF-ретраи — hardware-only.

**N4 — блокировка NFC-потока.** *Улучшить:* предвычислять решение/операции на фоне на SELECT_AID, держать готовые байты; `processCommandApdu` только собирает кадр. *Заменить:* отложенный ответ — вернуть `null` и позже `sendResponseApdu()` (документированная возможность HCE). **Рекомендация:** обе — precompute + отказ от `runBlocking` под mutex; снять зависимость от `fRetry=3.28с`. Effort M.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; deferred-response, HCE-работа ушла в корутину, `synchronized`→kotlinx `Mutex`)

**N5 — нет дедлайна.** Штамп `t_session_start` уже есть → abort `run_tap_session` при >8–10 с; cap по числу/времени READ_CHUNK; обвязать ESP32 task-watchdog вокруг tap-loop. **Рекомендация:** сделать, дёшево. Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; дедлайн NFC-сессии 8с; task-WDT — follow-up)

**N6 — артефакт MTU 256.** Зафиксировать `max_apdu_size ≤ 255` в INFO и переписать `docs/00 §4.2` (убрать «нужен патч PN532»). Кода менять не надо (провод уже ≤252). **Рекомендация:** сделать (корректность ВКР). Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; §4.2 исправлен, short-APDU ≤255)

### 3.2 BLE

**B1 — потеря кадров WRITE_NR.** *Улучшить:* очередь write с отправкой следующего чанка только по `onCharacteristicWrite`. *Заменить:* `WRITE_TYPE_DEFAULT` (write-with-response) — стек сам пейсит/подтверждает (свойство `WRITE` на `OP_WRITE` уже объявлено в firmware). **Рекомендация:** очередь по колбэку для bulk; write-with-response для мелких/control. Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; очередь по `onCharacteristicWrite`)

**B2 — глобальные буферы / нет single-central.** На `onConnect` отклонять второй коннект (или гасить advertising пока подключён); `g_inbound`/result в per-connection контекст по `conn_handle`; `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1`. **Рекомендация:** сделать (соответствует «один человек у двери»). Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; 2-й коннект отклоняется → per-conn буферы не нужны)

**B3 — гонка `g_state`.** *Улучшить:* FreeRTOS `recursive mutex`/`portMUX` вокруг доступа к nonce-ring/session_seq/passage-cache/буферам. *Заменить (чище):* единая задача-диспетчер — NFC и BLE кладут собранную операцию в очередь, обрабатывает один поток → NFC/BLE взаимоисключающи. **Рекомендация:** диспетчер-задача (заодно решает B2 и часть X1). Effort M.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; доступ к g_state перенесён на главный цикл `ble_loop_tick`, гонка снята без мьютекса)

**B4 — позиционная корреляция.** Добавить `msg_id`/`op_seq` (1–2 Б) в первый кадр OP_WRITE, эхо в первом кадре RESULT_NOTIFY; `BleSession` сопоставляет по id (map id→Deferred) вместо `receive()`. **Рекомендация:** сделать; **меняет провод** → 3 impl + docs/00. Effort S.
✅ **Решено** (compile-only — см. docs/transport_progress.md; 1-байтовый `op_seq`-префикс внутри собранного сообщения, firmware `ble_channel` + Android `BleSession`, §16.5.1/§16.6). Реальная op↔result-корреляция по радио — hardware-only.

**B5 — пустой watchdog.** Реализовать §16.6: трекать last-activity, в `ble_loop_tick` дисконнектить idle >30 с (+ cap общей длины сессии). **Рекомендация:** сделать. Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; idle-watchdog 30с в `ble_loop_tick`)

**B6 — 16 КБ на BLE.** Та же потоковая запись в flash, что и N2, на пути `OpWriteCallbacks` (кормить верификатор чанками, не копить в `g_inbound`). **Рекомендация:** сделать вместе с N2 (общий streaming-sink). Effort M.
✅ **Решено** (compile-only + host-proven; см. docs/transport_progress.md): общий с N2 flash `op_sink` — BLE-реассемблер (`ble_channel.cpp`) по `total_len > TRANSFER_BUFFER_CAP` для inner 0x13 стримит большой фильтр прямо в flash, не копя в `g_inbound`. NimBLE-flash-стрим — hardware-required.

**B7 — двойной INFO.** Убрать дубль; оставить один канал INFO (или, при переходе на NUS-стиль, INFO — просто тип сообщения). **Рекомендация:** упростить (попадает в редизайн фрейминга). Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; INFO один раз, framed, на INFO_NOTIFY)

**B8 — gate не соблюдается.** `ble_init` читает NVS `ble_en`/`device_mode`; на `battery_only` не поднимать стек. **Рекомендация:** сделать (экономия RAM/питания, §16.10). Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; runtime-gate `ble_enabled` + NVS + CLI `BLE-ENABLE/DISABLE`)

**B10 — расхождение manufacturer-data.** Согласовать источник short_reader_id (firmware ↔ docs/00 §16.2). Effort S.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; `short_reader_id = BLAKE2s(reader_id)[0:6]`, единый источник)

**NUS-стиль (2 характеристики) / L2CAP CoC** — см. §3.3 и §8.

### 3.3 Сквозные

**X1 — дублирование фрейминга.** *Заменить:* один интерфейс `Transport.send_message/recv_message` + одна FSM (chunk/reassembly/correlation/watchdog); NFC-pull и BLE-push — адаптеры. **Рекомендация:** стратегически — да (зонтик для всего), но после T0–T3 (рефактор тайминг-чувствительного кода). Effort L.
🟨 **Частично решено** (compile-only — см. docs/transport_progress.md): унифицирован per-op dispatch — единый источник transport-policy `dispatch_op(inner, …, OpTransport)` (раньше дублировался в `transfer.cpp` + `ble_channel.cpp`). **Отложено (hardware-only):** унификация framing/reassembly-FSM + L2CAP-адаптер (NFC/BLE framing-ядра и large-filter flash-пути сознательно не тронуты).

**X2 — роутинг по типу пакета.** `TapDecisionTree` (и BLE-сессия) становятся transport-aware: при наличии BLE-caps у ридера и pending-фильтре крупная доставка идёт по BLE, ACCESS/мелкое — по NFC. Ридер публикует caps (manuf-data bit / INFO-флаг). **Рекомендация:** сделать; **меняет провод** (capability-флаги). Effort M.
✅ **Решено** (compile-only — см. docs/transport_progress.md; adv manuf-data бит `BLE_CAP_BULK` 0x01 + чистая `chooseTransport(inner_opcode, supportsBulk)`/`TransportRouter`: bulk 0x13/0x14 → BLE iff caps, proximity → NFC). Реальный выбор радио — hardware-only.

**X3 — relay / proximity.** *Документировать:* §16.8 — признать relay (replay≠relay), назвать NFC-близость свойством безопасности. *Политика:* ACCESS-door — только NFC; BLE-ACCESS оставить лишь для §18 `barrier/gate`, где это осознанно. *Митигейшн (по силе):* (a) ограничить дальнобойный ACCESS — сильнее всего; (b) RTT-окно + RSSI-гейт — слабо (софт-RTT через GATT бьётся, время Keystore-подписи доминирует); (c) UWB ToF — единственное физически надёжное, но нет железа (отклонено для MVP). **Рекомендация:** (a) + честная документация; (b) опционально для §17. Effort S (политика/доки) + M (RTT).
✅ **Решено** (doc + firmware enforcement; compile-only — см. docs/transport_progress.md): §16.8 переписан (relay≠replay, NFC-близость как анти-relay).
🔄 **Пересмотрено (унификация транспорта H1, 2026-05-31):** прежний reader-side NFC-only boundary **снят** — все операции (ACCESS/FDI/TIME_SYNC/REVOKE_KEY/FILTER_UPDATE/GET_BLACKLIST) едут по обоим каналам; relay/wormhole-риск BLE **явно принят** владельцем. Единственный BLE-митигейшн — Android per-reader confirm-session (UX-контроль на эндпоинте, **не** граница ридера: кастомный BLE-клиент пишет CHR_OP_WRITE напрямую). Что держится на обоих транспортах: нет подделки (Ed25519), нет cross-reader (RES_WRONG_READER), нет replay (per-session nonce), REVOKE ограничен своей permit-группой, время не подделать (TIME_SYNC требует server-grant). passage_receipt больше не отдельный оп — едет в хвосте ACCESS_VERDICT (см. §5.4 в 00; H3 fold-in). RTT/RSSI presence-gate — отложен (нужно железо).

**X4 — тесты транспорта.** Golden-векторы фрейминга (вход→точные кадры) + host-fuzzer reassembly + интеграционный round-trip. Тянет к общей проблеме conformance-векторов (07/TESTIN-01). Effort M.
✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; golden-векторы фрейминга BLE+APDU, conformance во всех 3 impl + CI; host-fuzzer reassembly/round-trip — частично)

### 3.4 Управление размером фильтра (снижает давление на оба канала)

Фильтр — **по ридеру** (`(filter_version, reader_id)`), а не глобальный. Релевантное множество отзывов одного ридера обычно много меньше 65k. Рычаги: per-reader bloom-sizing (`m_bits` под популяцию ридера), blacklist-delta вместо полного пакета, инкрементальные обновления. Это делает 16-КБ (даже после фикса — желательную) границу редко достижимой на battery-ридерах. **Рекомендация:** заложить в генерацию фильтра на backend. Effort M (backend).

---

## 4. Ключевые реализации (эскизы)

### 4.1 Потоковая верификация фильтра (снимает N2/B6) — keystone
Ed25519: `h = SHA512(R ‖ A ‖ M)`, проверка `[S]B == R + [h]A`. `R` (первые 32 Б подписи) и `A` (server_pub) известны из заголовка пакета **до** тела `M`. Значит `M` можно не держать в RAM:

```c
// при старте FILTER_UPDATE: распарсить header (m_bits,k,seed,len,wl,signature R||S)
sha512_init(&ctx); sha512_update(&ctx, R, 32); sha512_update(&ctx, A, 32);
open_inactive_slot();                       // SPIFFS A/B, неактивный слот
// на каждый прибывший чанк (NFC READ_CHUNK или BLE OP_WRITE):
sha512_update(&ctx, chunk, n);
flash_write(slot, offset, chunk, n);        // пишем сразу в flash, не в RAM
// в конце:
sha512_final(&ctx, h);
if (!ed25519_check_with_prehash(h, S, A)) { discard_slot(); return BAD_SIGNATURE; }
fsync(slot); commit_slot_pointer();         // атомарный swap (см. 07/FW-ARC-03)
```
mbedTLS даёт инкрементальный `mbedtls_sha512_update`; финальную EdDSA-проверку — на готовом `h`. Это ровно дизайн §4.10, который не сделали из-за one-shot Monocypher. Бонус: можно дописать реверификацию слота на буте (07/FW-ARC-03).

### 4.2 BLE: сериализация write + корреляция (B1/B4)
```kotlin
// Android: следующий чанк — только после колбэка предыдущего; результат — по msg_id
suspend fun runOperation(op: ByteArray): ByteArray {
    val id = nextOpId()
    val deferred = pending.put(id, CompletableDeferred())   // map id→result
    writeChunkedAwaiting(opChr, frame(id, op))              // await onCharacteristicWrite на каждый чанк
    return withTimeout(5000) { deferred.await() }           // result с тем же id
}
// onCharacteristicChanged(result): reassemble → parse id → pending.remove(id)?.complete(bytes)
```
Ридер копирует `id` входной операции в первый кадр результата.

### 4.3 NFC→BLE handover (X2/T3, для mains-ридеров) — ✅ реализовано (compile-only)
```
NFC tap: SELECT → PUSH_INFO(fresh_nonce) → ACCESS → … →
   phone шлёт INNER_HANDOVER_ISSUE(0x17, phone_pubkey) по NFC →
   ридер выдаёт handover_token (167 B, marker 0x99) =
       0x99 ‖ reader_id ‖ phone_pubkey ‖ reader_ble_addr ‖ tap_nonce ‖ issued_at ‖ expires_at
       ‖ sign_reader( domain_BLE, bytes[0:103] )
   телефон: connectGatt(reader_ble_addr) → INNER_HANDOVER_PRESENT(0x18, token) →
   ридер верифицирует (своя подпись + привязка к tap_nonce/phone_pubkey + expiry) →
   авторизует FILTER-поток на этом соединении (one-shot) → стримит FILTER по BLE
```
Bulk уходит с NFC, но **авторизован физическим тапом** (proximity-attested). Beam удалён в Android 14 → рандеву своё (через INFO/manuf-data), не OS-API.

**Реализовано (compile-only + host-proven):** точный 167-B layout + опкоды + binding/gate
формализованы в `docs/00 §17.1`. Прошивка: `ops/handover.{h,cpp}` (`op_handover_issue`
NFC-only / `op_handover_present` BLE-only), dispatch в `transfer.cpp`+`ble_channel.cpp`,
soft-gate `handover_required` (CFG[], default 0, fail-closed), cap `BLE_CAP_HANDOVER`
в adv. Android: `ble/HandoverToken.kt` (build/parse/verify), `ble/HandoverOrchestrator.kt`
(BLE-половина present+stream, bounded+guarded). Golden-вектор `handover_token` в корпусе,
проверяется backend/Android/firmware-host (layout+reader-sig+binding байт-идентичны).
**Hardware-only seam:** реальный two-radio rendezvous (NFC-issue → connectGatt к MAC из
токена → per-connection authorize в рантайме) — без телефона+ридера не верифицируется.

### 4.4 Единый писатель состояния (B3/B2)
Очередь + одна задача-диспетчер: и `run_tap_session`, и `OpWriteCallbacks` кладут `{transport, conn, op_bytes}` в queue; единственный consumer вызывает `dispatch_op` и шлёт результат назад через интерфейс транспорта. NFC/BLE взаимоисключающи; гонок по `g_state` нет by construction.

---

## 5. Изменения протокола `docs/00` (lockstep: doc+backend+firmware+android)

- §4.2: `max_apdu_size ≤ 255`, убрать «патч PN532» (N6).
- §4.x/§16.5: добавить `msg_id`/`op_seq` корреляцию в OP/RESULT-фреймы (B4).
- §5.2 INFO / §16.2 adv: capability-флаги ридера (BLE-bulk, handover) (X2); согласовать short_reader_id (B10).
- §17: `handover_token` (привязка к tap_nonce), domain_BLE — ✅ формализовано в §17.1 (T3, реализовано compile-only).
- §4.10 / §15: описать потоковую верификацию как реализованную (N2); зафиксировать «FILTER/BLK — предпочтительно BLE».
- §16.8: ✅ переписан (X3) — relay≠replay, NFC-близость как защита, политика ACCESS-транспорта + двусторонний enforcement (телефон `chooseTransport` + ридер отвергает proximity-ops на BLE).

---

## 6. Тестирование транспорта

- **Golden-векторы фрейминга:** вход (op_bytes, MTU) → ожидаемые точные кадры; reassembly(кадры) → op_bytes. Один корпус для firmware (Unity host-env) и Android (JVM unit). Тянет к 07/TESTIN-01.
- **Fuzzer reassembly:** случайные/злонамеренные кадры (seq-skip, overflow, дубль, обрезка) — проверять fail-closed, отсутствие OOB (закрывает класс 07/FW-ARC-01).
- **Интеграция round-trip:** backend-issued байты → firmware native verify → firmware-signed receipt → backend ingest.
- **Стресс BLE:** многокадровый op под потерей кадров (B1), второй central (B2), idle-timeout (B5).

---

## 7. Дорожная карта

**T0 — Корректность/устойчивость, low-risk, ~1 нед (без/мин. изменения протокола)**
B1 (write-serialization, Android) · B2 (single-central+per-conn, fw) · B3 (mutex/диспетчер, fw) · B5 (idle-watchdog, fw) · B8 (gate, fw) · N5 (NFC-дедлайн+task-wdt, fw) · N6 (доки) · N4-частично (precompute off-thread).

**T1 — Корреляция/фрейминг, малое изменение протокола, ~1 нед**
B4/op_seq (3 impl+doc) · B7 (один INFO) · B10 (согласование) · X4 (golden-векторы фрейминга).

**T2 — Bulk-путь (ядро), ~2–3 нед**
N2/B6 (потоковая верификация в flash — keystone) · X2 (роутинг по типу пакета + caps) · §3.4 (per-reader sizing/delta, backend) · N3 (per-chunk retry; resume-offset опц.) · N4-завершение.

**T3 — Хендовер + снятие bulk с NFC, ~2 нед**
NFC→BLE handover (§4.3) ✅ compile-only · X3 (политика ACCESS=NFC + §16.8) ✅ doc+firmware enforcement; опц. RTT/RSSI для §17 — отложено (нужно железо).

**T4 — Унификация (стратегически), ~3+ нед**
X1 (один `Transport` + одна FSM фрейминга; NFC/BLE адаптеры) · далее L2CAP CoC как адаптер (см. §8).
> **X1 — partial done (compile-only):** dispatch унифицирован — единый источник
> transport-policy `transport/op_dispatch.{h,cpp}` (`dispatch_op(inner, op, len,
> result, max, OpTransport)`). Сняло дублирование `transfer.cpp::dispatch_op` (NFC)
> и inline-`switch` `ble_channel.cpp` (BLE), которые уже разошлись однажды
> («BLE accepted ACCESS»); результат byte-identical обоим прежним местам. Оба env
> собираются (esp32dev +540 B / esp32dev_ble −188 B flash, RAM 0). **Отложено
> (full X1):** унификация framing-FSM + L2CAP-адаптер — NFC/BLE
> framing/reassembly-ядра и large-filter flash-пути сознательно не тронуты
> (FW-ARC-01/N5/N4/N2, hardware-only).

Зависимости: X2 требует устойчивого BLE (T0) → поэтому после него; N2/B6 — общий streaming-sink, делать вместе; X1 — поверх стабилизированных адаптеров.

---

## 8. Осознанно отклонено (и почему)

- **Extended APDU (до 64 КБ):** PN532 `inDataExchange` — длина `uint8_t` (≤255), физически не отправит; HCE extended-length негарантирован на парке устройств. → reject.
- **Инверсия: телефон-ридер + PN532-target:** target-mode против Android-ридеров ненадёжен, теряется рабочий HCE и tap-UX, выигрыша нет. → reject.
- **L2CAP CoC сейчас:** `createL2capChannel` = **API 29**, а `minSdk=26` (нужен GATT-fallback); **NimBLE-Arduino не экспонирует L2CAP CoC** (только raw ESP-IDF). Хорош как **будущий адаптер** под единым `Transport` (T4), не как немедленная замена. → consider/defer.
- **LESC bonding:** противоречит cleartext-UX, мало пользы (E2E уже закрывает forge/replay), износ NVS, боль iOS, **против relay не помогает**. → reject (кроме как для приватности метаданных, если потребуется явно).
- **UWB secure ranging:** единственное, что физически бьёт relay, но у ESP32 нет UWB. → reject для MVP.
- **Wi-Fi/SoftAP:** throughput решает 127 КБ за <1с, но питание/новый attack-surface неприемлемы для дверей. → consider только для provisioning/field-service (не рутинная доставка).

---

## 9. Связь с общим аудитом (не-транспортные узкие места)

Полный список — в [`07_architecture_review.md`](07_architecture_review.md). Прямо смежные с транспортом:
- `FW-ARC-01` (heap overflow в READ_CHUNK) — закрывается клампом `chunk_len` + fuzzer (T0/X4). ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; кламп `chunk_len`)
- `FW-ARC-03` (фильтр не реверифицируется на буте) — закрывается потоковой подписью (T2, §4.1).
- `FW-ARC-04` (нет watchdog) — N5/B5 (T0). ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; N5 дедлайн NFC-сессии + B5 BLE idle-watchdog закрыты; task-WDT — follow-up)
- `CRYPTO-06` (общий nonce-ring NFC+BLE) — B3 (T0). ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; через B3 — единый доступ к g_state)
- `ANDROID-02` (блокировка на NFC-потоке) — N4 (T0/T2). ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; через N4 — deferred-response + Mutex)
- `ANDROID-11` (позиционная BLE-корреляция) — B4 (T1).
- `CRYPTO-01` (RNG ридера) — влияет на nonce/sealed-box, чинить вместе с транспортной безопасностью.
