// BLE channel implementation (shared §16).
//
// Файл имеет два режима:
//   * SCUD_BLE_ENABLED определён → реальный NimBLE peripheral.
//   * Не определён                → no-op stubs. Это позволяет main.cpp
//                                   звать ble_init() без #ifdef-обвязки.
//
// Реальная имплементация:
//   - Service: 5C0DA001-5C0D-4D11-8001-000000000000
//   - INFO_NOTIFY: ридер push'ит 146 B INFO после connect
//   - OP_WRITE:    phone заливает операцию через chunked frames (§16.5)
//   - RESULT_NOTIFY: ридер push'ит результат через chunked frames
//   - CONTROL:    sub-команды (END, RESET)
//
// Безопасность: BLE link cleartext. Наша Ed25519/X25519 поверх — end-to-end.
//
// Размер кода под NimBLE ~3 KB Flash. Все коллбэки выполняются в стек-задаче
// NimBLE host'а (16 KB stack), что отдельно от main loop'а.

#include "ble_channel.h"
#include "ble_frame.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../state/authoritative.h"
#include "../transport/op_sink.h"
#include "../transport/op_dispatch.h"
#include "../transport/transfer.h"   // L1: shared apply_access_result (lock/LED/cooldown)
#include "../ops/ops.h"
#include "../crypto/blake2s.h"
// L1: lock/LED actuation moved into transfer.cpp::apply_access_result — the BLE
// channel no longer touches hw/lock.h or hw/led.h directly.
#include <Arduino.h>
#include <string.h>

#ifdef SCUD_BLE_ENABLED

#include <NimBLEDevice.h>
#include <NimBLEServer.h>
#include <NimBLEService.h>
#include <NimBLECharacteristic.h>
#include <NimBLEAdvertising.h>

