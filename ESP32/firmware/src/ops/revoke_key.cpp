// REVOKE_KEY handler (shared §5.10, §13). Request 407 B.
#include "ops.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../state/local.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include "../crypto/bloom.h"
#include "../crypto/key_id.h"
#include <esp_system.h>
#include <string.h>

static size_t make_rev_result(uint8_t* resp, uint8_t code) {
    uint8_t ext[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(ext + i * 4, &r, 4);
    }
    issue_nonce(ext);
    return make_op_result(resp, INNER_REVOKE_KEY, MARK_OP_RESULT_REV, code, ext, 32);
}

#define REV_BAD(c) make_rev_result(resp, (c))

size_t op_revoke(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (resp_max < 45) return 0;
    if (req_len != 407) return REV_BAD(RES_BAD_FORMAT);

    const uint8_t* requester = req + 1;       // 151 B
    const uint8_t* target    = req + 152;     // 151 B
    const uint8_t* used_nonce     = req + 303;
    const uint8_t* reader_time_e  = req + 335;
    const uint8_t* phone_sig      = req + 343;

    if (requester[0] != 0x01 || target[0] != 0x01) return REV_BAD(RES_BAD_FORMAT);

    if (!consume_nonce(used_nonce)) return REV_BAD(RES_BAD_NONCE);

    if (memcmp(requester + 1, g_state.reader_id, 16) != 0) return REV_BAD(RES_WRONG_READER);
    if (memcmp(target    + 1, g_state.reader_id, 16) != 0) return REV_BAD(RES_WRONG_READER);

    // permit_id check: requester.permit_id == target.permit_id
    if (memcmp(requester + 65, target + 65, 16) != 0) return REV_BAD(RES_NOT_AUTHORIZED);

    uint64_t now = rtc_now();
    uint64_t requester_expires = read_u64le(requester + 57);
    if (requester_expires <= now) return REV_BAD(RES_EXPIRED);

    // Compute requester key_id and perform bloom/blacklist check.
    const uint8_t* requester_pub = requester + 17;
    uint64_t requester_issued    = read_u64le(requester + 49);
    uint32_t requester_serial    = read_u32le(requester + 81);

    uint8_t req_key_id[16];
    compute_key_id(g_state.reader_id, requester_pub, requester_issued, requester_serial, req_key_id);
    if (blacklist_find(req_key_id) >= 0) return REV_BAD(RES_REVOKED_BLACKLIST);
    if (g_state.bloom_bytes &&
        bloom_contains(g_state.bloom_bytes, g_state.filter_m_bits,
                       g_state.filter_k_hashes, g_state.filter_hash_seed, req_key_id)) {
        bool in_wl = false;
        for (uint16_t i = 0; i < g_state.whitelist_count; i++) {
            if (memcmp(g_state.whitelist[i].key_id, req_key_id, 16) == 0) {
                if (g_state.whitelist[i].expires_at > now) { in_wl = true; break; }
            }
        }
        if (!in_wl) return REV_BAD(RES_REVOKED_FILTER);
    }

    // Verify requester server signature.
    {
        uint8_t to_v[16 + 87];
        memcpy(to_v, DOMAIN_KEY, 16);
        memcpy(to_v + 16, requester, 87);
        if (!ed25519_verify(g_state.server_ed_pub, to_v, sizeof(to_v), requester + 87))
            return REV_BAD(RES_BAD_SIGNATURE);
    }
    // Verify target server signature.
    {
        uint8_t to_v[16 + 87];
        memcpy(to_v, DOMAIN_KEY, 16);
        memcpy(to_v + 16, target, 87);
        if (!ed25519_verify(g_state.server_ed_pub, to_v, sizeof(to_v), target + 87))
            return REV_BAD(RES_BAD_SIGNATURE);
    }

    // Compute target key_id.
    const uint8_t* target_pub = target + 17;
    uint64_t target_issued    = read_u64le(target + 49);
    uint32_t target_serial    = read_u32le(target + 81);
    uint8_t target_key_id[16];
    compute_key_id(g_state.reader_id, target_pub, target_issued, target_serial, target_key_id);

    // Verify phone_signature.
    {
        uint8_t to_v[16 + 16 + 32 + 8 + 16 + 16];
        memcpy(to_v,                  DOMAIN_REV,         16);
        memcpy(to_v + 16,             g_state.reader_id,  16);
        memcpy(to_v + 32,             used_nonce,         32);
        memcpy(to_v + 64,             reader_time_e,       8);
        memcpy(to_v + 72,             req_key_id,         16);
        memcpy(to_v + 88,             target_key_id,      16);
        if (!ed25519_verify(requester_pub, to_v, sizeof(to_v), phone_sig))
            return REV_BAD(RES_BAD_SIGNATURE);
    }

    // Idempotency.
    if (blacklist_find(target_key_id) >= 0) return make_rev_result(resp, RES_OK);

    uint64_t target_expires = read_u64le(target + 57);
    // FW-ARC-06: blacklist_append() is evict-expired-then-fail-closed. If it
    // returns false the blacklist is full AND no entry was expired, so the
    // revocation was NOT stored. Return RES_NO_SLOT (an op_result with a
    // non-OK code) — this path signs no success delivery-receipt, so we never
    // acknowledge a revocation we failed to persist (fail-closed).
    if (!blacklist_append(target_key_id, now, target_expires, BL_REASON_BY_USER)) {
        return REV_BAD(RES_NO_SLOT);
    }

    return make_rev_result(resp, RES_OK);
}
