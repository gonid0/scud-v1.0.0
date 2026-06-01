// BLE chunked framing (§16.5). See ble_frame.h. Mirrors the Python reference
// docs/test_vectors/generate.py::ble_frame and the Android writeChunked math.
#include "ble_frame.h"
#include "../config.h"   // BLE_MAX_PDU / BLE_PDU_STAGE_CAP (pure #defines; host-safe via relative path)
#include <string.h>

// Staging buffer must always hold one full first-PDU (6 B header + max_pdu payload).
static_assert(BLE_PDU_STAGE_CAP >= BLE_MAX_PDU + 6,
              "BLE_PDU_STAGE_CAP must fit a full first-PDU header + max payload");

static inline void w_u32le(uint8_t* p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)((v >> 16) & 0xFF);
    p[3] = (uint8_t)((v >> 24) & 0xFF);
}

// Open-coded LE read (kept self-contained so the host build needs no extra -I).
static inline uint32_t r_u32le(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

void ble_frame_message(const uint8_t* data, uint32_t len, uint16_t max_pdu,
                       ble_pdu_sink sink, void* ctx) {
    uint32_t sent = 0;
    uint8_t  seq  = 0;
    while (sent < len) {
        uint8_t  pdu[BLE_PDU_STAGE_CAP];         // max_pdu <= BLE_MAX_PDU on the wire
        size_t   off       = 0;
        bool     is_first  = (seq == 0);
        uint32_t header    = is_first ? 6u : 2u; // seq+flags(+total_len on first)
        uint32_t can_send  = (uint32_t)max_pdu - header;
        uint32_t this_chunk = (len - sent) > can_send ? can_send : (len - sent);
        bool     is_last   = (sent + this_chunk == len);

        pdu[off++] = seq;
        uint8_t flags = 0;
        if (is_last)  flags |= BLE_FLAG_LAST;
        if (is_first) flags |= BLE_FLAG_HAS_TOTAL;
        pdu[off++] = flags;
        if (is_first) {
            w_u32le(pdu + off, len);
            off += 4;
        }
        memcpy(pdu + off, data + sent, this_chunk);
        off += this_chunk;

        sink(ctx, pdu, off);
        sent += this_chunk;
        seq++;
    }
}

static void reasm_reset(uint32_t* total_len, uint32_t* got_len,
                        uint8_t* next_seq, bool* in_progress) {
    *total_len = 0;
    *got_len = 0;
    *next_seq = 0;
    *in_progress = false;
}

ble_reasm_status ble_reasm_feed(const uint8_t* pdu, size_t len,
                                uint8_t* buf, uint32_t buf_cap,
                                uint32_t* total_len, uint32_t* got_len,
                                uint8_t* next_seq, bool* in_progress) {
    if (len < 2) return BLE_REASM_IGNORED;
    uint8_t seq   = pdu[0];
    uint8_t flags = pdu[1];
    size_t  hdr   = 2;
    if (flags & BLE_FLAG_HAS_TOTAL) {
        if (len < 6) return BLE_REASM_IGNORED;
        *total_len   = r_u32le(pdu + 2);
        *got_len     = 0;
        *next_seq    = 0;
        *in_progress = true;
        hdr = 6;
        if (*total_len > buf_cap) {
            reasm_reset(total_len, got_len, next_seq, in_progress);
            return BLE_REASM_ERROR;
        }
    }
    if (!*in_progress) return BLE_REASM_IGNORED;
    if (seq != *next_seq) {
        reasm_reset(total_len, got_len, next_seq, in_progress);
        return BLE_REASM_ERROR;
    }
    uint32_t chunk = (uint32_t)(len - hdr);
    if (*got_len + chunk > *total_len) {
        reasm_reset(total_len, got_len, next_seq, in_progress);
        return BLE_REASM_ERROR;
    }
    memcpy(buf + *got_len, pdu + hdr, chunk);
    *got_len += chunk;
    (*next_seq)++;
    return (flags & BLE_FLAG_LAST) ? BLE_REASM_DONE : BLE_REASM_NEED_MORE;
}
