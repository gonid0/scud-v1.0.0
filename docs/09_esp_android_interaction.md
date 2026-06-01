# 09. Взаимодействие ESP32 ↔ Android: AS-IS и TO-BE

> Детальное описание обмена между ридером (ESP32) и телефоном (Android) на обоих транспортах
> (NFC HCE и BLE): кто инициатор, какой пакет в какую сторону идёт, что его триггерит.
> **AS-IS** — как сейчас в коде; **TO-BE** — целевая модель после улучшений (план в
> [`08_transport_plan.md`](08_transport_plan.md), узкие места — в [`07_architecture_review.md`](07_architecture_review.md)).

---

## 0. Кто инициатор (важно для направления пакетов)

| | NFC | BLE |
|---|---|---|
| **Ридер (ESP32)** | **активный инициатор** (PN532 reader-mode опрашивает телефон) | **peripheral** (advertise + GATT server) |
| **Телефон** | **пассивный таргет** (HCE — эмулирует карту, *отвечает*) | **central** (сканирует, *подключается*) |
| Кто «двигает» диалог | ридер «тянет» (FETCH/READ_CHUNK) | телефон «толкает» (OP_WRITE), ридер уведомляет (NOTIFY) |

Из-за инверсии направления один и тот же логический обмен на NFC — «ридер опрашивает», а на BLE — «телефон пишет». Семантика операций (что внутри) одинаковая; меняется только обёртка.

---

## 1. Словарь пакетов (что вообще летает)

| Пакет | Размер | Направление | Кем подписан | Назначение |
|---|---|---|---|---|
| **INFO** | 146 B | ридер → телефон | `reader_priv` (§domain_INF) | Кто я, моё время, версия фильтра, `fresh_nonce`, число blacklist |
| **issued_key** | 151 B | (хранится на телефоне) | `server_priv` | Сам пропуск; включается в ACCESS |
| **ACCESS request** | 256 B | телефон → ридер | `phone_priv` (над `fresh_nonce`) | «Открой»: issued_key + подпись телефона |
| **ACCESS_VERDICT** | 42 B | ридер → телефон | — | Решение: OK / EXPIRED / REVOKED / … + next_nonce |
| **time_grant** | 148 B | (хранится на телефоне) | `server_priv` | Право телефона переподписывать время |
| **TIME_SYNC** | ~289 B | телефон → ридер | `phone` (authority) | Поправить часы ридеру |
| **FDI** | 241 B | ридер → телефон → сервер | `reader_priv` | «Какая у меня версия фильтра» (sealed-box серверу) |
| **filter_package** | до ~127 KB | сервер → телефон → ридер | `server_priv` | Bloom-фильтр отзывов + whitelist (**bulk!**) |
| **delivery_receipt** | 112 B | ридер → телефон → сервер | `reader_priv` | «Фильтр vN применил» |
| **GET_BLACKLIST → BLK** | до ~8 KB | ридер → телефон → сервер | `reader_priv` | Локальный blacklist ридера (sealed-box) |
| **REVOKE_KEY** | ~407 B | телефон → ридер | `phone_priv` | Положить ключ в локальный blacklist ридера |
| **passage_receipt** | 192 B (envelope 225) | ридер → телефон → сервер | `reader_priv` | Квитанция о проходе (учёт) |
| **handover_token** *(✅ реализовано, compile-only)* | 167 B | ридер → телефон | `reader_priv` (§domain_BLE) | Привязка BLE-сессии к только что прошедшему NFC-тапу |

---

## 2. Транспортные команды

**NFC wire-команды (APDU, ридер → телефон):**
`0xA4` SELECT AID · `0xC1` PUSH_INFO · `0xC2` FETCH · `0xC3` READ_CHUNK · `0xC4` PUSH_CHUNK · `0xC5` END.

**Inner-операции (телефон формирует в ответ на FETCH):**
`0x01` ACCESS · `0x11` FDI · `0x12` TIME_SYNC · `0x13` FILTER_UPDATE · `0x14` GET_BLACKLIST · `0x15` REVOKE_KEY · `0x16` GET_PASSAGE_RECEIPT.