namespace {

// 128-bit UUIDs (shared §16.3). NimBLE парсит UUID-строку в LE байты.
constexpr const char* SVC_UUID            = "5c0da001-5c0d-4d11-8001-000000000000";
constexpr const char* CHR_INFO_NOTIFY     = "5c0da001-5c0d-4d11-8001-000000000001";
constexpr const char* CHR_OP_WRITE        = "5c0da001-5c0d-4d11-8001-000000000002";
constexpr const char* CHR_RESULT_NOTIFY   = "5c0da001-5c0d-4d11-8001-000000000003";
constexpr const char* CHR_CONTROL         = "5c0da001-5c0d-4d11-8001-000000000004";

constexpr uint16_t MANUFACTURER_ID = 0xC0DE;   // placeholder

NimBLEServer*         g_server   = nullptr;
NimBLEService*        g_service  = nullptr;
NimBLECharacteristic* g_info_chr = nullptr;
NimBLECharacteristic* g_op_chr   = nullptr;
NimBLECharacteristic* g_res_chr  = nullptr;
NimBLECharacteristic* g_ctl_chr  = nullptr;
NimBLEAdvertising*    g_adv      = nullptr;

// Входящий буфер операции (накапливается из chunked OP_WRITE).
struct InboundOp {
    uint32_t total_len   = 0;
    uint32_t got_len     = 0;
    uint8_t  next_seq    = 0;
    bool     in_progress = false;
    uint8_t  buf[TRANSFER_BUFFER_CAP];
};
InboundOp g_inbound;

// Идентификатор последнего активного клиента (для cleanup).
uint16_t g_conn_handle = BLE_CONN_HANDLE_NONE;

// B4: op↔result корреляция. Сообщение OP_WRITE на прикладном уровне теперь
// [op_seq 1B][op_bytes...]; ридер запоминает op_seq собранной операции и эхо'ит
// его первым байтом сообщения RESULT_NOTIFY ([op_seq 1B][result_bytes...]).
// Это позволяет phone сопоставить result со своим pending-op по id, а не
// позиционно. op_seq — прикладной префикс ВНУТРИ reassembled-сообщения; PDU-
// фрейминг [seq][flags][total_len] не меняется (golden ble_framing валиден).
uint8_t g_op_seq = 0;

// N2/B6 — large FILTER_UPDATE over BLE. H4 Option B': the onWrite callback (NimBLE
// host-task) NO LONGER touches flash AND no longer holds a full-file RAM copy. It
// stages each reassembled PDU body into a fixed-size BLE-private SPSC ring of small
// records and captures the routing prefix + pkg_header. loopTask drains the ring
// (DRAIN_PDUS_PER_TICK records/tick) into the inactive A/B slot — ALL flash work
// (open / per-record write / verify-swap / close) runs on loopTask in the drain
// block of ble_loop_tick + on_flash_op_complete, so every g_slot_file / SPIFFS
// touch is serialized on the single-threaded loop() with NFC run_tap_session (no
// locks — flash is single-writer-on-loopTask, the B3 invariant). The artificial
// 24 KB RAM cap is GONE: the ring carries bytes, not the whole file, so the BLE
// filter rises to the flash ceiling (MAX_FILTER_BYTES gate; ~120 KB real on the
// 256 KB partition).
//
// The reassembled message is [op_seq 1B][inner 1B][courier 16B][filter_package …];
// the host-task keeps the 18-B prefix (routing + receipt) in fi.* and enqueues only
// the package bytes into the ring. ble_reasm_feed (host-tested vs the golden vectors)
// is left UNTOUCHED — this is a parallel path for the oversize case; small ops flow
// through g_inbound.
struct FlashInbound {
    bool     active     = false;   // currently staging a big FILTER_UPDATE
    bool     failed     = false;   // staging/overflow error — drop until reset
    uint32_t total_len  = 0;       // total reassembled message length (with prefix)
    uint32_t got_len    = 0;       // message bytes consumed so far (with prefix)
    uint32_t pkg_len    = 0;       // package bytes written to the slot by loopTask
    uint8_t  next_seq   = 0;       // expected PDU seq
    uint8_t  op_seq     = 0;       // correlation id (message byte 0)
    uint8_t  courier_id[16];       // message bytes [2..17]
    uint8_t  pkg_header[56];       // first 56 B of the filter_package
    uint32_t header_got = 0;       // bytes of pkg_header captured so far
};
FlashInbound g_flash_inbound;

// H4 B' — BLE-private SPSC ring (host-task producer / loopTask consumer). Each slot
// holds ONE reassembled PDU body fragment (the message bytes after the 2-B framing
// header), so the consumer appends it verbatim to the flash sink with no re-parsing.
struct BleFlashRec {
    uint16_t len;                          // valid bytes in payload (0..BLE_RING_REC_CAP)
    uint8_t  flag_last;                    // 1 if this PDU had FLAG_LAST (end of op)
    uint8_t  payload[BLE_RING_REC_CAP];
};
static BleFlashRec       g_ble_ring[BLE_RING_DEPTH];
static volatile uint32_t g_ring_head = 0;   // producer (host-task) writes, consumer reads
static volatile uint32_t g_ring_tail = 0;   // consumer (loopTask) writes, producer reads
// count = head - tail (unsigned wrap-safe); full iff (head - tail) == BLE_RING_DEPTH.

// loopTask-only: true while the inactive A/B slot sink is open mid-drain. Lazy-open
// on the first drained record; closed on FLAG_LAST / drop. Single owner = loopTask.
static bool g_slot_open = false;

// loopTask-owned flash sink for the ring drain. Kept SEPARATE from g_flash_inbound
// (which a host-task disconnect/reset zeroes) so its write() function pointer can
// never be nulled under an in-flight loopTask write. flash_slot_sink_close ignores
// its arg and operates on the file-scope g_slot_file, so g_slot_open is the
// authoritative "a slot is open" flag.
static op_sink g_drain_sink;

// The application-level prefix inside the reassembled BLE message that precedes
// the filter_package: op_seq(1) + inner_opcode(1) + courier_id(16).
constexpr uint32_t FLASH_MSG_PREFIX = 1u + 1u + 16u;

// B3: коллбэки NimBLE крутятся на host-task, а NFC и cleanup'ы — на главном цикле.
// Чтобы op-хендлеры и nonce-ring (всё это в g_state) не гонялись между задачами,
// NimBLE-task только наполняет g_inbound (BLE-приватный) и взводит эти флаги;
// фактический доступ к g_state делает ble_loop_tick на главном цикле.
volatile bool g_ble_pending_info = false;   // надо запушить INFO новой сессии
volatile bool g_ble_op_ready     = false;   // входящая операция собрана целиком
volatile uint32_t g_ble_connect_ms = 0;
volatile uint32_t g_ble_last_act_ms = 0;    // B5: время последней активности сессии

// ---------------------------------------------------------------------------
// Framing helpers (§16.5). PDU = [seq 1][flags 1][total_len 4B LE on first][chunk].
// The produce-side chunking math lives in the pure ble_frame module (ble_frame.h),
// host-tested against the golden vectors; the FLAG_* constants come from there.
// ---------------------------------------------------------------------------
constexpr uint8_t FLAG_LAST      = BLE_FLAG_LAST;
constexpr uint8_t FLAG_HAS_TOTAL = BLE_FLAG_HAS_TOTAL;

// H4 B': HOST-SAFE reset. Drops any in-flight staged FILTER_UPDATE by zeroing the
// BLE-private FlashInbound state. It does NOT touch the ring indices (g_ring_tail is
// loopTask-exclusive) and NEVER calls flash_slot_sink_close — the host-task never
// opens a sink, so there is nothing to close here. The sole flash_slot_sink_close
// owner AND the sole ring-index reset owner is the loop path (the drain block /
// on_flash_op_complete). Safe to call from onConnect / onDisconnect /
// ControlCallbacks (all host-task) without racing a flash handle or g_ring_tail the
// loop might be using. On a host-task drop (seq-gap / overflow) the producer sets
// fi.failed=true; the consumer observes it and performs the ring reset + sink close.
void flash_reset_ram_only() {
    g_flash_inbound = {};
}

void reset_inbound() {
    g_inbound = {};
    flash_reset_ram_only();
}

// Sink that notifies one PDU on a characteristic (RESULT_NOTIFY / INFO_NOTIFY).
static void notify_pdu_sink(void* ctx, const uint8_t* pdu, size_t len) {
    NimBLECharacteristic* chr = static_cast<NimBLECharacteristic*>(ctx);
    chr->setValue(pdu, len);
    chr->notify();
}

// Push большой результат/INFO через chunked framing (§16.5). max_pdu 240 безопасен
// при negotiated MTU 247 (оставляет ATT header). Сама нарезка — в ble_frame_message.
void notify_chunked(NimBLECharacteristic* chr, const uint8_t* data, uint32_t len) {
    ble_frame_message(data, len, /*max_pdu=*/BLE_MAX_PDU, notify_pdu_sink, chr);
}

// B4: result framed как [op_seq 1B][result_bytes...] — phone стрипает op_seq и
// сопоставляет с pending-op. op_seq берётся из только что собранной операции
// (g_op_seq). Префикс кладём в один буфер, чтобы фрейминг разрезал [op_seq‖result]
// как единое сообщение (PDU-фрейминг неизменён).
void notify_chunked_result(uint8_t op_seq, const uint8_t* data, uint32_t len) {
    static uint8_t framed[1 + RESULT_BUF_CAP];
    if (len > RESULT_BUF_CAP) return;   // не должно случаться: result_buf == RESULT_BUF_CAP
    framed[0] = op_seq;
    memcpy(framed + 1, data, len);
    notify_chunked(g_res_chr, framed, len + 1);
}

void push_info_after_connect() {
    uint8_t info[146];
    if (build_info_bytes(info) != 146) return;
    // B7: INFO — единым framed-сообщением (§16.5) на INFO_NOTIFY, как и ждёт
    // Android (infoChannel reassembles framed; при MTU≥155 — один PDU). Раньше
    // слалось дважды и несогласованно: framed на RESULT_NOTIFY (отравлял
    // resultChannel первого op) + raw на INFO_NOTIFY (Android-фреймер не мог
    // распарсить → connect зависал до таймаута).
    // B4: INFO — unsolicited push, НЕ ответ на op → БЕЗ op_seq-префикса (его несут
    // только OP_WRITE / RESULT_NOTIFY). INFO идёт по своему infoChannel-пути.
    notify_chunked(g_info_chr, info, 146);
}

// Восстановили операцию полностью — диспатчим в существующие ops.
void on_op_complete() {
    // B4: reassembled-сообщение OP_WRITE = [op_seq 1B][op_bytes...]. Нужен хотя бы
    // op_seq + 1 байт op (inner_opcode).
    if (!g_inbound.in_progress || g_inbound.got_len < 2) {
        reset_inbound();
        return;
    }
    // B4: первый байт — корреляционный op_seq (НЕ часть op). Запоминаем для эха
    // в RESULT_NOTIFY; op-хендлер видит op БЕЗ префикса (buf+1, длина -1).
    uint8_t        op_seq    = g_inbound.buf[0];
    const uint8_t* op        = g_inbound.buf + 1;
    uint32_t       op_len    = g_inbound.got_len - 1;
    g_op_seq = op_seq;
    uint8_t  inner_opcode = op[0];
    static uint8_t result_buf[RESULT_BUF_CAP];
    // X1 (partial): the per-op dispatch + transport policy is now the shared
    // transport/op_dispatch.cpp::dispatch_op (single source of truth). It runs in
    // the SAME context the inline switch did (the main loop via ble_loop_tick —
    // the B3 g_state main-loop-only access model is unchanged). With
    // OP_TRANSPORT_BLE it reproduces the former switch byte-for-byte: the X3
    // proximity-op rejects, the T3 handover gate on FILTER_UPDATE, BLACKLIST,
    // HANDOVER_PRESENT/ISSUE. op_seq handling + notify_chunked_result below stay
    // untouched, as does the large-filter flash route (on_flash_op_complete).
    size_t result_len = dispatch_op(inner_opcode, op, op_len,
                                    result_buf, sizeof(result_buf), OP_TRANSPORT_BLE);
    // Behavior-preserving: the former inline switch logged "unknown inner_opcode"
    // ONLY from its default case (a genuinely unrecognized opcode), NOT when a
    // known handler returned 0 (malformed payload / short buffer). dispatch_op
    // returns 0 in both situations, so gate the log on the opcode being outside
    // the known/handled set — matching the old switch's default arm exactly.
    // H3: INNER_PASSAGE (0x16) dropped from the known set (op retired). H1: FDI /
    // TIME_SYNC / REVOKE_KEY now have real handlers on BLE too (no X3 reject), so
    // they stay in the known set exactly as before.
    if (result_len == 0 &&
        inner_opcode != INNER_ACCESS && inner_opcode != INNER_FDI &&
        inner_opcode != INNER_TIME_SYNC && inner_opcode != INNER_REVOKE_KEY &&
        inner_opcode != INNER_FILTER_UPDATE &&
        inner_opcode != INNER_BLACKLIST && inner_opcode != INNER_HANDOVER_PRESENT &&
        inner_opcode != INNER_HANDOVER_ISSUE) {
        Serial.printf("[BLE] unknown inner_opcode 0x%02X\n", inner_opcode);
    }
    if (result_len > 0) {
        // B4: эхо op_seq собранной операции первым байтом RESULT_NOTIFY-сообщения.
        notify_chunked_result(op_seq, result_buf, (uint32_t)result_len);
    }
    // ACCESS по BLE: дёргаем замок/LED/cooldown через ЕДИНЫЙ хелпер, общий с
    // NFC-путём (transfer.cpp::apply_access_result). Это и закрывает L1: BLE-путь
    // теперь ТОЖЕ ставит cooldown (раньше не ставил → подключённый телефон мог
    // перевзводить замок в тихое окно). on_op_complete крутится на главном цикле
    // (ble_loop_tick дренит g_ble_op_ready), НЕ в host-task коллбэке, поэтому
    // доступ к g_state / g_cooldown_until_ms внутри хелпера main-loop-only (B3-safe).
    // X3-reject для ACCESS снят → BLE-ACCESS штатный.
    apply_access_result(result_buf, result_len, inner_opcode);
    reset_inbound();
}

// N2/B6 + H4 B' — stage ONE reassembled PDU body (the message bytes, PDU header
// already stripped) into the BLE-private SPSC ring. The message is
// [op_seq 1][inner 1][courier 16][filter_package …]; the host-task keeps the 18-B
// prefix (op_seq + courier_id) in fi.* and captures the first 56 package bytes into
// fi.pkg_header (for routing/validation on loopTask). The package bytes of THIS body
// become the enqueued record payload; the consumer (loopTask) appends them verbatim
// to the flash sink. NO flash here — open/write/close all run on loopTask. Returns
// false on ring-full or an over-cap body (caller drops the run via fi.failed). Runs
// on the NimBLE host-task; touches only BLE-private RAM (no g_state, no g_slot_file).
// `flag_last` is the FLAG_LAST bit of this PDU (carried into the record so the
// consumer finishes the op when it drains the last record).
static bool flash_consume(const uint8_t* body, uint32_t blen, bool flag_last) {
    FlashInbound& fi = g_flash_inbound;
    uint32_t i = 0;
    // Drain the 18-B prefix (op_seq + inner + courier_id) byte-by-byte; it is
    // short and only crossed once. These bytes are NOT part of the package and are
    // NOT enqueued — only fi.* metadata is captured here.
    while (i < blen && fi.got_len < FLASH_MSG_PREFIX) {
        uint32_t pos = fi.got_len;
        uint8_t  b   = body[i];
        if (pos == 0) fi.op_seq = b;
        else if (pos >= 2 && pos < 2 + 16) fi.courier_id[pos - 2] = b;
        fi.got_len++;
        i++;
    }
    // The remainder of this body is all package bytes → capture the package header
    // (first 56 B, for loopTask routing) without consuming, then enqueue them.
    const uint8_t* pkg = body + i;
    uint32_t plen = blen - i;
    if (plen > 0 && fi.header_got < 56) {
        uint32_t take = (56 - fi.header_got) < plen ? (56 - fi.header_got) : plen;
        memcpy(fi.pkg_header + fi.header_got, pkg, take);
        fi.header_got += take;
    }
    // A single PDU body can never exceed the ring record cap (negotiated maxPdu - 2
    // <= BLE_RING_REC_CAP); guard defensively so a malformed oversize body cannot
    // overrun the record payload.
    if (plen > (uint32_t)BLE_RING_REC_CAP) {
        fi.failed = true;
        return false;
    }
    // SPSC enqueue (producer = host-task). Read our own head + the consumer-owned
    // tail; if full, drop the run (bounded, no overrun — see backpressure).
    uint32_t head = g_ring_head;
    uint32_t tail = g_ring_tail;          // volatile: written by consumer
    if ((uint32_t)(head - tail) == BLE_RING_DEPTH) {
        fi.failed = true;                 // ring full → NAK-by-drop on loopTask
        return false;
    }
    BleFlashRec& r = g_ble_ring[head & (BLE_RING_DEPTH - 1)];
    if (plen > 0) memcpy(r.payload, pkg, plen);
    r.len       = (uint16_t)plen;
    r.flag_last = flag_last ? 1 : 0;
    __sync_synchronize();                 // publish record bytes BEFORE advancing head
    g_ring_head = head + 1;               // single volatile store; consumer now sees it
    fi.got_len += plen;
    return true;
}

// H4 B' — FINALIZE a fully-drained big FILTER_UPDATE on loopTask. The inactive A/B
// slot (g_drain_sink → g_slot_file) is already OPEN and holds the streamed package
// (fi.pkg_len bytes, written record-by-record by drain_flash_ring). This runs the
// min-size gate, verify-from-flash + atomic swap, close, notify, and reset — NO
// scratch buffer, NO bulk write (the bytes are already in the slot).
void on_flash_op_complete() {
    FlashInbound& fi = g_flash_inbound;
    static uint8_t result_buf[RESULT_BUF_CAP];
    if (!g_slot_open) { reset_inbound(); return; }   // defensive: nothing to finalize

    // Lower bound: 56-B package header + 64-B signature. The UPPER bound was
    // pre-screened in onWrite against MAX_FILTER_BYTES (the real flash ceiling) —
    // there is no RAM cap here, the 24 KB BLE_FLASH_STAGE_CAP is gone.
    if (fi.pkg_len < 56u + 64u) {
        Serial.printf("[BLE] flash FILTER_UPDATE too small %u — reject\n",
                      (unsigned)fi.pkg_len);
        flash_slot_sink_close(&g_drain_sink);
        g_slot_open = false;
        size_t rl = make_op_result(result_buf, INNER_FILTER_UPDATE,
                                   MARK_OP_RESULT_FLT, RES_BAD_FORMAT, nullptr, 0);
        notify_chunked_result(fi.op_seq, result_buf, (uint32_t)rl);
        reset_inbound();
        return;
    }

    g_op_seq = fi.op_seq;
    size_t result_len = op_filter_update_from_flash(fi.courier_id, fi.pkg_header, 56,
                                                    &g_drain_sink, result_buf, sizeof(result_buf));
    flash_slot_sink_close(&g_drain_sink);
    g_slot_open = false;
    if (result_len > 0) {
        notify_chunked_result(fi.op_seq, result_buf, (uint32_t)result_len);
    }
    reset_inbound();
}

// Notify a single FILTER_UPDATE error result so a failed flash stream does not leave
// the phone hanging until the idle-watchdog fires.
static void notify_flt_error(uint8_t op_seq, uint8_t code) {
    static uint8_t rb[RESULT_BUF_CAP];
    size_t rl = make_op_result(rb, INNER_FILTER_UPDATE, MARK_OP_RESULT_FLT,
                               code, nullptr, 0);
    notify_chunked_result(op_seq, rb, (uint32_t)rl);
}

// H4 B' — DRAIN the BLE FILTER_UPDATE ring on loopTask (consumer). Lazily opens the
// inactive A/B slot on the first record (the first and only g_slot_file touch — on
// loopTask), writes each drained record's package bytes to it, and finalizes
// (verify + swap + close) when the FLAG_LAST record is drained. ALL flash access is
// here on loopTask, sequential with NFC run_tap_session in loop() — single writer,
// so the H4 cross-task race is closed BY CONSTRUCTION (no atomic, no claim). Bounded
// to DRAIN_PDUS_PER_TICK records/tick so a tick stays within the NFC time budget.
static void drain_flash_ring() {
    FlashInbound& fi = g_flash_inbound;

    // Abandoned op: a host-task disconnect/idle-reset zeroed g_flash_inbound
    // (active=false) while the slot was left open mid-drain. Close it here (loopTask
    // is the sole flash owner; flash_slot_sink_close ignores its arg and closes the
    // file-scope g_slot_file) and discard any queued records. The reconnect handshake
    // (>> one loop tick) means this runs before any new op reuses the slot; if it
    // ever didn't, stale bytes fail signature verify → fail-closed, no swap.
    if (g_slot_open && !fi.active) {
        flash_slot_sink_close(&g_drain_sink);
        g_slot_open  = false;
        g_ring_tail  = g_ring_head;
        return;
    }
    // Producer flagged a fatal staging error (ring-full / seq-gap / oversize body):
    // fail-closed — close a half-open slot, drop queued records, notify, reset.
    if (fi.failed) {
        uint8_t op_seq = fi.op_seq;
        if (g_slot_open) { flash_slot_sink_close(&g_drain_sink); g_slot_open = false; }
        g_ring_tail = g_ring_head;
        reset_inbound();
        notify_flt_error(op_seq, RES_INTERNAL_ERROR);
        return;
    }

    uint32_t drained = 0;
    while (drained < DRAIN_PDUS_PER_TICK && g_ring_tail != g_ring_head) {
        __sync_synchronize();               // observe the producer's published record
        BleFlashRec& r = g_ble_ring[g_ring_tail & (BLE_RING_DEPTH - 1)];

        if (!g_slot_open) {
            // T3 gate (plan 08 §4.3) BEFORE opening — an unauthorized op writes no
            // flash (fail-closed), identical to the small path in on_op_complete.
            if (g_state.cfg.handover_required && !g_state.handover_authorized) {
                Serial.println("[BLE] flash FILTER_UPDATE rejected: handover required");
                uint8_t op_seq = fi.op_seq;
                g_ring_tail = g_ring_head;
                reset_inbound();
                notify_flt_error(op_seq, RES_NOT_AUTHORIZED);
                return;
            }
            if (!flash_slot_sink_open(&g_drain_sink)) {
                Serial.println("[BLE] flash slot open failed — reject FILTER_UPDATE");
                uint8_t op_seq = fi.op_seq;
                g_ring_tail = g_ring_head;
                reset_inbound();
                notify_flt_error(op_seq, RES_INTERNAL_ERROR);
                return;
            }
            g_slot_open = true;
            fi.pkg_len  = 0;
        }

        if (r.len > 0 && !g_drain_sink.write(&g_drain_sink, r.payload, r.len)) {
            Serial.println("[BLE] flash slot write failed (SPIFFS full?) — reject");
            flash_slot_sink_close(&g_drain_sink);
            g_slot_open = false;
            uint8_t op_seq = fi.op_seq;
            g_ring_tail = g_ring_head;
            reset_inbound();
            notify_flt_error(op_seq, RES_INTERNAL_ERROR);
            return;
        }
        fi.pkg_len += r.len;
        bool last = (r.flag_last != 0);
        g_ring_tail = g_ring_tail + 1;      // consume one record (single consumer)
        drained++;

        if (last) {
            on_flash_op_complete();         // verify + swap + close + notify + reset
            return;
        }
    }
}

// ---------------------------------------------------------------------------
// Callbacks
// ---------------------------------------------------------------------------
class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* server, ble_gap_conn_desc* desc) override {
        // B2: ровно один активный central. Если кто-то уже подключён — отклоняем
        // нового, иначе его OpWrite-кадры интерливятся в общий g_inbound и портят сборку.
        if (g_conn_handle != BLE_CONN_HANDLE_NONE) {
            Serial.printf("[BLE] reject 2nd central handle=%u (busy=%u)\n",
                          desc->conn_handle, g_conn_handle);
            server->disconnect(desc->conn_handle);
            return;
        }
        // H4 B': no channel-busy claim — every flash access (NFC run_tap_session and
        // the BLE ring drain) runs on loopTask, sequentially in loop(), so a BLE
        // connect during an NFC tap is harmless (no shared-flash concurrency).
        g_conn_handle = desc->conn_handle;
        reset_inbound();
        // T3: a fresh connection starts UNauthorized — the handover gate (if armed)
        // requires a verified INNER_HANDOVER_PRESENT on THIS connection. Resetting
        // here (and on disconnect) makes authorization strictly per-connection.
        g_state.handover_authorized = false;
        Serial.printf("[BLE] connect handle=%u (g_state deferred to loop)\n", g_conn_handle);
        // B3: НЕ трогаем g_state и не пушим INFO в host-task коллбэке — это сделает
        // ble_loop_tick на главном цикле (последовательно с NFC), убирая гонку.
        // INFO пушим НЕ здесь по таймеру (он гонялся вперёд подписки телефона → push
        // терялся, телефон вис на "awaiting INFO"), а по факту onSubscribe RESULT_NOTIFY
        // (NotifySubscribeCallbacks ниже).
        g_ble_connect_ms   = millis();
        g_ble_last_act_ms  = g_ble_connect_ms;   // B5: старт idle-таймера
    }
    void onDisconnect(NimBLEServer* /*server*/, ble_gap_conn_desc* /*desc*/) override {
        Serial.println("[BLE] disconnect — re-start advertising");
        g_conn_handle = BLE_CONN_HANDLE_NONE;
        reset_inbound();
        g_state.handover_authorized = false;   // T3: drop per-connection authorization
        NimBLEDevice::startAdvertising();
    }
};

class OpWriteCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* chr) override {
        std::string value = chr->getValue();
        const uint8_t* d = (const uint8_t*)value.data();
        size_t n = value.size();
        if (n < 2) return;
        g_ble_last_act_ms = millis();   // B5: активность сессии

        uint8_t seq   = d[0];
        uint8_t flags = d[1];
        bool    is_first = (flags & FLAG_HAS_TOTAL) != 0;

        // N2/B6 + H4 B': route a large FILTER_UPDATE to the SPSC ring. Detect on the
        // first PDU: it carries total_len @2 and the message head; message byte 1
        // (after op_seq) is the inner_opcode. A package over the RAM op cap is staged
        // PDU-by-PDU into the ring (NO flash here — drained/written on loopTask).
        // Small ops (and small filters) fall through to ble_reasm_feed.
        if (is_first && n >= 8) {
            uint32_t total_len = read_u32le(d + 2);
            uint8_t  inner     = d[7];   // d[6]=op_seq, d[7]=inner_opcode
            if (inner == INNER_FILTER_UPDATE && total_len > TRANSFER_BUFFER_CAP) {
                flash_reset_ram_only();
                FlashInbound& fi = g_flash_inbound;
                uint32_t pkg_len = total_len > FLASH_MSG_PREFIX ? total_len - FLASH_MSG_PREFIX : 0;
                // Cheap host-side pre-screen on the declared size: reject obviously
                // out-of-range messages before staging. The real ceiling is the flash
                // partition (MAX_FILTER_BYTES); the 24 KB RAM cap is GONE. The
                // authoritative size + open/write policy runs on loopTask
                // (on_flash_op_complete). NO flash_slot_sink_open here.
                if (total_len < FLASH_MSG_PREFIX + 56u + 64u ||
                    pkg_len > (uint32_t)MAX_FILTER_BYTES) {
                    Serial.println("[BLE] FILTER_UPDATE flash route rejected — reset");
                    flash_reset_ram_only();
                    return;
                }
                fi.active    = true;
                fi.total_len = total_len;
                fi.next_seq  = 1;
                // The ring is loopTask-owned; a fresh run cannot legitimately begin
                // until the prior op's consumer reset the indices. Enqueue the first
                // body; the consumer (drain block) lazily resets indices on a stale
                // disconnect via the !active guard before this op went active.
                if (!flash_consume(d + 6, (uint32_t)(n - 6), (flags & FLAG_LAST) != 0)) {
                    Serial.println("[BLE] flash ring enqueue failed (full?) — drop");
                    // fi.failed already set by flash_consume; the consumer cleans up.
                    return;
                }
                return;
            }
        }

