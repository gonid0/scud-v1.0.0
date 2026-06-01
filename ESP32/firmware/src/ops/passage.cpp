// passage_receipt builder (shared §15.3).
//
// H3 FOLD-IN: the separate GET_PASSAGE_RECEIPT op (inner 0x16) + the per-session
// cache state machine are GONE. The 192-B reader-signed passage_receipt is now
// built inline on the ACCESS=OK path and APPENDED to the ACCESS_VERDICT response
// (see access.cpp). This file keeps ONLY the pure receipt-builder so the layout +
// DOMAIN_PSG signing live in one host-testable place; op_access calls it.
//
// Backend contract is UNCHANGED: the phone uploads the bare 192-B receipt body
// (response[42:234]) as the existing "passage_receipt" report type. Backend
// parse/verify/dedup stay byte-identical.
//
// Криптография:
//   reader_signature над DOMAIN_PSG || receipt[0:128]. Backend верифицирует
//   тем же reader_pubkey, которым проверяет INFO/FDI/BLK.

#include "ops.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include <esp_system.h>
#include <string.h>

// Build the 192-B passage_receipt (shared §15.3) into `out`. Pure: all inputs are
// passed explicitly (so it is host-testable and has no hidden session state). The
// caller (op_access on RES_OK) supplies the access facts; passed_at is captured
// from rtc_now() here, receipt_nonce is fresh random, session_seq echoes
// g_state.session_seq, direction comes from the reader policy (g_state.cfg).
void build_passage_receipt(uint8_t out[192],
                           const uint8_t key_id[16],
                           const uint8_t phone_pubkey[32],
                           const uint8_t permit_id[16],
                           uint64_t issued_key_issued_at,
                           uint32_t issued_key_serial) {
    out[0] = 0x01;                                       // format_version
    memcpy(out + 1, g_state.reader_id, 16);              // reader_id

    // receipt_nonce (16 B random) — backend dedup key
    uint8_t rnonce[16];
    {
        uint32_t r0 = esp_random(); memcpy(rnonce + 0,  &r0, 4);
        uint32_t r1 = esp_random(); memcpy(rnonce + 4,  &r1, 4);
        uint32_t r2 = esp_random(); memcpy(rnonce + 8,  &r2, 4);
        uint32_t r3 = esp_random(); memcpy(rnonce + 12, &r3, 4);
    }
    memcpy(out + 17, rnonce, 16);
    memcpy(out + 33, key_id, 16);
    write_u64le(out + 49, rtc_now());                    // passed_at
    out[57] = (uint8_t)g_state.cfg.passage_dir;          // direction = entry
                                                         // (детальная политика
                                                         // entry/exit — позже)
    out[58] = PASSAGE_VERDICT_OK;                        // verdict OK
    write_u32le(out + 59, g_state.session_seq);
    out[63] = PASSAGE_FLAG_REAL_PASS;                    // flags bit0 = real pass
    memcpy(out + 64, phone_pubkey, 32);
    memcpy(out + 96, permit_id, 16);
    write_u64le(out + 112, issued_key_issued_at);
    write_u32le(out + 120, issued_key_serial);
    memset(out + 124, 0, 4);                             // padding

    // sign: ed25519(DOMAIN_PSG || receipt[0:128]) → receipt[128:192]
    {
        uint8_t to_sign[16 + 128];
        memcpy(to_sign, DOMAIN_PSG, 16);
        memcpy(to_sign + 16, out, 128);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, sizeof(to_sign), out + 128);
    }
}