**BLE характеристики:** `INFO_NOTIFY` · `OP_WRITE` · `RESULT_NOTIFY` · `CONTROL` (RESET/END).

---

## 3. Как телефон решает, что отправить (очередь операций, `TapDecisionTree`)

Получив INFO, телефон строит очередь по содержимому:

```
если есть pending-фильтр новее ридерского  → FILTER_UPDATE (0x13)
если дрейф времени > 15с и есть TimeGrant   → TIME_SYNC (0x12)
всегда                                       → FDI (0x11)
если blacklist_count > 0                     → GET_BLACKLIST (0x14)
если есть pending revoke-intent              → REVOKE_KEY (0x15)
если есть валидный ключ                      → ACCESS (0x01)
после ACCESS=OK                              → GET_PASSAGE_RECEIPT (0x16)
```

---
---

# AS-IS (как сейчас)

## 4. NFC — полный цикл (AS-IS)

### 4.1 Happy path: ACCESS + квитанция
```
РИДЕР (инициатор)                         ТЕЛЕФОН (HCE)
  readPassiveTargetID ───────────────────►
  SELECT AID (00 A4 04 00 F0 53 43 55 44 01) ►
                          ◄─────────────── 90 00
  PUSH_INFO 0xC1 [INFO 146B] ─────────────►   ← телефон верифицирует reader_sig,
                          ◄─────────────── 90 00     строит очередь операций
  FETCH 0xC2 [prev=EMPTY] ────────────────►
                          ◄─────────────── OP_CHUNKED ACCESS (msg_id,total=256,first_chunk≈240)
  READ_CHUNK 0xC3 [msg_id,off=240] ───────►
                          ◄─────────────── [последний chunk, LAST]
  ▸ ридер исполняет op_access → ACCESS_VERDICT(42B); если OK — СРАЗУ открывает замок
  FETCH 0xC2 [prev=VERDICT inline] ───────►   ← результат «подвезён» в следующий FETCH
                          ◄─────────────── OP_SINGLE GET_PASSAGE_RECEIPT (0x16)
  ▸ ридер исполняет op_passage → PASSAGE_ENVELOPE(225B)
  FETCH 0xC2 [prev=PASSAGE] ──────────────►
                          ◄─────────────── NO_OP
  END 0xC5 ───────────────────────────────►
  ▸ field off, cooldown 4.5с
```
Ключевая «изюминка»: **результат предыдущей операции едет внутри следующего FETCH** (`prev_result`) — один round-trip вместо двух.

### 4.2 Bulk: FILTER_UPDATE (проблемный путь)
```
  FETCH 0xC2 [prev=…] ────────────────────►
                          ◄─────────────── OP_CHUNKED FILTER_UPDATE (total=80 000, first_chunk≈240)
  READ_CHUNK off=240 ─────►  ◄── chunk 240
  READ_CHUNK off=480 ─────►  ◄── chunk 240
  …  ≈330 round-trip'ов, телефон держат неподвижно у антенны  …
  ▸▸ AS-IS: total > 16384 → «op too big» → ABORT (фильтр >16КБ не проходил вообще)  ✅ снято (N2/B6, compile-only + host-proven): большой фильтр теперь стримит прямо во flash-слот (см. docs/transport_progress.md); реальный SPIFFS I/O — hardware-required
  ▸ иначе: ридер копит в RAM, верифицирует подпись над всем буфером, A/B-swap → delivery_receipt(112B)
  FETCH 0xC2 [prev=delivery_receipt] ─────►
```
Большой результат (BLK до 8КБ) ридер шлёт **обратной** серией `PUSH_CHUNK 0xC4`, затем `FETCH prev=REFERENCE(msg_id)`.