        // N2/B6 + H4 B': continuation PDUs of an in-flight ring-staged stream. Strip
        // the 2-B PDU header and enqueue the message body; enforce seq order. Still
        // NO flash on the host-task.
        if (g_flash_inbound.active) {
            FlashInbound& fi = g_flash_inbound;
            if (fi.failed) { return; }            // already errored; consumer will clean up
            if (seq != fi.next_seq) {
                Serial.println("[BLE] flash stream seq gap — drop");
                fi.failed = true;                 // consumer resets ring + closes slot
                return;
            }
            if (!flash_consume(d + 2, (uint32_t)(n - 2), (flags & FLAG_LAST) != 0)) {
                Serial.println("[BLE] flash ring enqueue failed (full?) — drop");
                // fi.failed already set by flash_consume; the consumer cleans up.
                return;
            }
            fi.next_seq++;
            return;
        }

        // Реассемблинг [seq][flags][total_len] — в чистом ble_reasm_feed (host-тестится
        // против golden ble_framing, симметрично notify_chunked). g_inbound —
        // BLE-приватный накопитель; on_op_complete читает его на главном цикле.
        ble_reasm_status rs = ble_reasm_feed(d, n, g_inbound.buf, sizeof(g_inbound.buf),
                                             &g_inbound.total_len, &g_inbound.got_len,
                                             &g_inbound.next_seq, &g_inbound.in_progress);
        if (rs == BLE_REASM_ERROR) {
            // seq-gap / overflow / oversize — состояние уже сброшено внутри feed.
            Serial.println("[BLE] OP reassembly error — reset inbound");
            return;
        }
        if (rs == BLE_REASM_DONE) {
            // B3: не диспатчим op-хендлер здесь (host-task) — данные уже в g_inbound,
            // обработает ble_loop_tick на главном цикле. Барьер публикует заполненный
            // буфер до взвода флага (g_inbound — internal SRAM, когерентна между ядрами).
            __sync_synchronize();
            g_ble_op_ready = true;
        }
    }
};

class ControlCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* chr) override {
        std::string value = chr->getValue();
        if (value.empty()) return;
        switch ((uint8_t)value[0]) {
            case 0x01:  // RESET
                Serial.println("[BLE] CONTROL=RESET — flush inbound");
                reset_inbound();
                break;
            case 0x02:  // END
                Serial.println("[BLE] CONTROL=END — disconnect");
                if (g_server && g_conn_handle != BLE_CONN_HANDLE_NONE) {
                    g_server->disconnect(g_conn_handle);
                }
                break;
            default:
                Serial.printf("[BLE] CONTROL unknown 0x%02X\n",
                              (uint8_t)value[0]);
                break;
        }
    }
};

// INFO push trigger: fire when the central ENABLES notifications on RESULT_NOTIFY
// (the second CCCD it writes). Earlier the INFO push ran on a post-connect timer
// (ble_info_defer ≈ 50–100 ms), but a phone enables notify only ~500 ms after connect
// (exchangeMTU + service discovery + two CCCD writes) — so the single INFO notification
// raced AHEAD of the subscription and was lost, and the phone hung on "awaiting INFO".
// Event-driven push is robust regardless of phone speed. Runs on the NimBLE host-task →
// only sets volatile flags; ble_loop_tick (main loop) does the actual push touching
// g_state (B3 — no cross-task g_state race).
class NotifySubscribeCallbacks : public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic* /*chr*/, ble_gap_conn_desc* /*desc*/, uint16_t subValue) override {
        if (subValue & 0x0001) {           // bit0 = notifications enabled
            g_ble_connect_ms   = millis();  // (ре)база небольшого ble_info_defer от подписки
            g_ble_pending_info = true;      // ble_loop_tick запушит INFO на главном цикле
        }
    }
};

