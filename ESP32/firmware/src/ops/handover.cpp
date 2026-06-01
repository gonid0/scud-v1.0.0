// NFC→BLE handover handlers (shared §17.1, plan 08 §4.3).
//
// op_handover_issue   — NFC, phone→reader (INNER_HANDOVER_ISSUE 0x17). The phone
//   has just passed a physical ACCESS and asks for a token to deliver a filter
//   over BLE. The reader captures the CURRENT fresh_nonce (g_state.last_issued_nonce
//   — the nonce the reader currently expects, established at the tap's PUSH_INFO /
//   carried forward by ACCESS's next_nonce) as the binding tap_nonce, builds the
//   167-B reader-signed handover_token, and stages g_state.pending_handover.
//
// op_handover_present — BLE, phone→reader (INNER_HANDOVER_PRESENT 0x18). The phone
//   presents the token on a fresh BLE connection. The reader re-verifies its OWN
//   signature over bytes[0:103] under DOMAIN_BLE (integrity / non-forgery by a 3rd
//   party), checks the binding (tap_nonce + phone_pubkey == pending_handover,
//   reader_now <= expires_at, pending_handover.valid), and on success sets
//   g_state.handover_authorized = true and consumes pending_handover (one-shot).
//   Any failure → RES_* reject, authorized stays false (fail-closed).
//
// Crypto: reader_signature over DOMAIN_BLE || bytes[0:103]. The reader signs with
// its own ed25519 key and (on PRESENT) verifies with its own pubkey — the signature
// proves the token was not forged/altered by the phone or a relay between the two
// radios; the binding proves it is the SAME tap and SAME phone.

#include "ops.h"
#include "handover.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include <string.h>

// NFC, phone→reader. Payload: inner_opcode(1) + phone_pubkey(32) = 33 B.
size_t op_handover_issue(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (resp_max < HANDOVER_TOKEN_LEN) return 0;
    // Payload is inner_opcode(1) + phone_pubkey(32). Reject malformed.
    if (req_len < 1 + 32)
        return make_op_result(resp, INNER_HANDOVER_ISSUE, MARK_OP_RESULT_HANDOVER,
                              RES_BAD_FORMAT, nullptr, 0);

    const uint8_t* phone_pubkey = req + 1;
    const uint8_t* tap_nonce    = g_state.last_issued_nonce;  // current fresh_nonce
    uint64_t issued_at  = rtc_now();
    uint64_t expires_at = issued_at + (uint64_t)HANDOVER_TOKEN_TTL_S;

    // ----- build the 167-B handover_token -----
    uint8_t* tok = resp;
    tok[0] = MARK_HANDOVER_TOKEN;                 // format_version = 0x99
    memcpy(tok + 1,  g_state.reader_id,      16); // reader_id
    memcpy(tok + 17, phone_pubkey,           32); // issued_to_phone_pubkey
    memcpy(tok + 49, g_state.reader_ble_addr, 6); // reader_ble_addr
    memcpy(tok + 55, tap_nonce,              32); // tap_nonce (binds to this tap)
    write_u64le(tok + 87, issued_at);             // issued_at (LE uint64)
    write_u64le(tok + 95, expires_at);            // expires_at (LE uint64)

    // reader_signature = sign_reader(DOMAIN_BLE, bytes[0:103])
    {
        uint8_t to_sign[16 + HANDOVER_TOKEN_SIGNED_LEN];
        memcpy(to_sign, DOMAIN_BLE, 16);
        memcpy(to_sign + 16, tok, HANDOVER_TOKEN_SIGNED_LEN);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, sizeof(to_sign), tok + 103);
    }

    // ----- stage pending_handover {tap_nonce, phone_pubkey, expires_at, valid} -----
    memcpy(g_state.pending_handover.tap_nonce,    tap_nonce,    32);
    memcpy(g_state.pending_handover.phone_pubkey, phone_pubkey, 32);
    g_state.pending_handover.expires_at = expires_at;
    g_state.pending_handover.valid      = true;

    return HANDOVER_TOKEN_LEN;
}

// BLE, phone→reader. Payload: inner_opcode(1) + handover_token(167) = 168 B.
size_t op_handover_present(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    // Generic OP_RESULT envelope (marker + in_reply_to + code + time + ext_len).
    if (resp_max < 13) return 0;

    // Fail-closed helper: every reject path leaves handover_authorized untouched
    // (it was reset on connect) and returns an OP_RESULT with the failure code.
    #define HO_REJECT(code) \
        return make_op_result(resp, INNER_HANDOVER_PRESENT, MARK_OP_RESULT_HANDOVER, (code), nullptr, 0)

    // Payload = inner_opcode(1) + token(167).
    if (req_len < 1 + HANDOVER_TOKEN_LEN) HO_REJECT(RES_BAD_FORMAT);
    const uint8_t* tok = req + 1;

    // 1. Marker + (implicit) length.
    if (tok[0] != MARK_HANDOVER_TOKEN) HO_REJECT(RES_BAD_FORMAT);

    // 2. reader_signature over bytes[0:103] under DOMAIN_BLE, against our own pub.
    {
        uint8_t to_verify[16 + HANDOVER_TOKEN_SIGNED_LEN];
        memcpy(to_verify, DOMAIN_BLE, 16);
        memcpy(to_verify + 16, tok, HANDOVER_TOKEN_SIGNED_LEN);
        if (!ed25519_verify(g_state.reader_ed_pub, to_verify, sizeof(to_verify), tok + 103))
            HO_REJECT(RES_BAD_SIGNATURE);
    }

    // 3. reader_id echo must be ours (defence-in-depth; the sig already binds it).
    if (memcmp(tok + 1, g_state.reader_id, 16) != 0) HO_REJECT(RES_WRONG_READER);

    // 4. Binding: there must be a staged, still-valid handover from a recent tap,
    //    and the token's tap_nonce + phone_pubkey must match it exactly.
    if (!g_state.pending_handover.valid) HO_REJECT(RES_NOT_AUTHORIZED);
    if (memcmp(tok + 55, g_state.pending_handover.tap_nonce, 32) != 0)
        HO_REJECT(RES_BAD_NONCE);
    if (memcmp(tok + 17, g_state.pending_handover.phone_pubkey, 32) != 0)
        HO_REJECT(RES_NOT_AUTHORIZED);

    // 5. Expiry: reader_now must be at or before the token expiry. rtc_now()==0
    //    (RTC dead) → fail-closed: we cannot prove freshness without a clock.
    uint64_t now = rtc_now();
    uint64_t expires_at = read_u64le(tok + 95);
    if (now == 0 || now > expires_at) HO_REJECT(RES_EXPIRED);

    // Success: authorize the FILTER stream on THIS connection and consume the
    // pending handover (one-shot — a presented token cannot be replayed).
    g_state.handover_authorized = true;
    g_state.pending_handover.valid = false;

    return make_op_result(resp, INNER_HANDOVER_PRESENT, MARK_OP_RESULT_HANDOVER,
                          RES_OK, nullptr, 0);
    #undef HO_REJECT
}