## 5. BLE — полный цикл (AS-IS)
```
ТЕЛЕФОН (central)                          РИДЕР (peripheral)
  scan → видит "SCUD-XXXXXX" (adv: svc UUID + short_reader_id)
  connectGatt ────────────────────────────►
  discoverServices, requestMtu(247)
  enable notify: INFO_NOTIFY, RESULT_NOTIFY
                          ◄─────────────── INFO push (146B, framed)  ⚠ пушится ДВАЖДЫ (B7) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; INFO один раз)
  ▸ строит очередь (TapDecisionTree)
  OP_WRITE [seq,flags,total | ACCESS] ─────►  ⚠ WRITE_NO_RESPONSE пачкой, без ожидания → дроп кадров (B1) ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; сериализация по onCharacteristicWrite)
                          ◄─────────────── RESULT_NOTIFY [VERDICT framed]  ⚠ сопоставление по позиции (B4)
  OP_WRITE [GET_PASSAGE_RECEIPT] ──────────►
                          ◄─────────────── RESULT_NOTIFY [PASSAGE]
  CONTROL = END ──────────────────────────►
                          ▸ AS-IS: FILTER (>16КБ) тоже не проходил — g_inbound cap 16КБ (B6) ✅ снято (compile-only + host-proven): BLE-реассемблер стримит большой фильтр прямо во flash; NimBLE-flash-стрим — hardware-required (см. docs/transport_progress.md)
```

## 6. Узкие места AS-IS (кратко, детали в 07/08)
- 🔴 Фильтр не пролезал ни по NFC, ни по BLE (потолок 16КБ; streaming не реализован) — `N2/B6`. ✅ **Снято** (compile-only + host-proven): унифицированный flash `op_sink` стримит большой фильтр прямо во flash-слот + two-pass verify-from-flash; реальный SPIFFS/NimBLE-flash I/O — hardware-required (см. docs/transport_progress.md)
- 🔴 BLE теряет кадры (WRITE_NR без подтверждения) — `B1` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md); гонка общего `g_state` NFC↔BLE — `B3` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; доступ к g_state на главном цикле).
- 🔴 READ_CHUNK heap-overflow (нет клампа) — `FW-ARC-01`. ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; кламп chunk_len)
- 🟠 Блокирующий БД/Keystore на NFC-потоке — `ANDROID-02` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; N4 deferred-response); нет дедлайна/watchdog — `N5/B5` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; NFC-дедлайн 8с + BLE idle-watchdog 30с).
- 🟠 Позиционная корреляция результата на BLE — `B4` ✅ **Решено** (compile-only; ветка feature/transport-compile-only — см. docs/transport_progress.md; 1-байтовый `op_seq`-префикс, §16.5.1/§16.6); нет «один клиент» — `B2` ✅ **Решено** (ветка feature/transport-hardening — см. docs/transport_progress.md; single-central).
- 🟠 ACCESS по BLE = relay-риск (нет близости) — `X3` ✅ **Решено** (doc + firmware enforcement, compile-only; ветка feature/transport-compile-only — см. docs/transport_progress.md; ридер отвергает proximity-ops на BLE; §16.8 переписан relay≠replay).

---
---

# TO-BE (как лучше)

## 7. Принципы целевой модели
1. **Маршрутизация по типу пакета:** мелкие/проксимити-операции (ACCESS, FDI, TIME_SYNC, REVOKE, PASSAGE) — **по NFC**; bulk (FILTER_UPDATE, большой BLK) — **по BLE**.
2. **Потоковая верификация фильтра в flash** — снимает 16КБ-потолок (инкрементальный SHA-512, без копления в RAM).
3. **NFC-тап + handover на BLE** для bulk на mains-ридерах (физический тап авторизует BLE-передачу).
4. **Единый framing + `msg_id`-корреляция** на обоих каналах (нет позиционного сопоставления).
5. **Устойчивость:** один активный central, mutex/диспетчер на `g_state`, idle-watchdog, дедлайн сессии, неблокирующий HCE.
6. **Безопасность:** ACCESS-door — только NFC (близость = защита от relay); BLE-only шлагбаум — гейт по датчику присутствия.