ServerCallbacks          g_server_cbs;
OpWriteCallbacks         g_op_cbs;
ControlCallbacks         g_ctl_cbs;
NotifySubscribeCallbacks g_notify_subscribe_cbs;

}  // namespace

bool ble_compiled_in() { return true; }

bool ble_init() {
    // B8: рантайм-gate (g_state.ble_enabled, NVS scud_imm:ble_en) проверяется в
    // main.cpp перед вызовом ble_init — сюда не доходим при ble_en=false.

    // B10: short_reader_id = первые 6 B от BLAKE2s(reader_id) (§16.2). Приватность:
    // пассивный сканер не видит реальные байты reader_id. Один и тот же source для
    // device-name и manufacturer-data, чтобы UI был консистентен.
    uint8_t short_reader_id[6];
    {
        uint8_t h[16];
        blake2s_state S;
        blake2s_init(&S, 16);
        blake2s_update(&S, g_state.reader_id, 16);
        blake2s_final(&S, h, 16);
        memcpy(short_reader_id, h, 6);
    }

    char device_name[BLE_DEVICE_NAME_BUF];
    // SCUD-XXXXXX (первые 3 байта short_reader_id → 6 hex).
    snprintf(device_name, sizeof(device_name), BLE_DEVICE_NAME_PREFIX "%02X%02X%02X",
             short_reader_id[0], short_reader_id[1], short_reader_id[2]);

    NimBLEDevice::init(device_name);
    NimBLEDevice::setMTU((uint16_t)g_state.cfg.ble_mtu);

    // T3 (shared §17.1): capture the local BLE MAC so op_handover_issue can bind it
    // into the handover_token (reader_ble_addr). NimBLEAddress stores 6 B native
    // (LE); copy as-is — the token field is opaque to the reader (the phone uses it
    // to know which MAC to connect to). Available only once the stack is up.
    {
        NimBLEAddress addr = NimBLEDevice::getAddress();
        memcpy(g_state.reader_ble_addr, addr.getNative(), 6);
    }

    g_server = NimBLEDevice::createServer();
    g_server->setCallbacks(&g_server_cbs);

    g_service = g_server->createService(SVC_UUID);

    g_info_chr = g_service->createCharacteristic(
        CHR_INFO_NOTIFY, NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ);
    g_op_chr   = g_service->createCharacteristic(
        CHR_OP_WRITE,    NIMBLE_PROPERTY::WRITE_NR | NIMBLE_PROPERTY::WRITE);
    g_op_chr->setCallbacks(&g_op_cbs);
    g_res_chr  = g_service->createCharacteristic(
        CHR_RESULT_NOTIFY, NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ);
    g_res_chr->setCallbacks(&g_notify_subscribe_cbs);   // push INFO когда телефон включил notify
    g_ctl_chr  = g_service->createCharacteristic(
        CHR_CONTROL,     NIMBLE_PROPERTY::WRITE);
    g_ctl_chr->setCallbacks(&g_ctl_cbs);

    g_service->start();

    g_adv = NimBLEDevice::getAdvertising();
    // ADV packet carries the 128-bit Service UUID so the phone's
    // ScanFilter.setServiceUuid() matches at the OS level. Prior bug:
    // setAdvertisementData() below replaced the helper-built payload and DROPPED the
    // UUID, so the phone (which filters by UUID) never saw the reader. Name is NOT in
    // ADV — flags(3)+128-bit UUID(18)=21 B leaves no room for the 13-B name under the
    // 31-B cap; name + manufacturer data ride the SCAN RESPONSE instead.
    g_adv->addServiceUUID(SVC_UUID);

    // Manufacturer-specific data layout (§16.2, X2). 9 B of mfg-specific payload,
    // unsigned and outside the conformance corpus (only short_reader_id is a hash):
    //   [0..1]  MANUFACTURER_ID (0xC0DE, LE)
    //   [2..7]  short_reader_id = BLAKE2s(reader_id, 16)[0:6]  (B10, privacy)
    //   [8]     capability flags: bit0 BLE_CAP_BULK, bit1 BLE_CAP_HANDOVER (rest reserved=0)
    // The phone routes bulk ops (FILTER_UPDATE/GET_BLACKLIST) to BLE iff BLE_CAP_BULK
    // is set, while proximity ops stay on NFC (see Android TransportRouter / §16.2).
    uint8_t mfg[9];
    write_u16le(mfg + 0, MANUFACTURER_ID);
    memcpy(mfg + 2, short_reader_id, 6);   // B10: BLAKE2s(reader_id)[0:6] (§16.2)
    // Reaching ble_init() means the BLE channel is up → advertise BLE_CAP_BULK.
    // T3: this build implements NFC→BLE handover (op_handover_issue/present, shared
    // §17.1) → also advertise BLE_CAP_HANDOVER so the phone knows it can hand off.
    mfg[8] = BLE_CAP_BULK | BLE_CAP_HANDOVER;
    std::string mfg_str((const char*)mfg, sizeof(mfg));

    // SCAN RESPONSE (separate 31-B budget): manufacturer data ONLY (short_reader_id +
    // caps, §16.2). Returned on an active scan (Android SCAN_MODE_BALANCED) and merged
    // into the same scanRecord the phone parses via getManufacturerSpecificData(0xC0DE).
    // The local name "SCUD-XXXXXX" is deliberately NOT advertised: a named, connectable
    // device shows a friendly entry in generic OS Bluetooth lists. Dropping the BLE name
    // keeps the reader out of those casual lists (privacy/stealth) — the SCUD app still
    // discovers it by Service UUID (ADV) + this mfg data and DISPLAYS a name derived from
    // short_reader_id, so nothing in-app breaks. (It stays connectable for the courier,
    // so it cannot be fully invisible like a non-connectable AirTag beacon.)
    // Do NOT call setAdvertisementData() here — it sets m_customAdvData and would again
    // drop the Service UUID from the ADV packet (the bug that hid the reader from the
    // phone's UUID ScanFilter).
    NimBLEAdvertisementData scan_rsp;
    scan_rsp.setManufacturerData(mfg_str);
    g_adv->setScanResponseData(scan_rsp);

    // Power: intermittent advertising (AirTag-style) instead of the NimBLE fast
    // default (~20-40 ms). The interval is a property of g_adv and persists across
    // stop/start, so the onDisconnect re-advertise path inherits it — set once here.
    // Trade-off: the phone scanner takes up to ~1.5 s to discover the reader.
    g_adv->setMinInterval(BLE_ADV_MIN_INTERVAL_UNITS);
    g_adv->setMaxInterval(BLE_ADV_MAX_INTERVAL_UNITS);

    NimBLEDevice::startAdvertising();

    Serial.printf("[BLE] peripheral up, name=%s svc=%s\n",
                  device_name, SVC_UUID);
    return true;
}

