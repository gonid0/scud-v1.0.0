// BLE chunked framing (shared §16.5) — PURE module, no NimBLE/Arduino deps, so
// the host conformance test (test_host/) links it directly and checks it against
// the golden vectors in docs/test_vectors/protocol_v1.json ("ble_framing").
//
// PDU layout: [seq 1B] [flags 1B] [total_len 4B LE on the first PDU] [chunk].
//   flags bit0 = LAST, bit1 = HAS_TOTAL (only seq==0 carries total_len).
#pragma once
#include <stdint.h>
#include <stddef.h>

constexpr uint8_t BLE_FLAG_LAST      = 0x01;
constexpr uint8_t BLE_FLAG_HAS_TOTAL = 0x02;

// Each built PDU is handed to the sink in order. Streaming by design: the caller
// notifies (firmware) or collects (host test) per PDU — no large frame buffer.
typedef void (*ble_pdu_sink)(void* ctx, const uint8_t* pdu, size_t len);

// Split data[0:len] into PDUs of at most max_pdu bytes, mirroring the Python
// reference ble_frame() byte-for-byte. max_pdu must be >= 7 (6 B first header + 1).
// len == 0 produces no PDUs (matches firmware/Android: the while-loop never runs).
void ble_frame_message(const uint8_t* data, uint32_t len, uint16_t max_pdu,
                       ble_pdu_sink sink, void* ctx);

// --- inbound reassembly (consume side) — the inverse of ble_frame_message ---
typedef enum {
    BLE_REASM_NEED_MORE = 0,  // PDU accepted; more chunks expected
    BLE_REASM_DONE      = 1,  // LAST seen; got_len bytes of the message are in buf
    BLE_REASM_IGNORED   = 2,  // PDU too short, or arrived before any first/HAS_TOTAL PDU
    BLE_REASM_ERROR     = 3,  // protocol error (seq gap / overflow / oversize); state reset
} ble_reasm_status;

// Feed one inbound [seq][flags][total_len] PDU. Pure (no NimBLE/Arduino): the caller
// owns the accumulator fields + destination buffer, mirroring the firmware g_inbound
// and the Android ReassemblyBuf. The first PDU (HAS_TOTAL) (re)initialises the run;
// each subsequent chunk is appended at *got_len. On a protocol error the four state
// fields are reset to a clean idle state and BLE_REASM_ERROR is returned. Checked
// against the golden ble_framing vectors in the host test.
ble_reasm_status ble_reasm_feed(const uint8_t* pdu, size_t len,
                                uint8_t* buf, uint32_t buf_cap,
                                uint32_t* total_len, uint32_t* got_len,
                                uint8_t* next_seq, bool* in_progress);