## 8. NFC TO-BE — быстрый неблокирующий тап (мелкие операции)
```
РИДЕР                                      ТЕЛЕФОН (HCE)
  SELECT AID ─────────────────────────────►
  PUSH_INFO [INFO + caps + filter_version_hint] ►  ← INFO несёт «умею BLE-bulk», текущую версию фильтра
  FETCH ──────────────────────────────────►   ◄── ACCESS   (байты ГОТОВЫ заранее, вне NFC-потока — N4)
  ▸ op_access → VERDICT; OK → замок (gated)
  FETCH [prev=VERDICT] ───────────────────►   ◄── FDI / TIME_SYNC / REVOKE (по необходимости, все мелкие)
  FETCH [prev=…] ─────────────────────────►   ◄── GET_PASSAGE_RECEIPT → PASSAGE
  ▸ если есть pending-фильтр И телефон/ридер умеют BLE:
        ридер выдаёт HANDOVER_TOKEN (подписан, привязан к fresh_nonce)
  FETCH ──────────────────────────────────►   ◄── NO_OP
  END ─── (sub-second, фильтр на NFC НЕ грузится) ──►
```
Главное отличие: **операции предсобраны на фоне** (HCE-поток только собирает кадр → нет зависимости от таймаута 3.28с), и **тяжёлый фильтр на NFC не идёт** — вместо него выдаётся токен на BLE-докачку.

## 9. NFC→BLE handover (mains-ридер, доставка фильтра) — ✅ реализовано (compile-only + host-proven)
> Token-layout (167 B, marker 0x99, reader-sig над `DOMAIN_BLE ‖ bytes[0:103]`), опкоды
> `INNER_HANDOVER_ISSUE 0x17` / `INNER_HANDOVER_PRESENT 0x18` и привязка к `tap_nonce`
> формализованы в `docs/00 §17.1`; firmware+Android lockstep, golden-вектор в conformance.
> **Host-proven:** layout+sig+binding. **Hardware-only:** реальный two-radio rendezvous
> (NFC-issue → connectGatt к MAC из токена → per-connection authorize). Схема ниже — TO-BE-набросок потока:
```
[NFC]  …тап как в §8, на выходе ридер дал HANDOVER_TOKEN…
[BLE]  ТЕЛЕФОН                              РИДЕР (peripheral)
  connectGatt(ble_addr из INFO/adv) ───────►   (single central)
  OP_WRITE [handover_token] ───────────────►   ▸ ридер верифицирует привязку к tap_nonce
  OP_WRITE FILTER_UPDATE [msg_id, поток чанков, write-with-ack] ►
        ▸ ридер на КАЖДЫЙ чанк: sha512.update + flash_write  (нет 16КБ-потолка!)
        ▸ финал: verify подписи по накопленному хешу → атомарный A/B-swap
                          ◄─────────────── RESULT_NOTIFY [delivery_receipt, msg_id-корр.]
  CONTROL = END ──────────────────────────►   → disconnect → re-advertise
```
Bulk уносится с NFC, но **авторизован физическим тапом** → relay не помогает (а фильтру relay и так не страшен — он подписан сервером и монотонен).

## 10. BLE-only TO-BE (шлагбаум, без NFC)
```
  ридер: advertise 100мс (rotating reader_id + caps + filter_version) [+ опц. iBeacon-wake]
  ТЕЛЕФОН (foreground/geofence) ── auto-connect по RSSI-порогу ──►  РИДЕР (single central)
                          ◄─────────────── INFO (fresh_nonce)
  OP_WRITE ACCESS ────────────────────────►
                          ◄─────────────── VERDICT = OK
  ▸ PRE-AUTH (замок НЕ дёргаем) ──────────────────────────────── pre_authorized_until = now+window
  ▸ когда петля присутствия активна И RSSI ок → ОТКРЫТЬ шлагбаум   ← анти-relay: «кто» по BLE, «здесь» по петле
                          ◄─────────────── passage_receipt (+ опц. session_token для быстрого повторного въезда 60с)
  disconnect → re-advertise
  ▸ FILTER_UPDATE — оппортунистически, когда телефон подключён и НЕподвижен (стоянка/курьер), не в момент проезда
```