void ble_loop_tick() {
    // B3: единственное место, где BLE трогает g_state — на главном цикле,
    // последовательно с NFC run_tap_session и cleanup'ами (нет гонки задач).
    if (g_ble_pending_info && (uint32_t)(millis() - g_ble_connect_ms) >= g_state.cfg.ble_info_defer) {
        g_ble_pending_info = false;
        // H3 FOLD-IN: no passage cache to reset (the receipt rides the
        // ACCESS_VERDICT tail now).
        g_state.session_seq++;
        push_info_after_connect();
    }
    if (g_ble_op_ready) {
        g_ble_op_ready = false;
        __sync_synchronize();   // забираем опубликованный onWrite'ом g_inbound
        on_op_complete();
    }
    // H4 B': drain the BLE FILTER_UPDATE ring into the inactive A/B slot here on the
    // main loop (sequential with NFC run_tap_session + all g_state access). Every
    // flash touch (open / write / verify-swap / close) lives on loopTask — single
    // writer, so there is no cross-task race with the NFC flash path and no atomic
    // or channel claim is needed.
    drain_flash_ring();
    // B5: idle-watchdog (§16.6) — рвём зависшую/молчащую сессию, освобождая
    // single-central слот и общие буферы. disconnect() безопасно вызывать с
    // главного цикла; onDisconnect (host-task) дочистит state и поднимет adv.
    if (g_conn_handle != BLE_CONN_HANDLE_NONE && g_server &&
        (uint32_t)(millis() - g_ble_last_act_ms) > g_state.cfg.ble_idle_ms) {
        Serial.println("[BLE] idle timeout — disconnect");
        g_server->disconnect(g_conn_handle);
    }
}

void ble_shutdown() {
    if (g_adv) g_adv->stop();
    NimBLEDevice::deinit(false);
    g_server   = nullptr;
    g_service  = nullptr;
    g_info_chr = nullptr;
    g_op_chr   = nullptr;
    g_res_chr  = nullptr;
    g_ctl_chr  = nullptr;
    g_adv      = nullptr;
}

#else  // SCUD_BLE_ENABLED не определён → stubs

bool ble_compiled_in() { return false; }
bool ble_init()        { return false; }
void ble_loop_tick()   {}
void ble_shutdown()    {}

#endif