## 11. Доставка фильтра (общий streaming-sink) — ✅ реализовано (compile-only + host-proven)
> Унифицированный flash `op_sink` снял 16КБ-потолок (`N2/B6`): большой фильтр стримит
> прямо в неактивный SPIFFS A/B-слот, two-pass verify-from-flash, атомарный свап только
> при валидной подписи; host-тест `test_op_sink.cpp` 8/8. Реальный SPIFFS I/O — hardware-required.
Один потоковый верификатор используется обоими каналами:
```
on_filter_start(header):  sha512_init; sha512_update(R); sha512_update(A); open_inactive_slot()
on_chunk(bytes):          sha512_update(bytes); flash_write(slot, off, bytes)   ← НЕ в RAM
on_filter_end:            h=sha512_final; if ed25519_check(h,S,A): commit_slot else discard
```
Снимает 16КБ-потолок (`N2/B6`) и заодно закрывает «фильтр не реверифицируется на буте» (`FW-ARC-03`).

## 12. Сводка: какой пакет каким каналом

| Пакет | AS-IS | TO-BE | Изменение на проводе? |
|---|---|---|---|
| INFO | NFC и BLE | NFC и BLE (+ caps, filter_version_hint) | да (поля caps) ✅ caps реализованы (compile-only) |
| ACCESS / VERDICT | NFC и BLE | **NFC** (door); BLE только для шлагбаума + presence-gate | политика (§16.8/§18) ✅ ACCESS=NFC реализовано (X3, compile-only); presence-gate шлагбаума — TO-BE |
| FDI / TIME_SYNC / REVOKE | NFC и BLE | **NFC** (мелкие) | нет ✅ роутинг по силе реализован (X2, compile-only) |
| PASSAGE_RECEIPT | NFC и BLE | NFC (или BLE на шлагбауме) | нет |
| FILTER_UPDATE | NFC и BLE (оба режут на 16КБ) | **BLE** (streaming, без потолка) + NFC→BLE handover | да (streaming-семантика) ✅ реализовано (compile-only + host-proven; flash-стрим — hardware-required) |
| GET_BLACKLIST (bulk) | NFC (PUSH_CHUNK) | **BLE** (streaming) | да ✅ роутинг по caps реализован (X2, compile-only) |
| handover_token | — | NFC→телефон (новый) | да (новый пакет, §17.1) ✅ реализовано (compile-only + host-proven; two-radio rendezvous — hardware-required) |
| op/result correlation | позиционно (BLE) | **op_seq** в собранном сообщении (оба) | да (поле op_seq) ✅ реализовано (B4, compile-only) |

> Все строки «да» требуют синхронной правки `docs/00` + backend + firmware + android (shared-инвариант).
> Пометка ✅ — реализовано на ветке `feature/transport-compile-only` (**compile-only / host-proven**, рантайм на железе ещё не верифицирован); L2CAP-адаптер и presence-gate шлагбаума остаются TO-BE.

---

## Итоговая картинка (одним взглядом)

**AS-IS:** оба канала делают всё одинаково → отсюда боль (фильтр не лезет никуда, ACCESS по BLE = relay, дубль кода, гонки).
**TO-BE:** каждый канал — по сильной стороне:
- **NFC** = быстрый проксимити-тап (идентичность, ACCESS, мелкие операции, выдача handover-токена) — неблокирующий, sub-second.
- **BLE** = поток для тяжёлого (фильтр/BLK), авторизованный тапом (handover) или, на шлагбауме, гейтированный датчиком присутствия.
- Единый framing с `msg_id`, потоковая верификация без RAM-потолка, один активный клиент, watchdog'и.
