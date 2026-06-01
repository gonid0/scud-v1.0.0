// ACCESS handler (shared §5.3, §11).
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

// Build ACCESS_VERDICT (42 B): marker + result + reader_time + next_nonce.
static size_t build_verdict(uint8_t* out, uint8_t result_code) {
    out[0] = MARK_ACCESS_VERDICT;
    out[1] = result_code;
    write_u64le(out + 2, rtc_now());
    uint8_t next[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(next + i * 4, &r, 4);
    }
    memcpy(out + 10, next, 32);
    issue_nonce(next);
    return 42;
}

size_t op_access(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    // H3 FOLD-IN: on RES_OK the response is 234 B (42-B verdict + 192-B
    // passage_receipt appended at offset 42); every deny stays 42 B. Require room
    // for the OK case up front (both NFC and BLE result buffers are RESULT_BUF_CAP
    // = 8704, so this never trips at runtime — it just guards the append below).
    if (resp_max < 234) return 0;
    if (req_len != 256) return build_verdict(resp, RES_BAD_FORMAT);

    // Layout: inner_opcode(1) + issued_key(151) + used_nonce(32) + reader_time_echo(8) + phone_signature(64)
    const uint8_t* issued_key   = req + 1;
    const uint8_t* used_nonce   = req + 152;
    const uint8_t* phone_sig    = req + 192;

    // issued_key layout: format(1) + reader_id(16) + phone_pubkey(32) + issued_at(8) + expires_at(8)
    //                    + permit_id(16) + serial(4) + payload(2) + server_signature(64)
    if (issued_key[0] != 0x01) return build_verdict(resp, RES_BAD_FORMAT);

    const uint8_t* ik_reader_id = issued_key + 1;
    const uint8_t* phone_pub    = issued_key + 17;
    uint64_t issued_at   = read_u64le(issued_key + 49);
    uint64_t expires_at  = read_u64le(issued_key + 57);
    const uint8_t* permit_id  = issued_key + 65;
    uint32_t serial      = read_u32le(issued_key + 81);
    const uint8_t* server_sig = issued_key + 87;

    // 1. nonce (consume)
    if (!consume_nonce(used_nonce)) return build_verdict(resp, RES_BAD_NONCE);

    // 2. reader_id match
    if (memcmp(ik_reader_id, g_state.reader_id, 16) != 0)
        return build_verdict(resp, RES_WRONG_READER);

    // 3. Time checks
    uint64_t now = rtc_now();
    // H2: DS3231 dead/unset (rtc_now()==RTC_DEAD_SENTINEL i.e. 0). Без валидного
    // времени любая проверка expires_at/issued_at бессмысленна — fail-closed как
    // EXPIRED, чтобы мёртвый RTC не выдал «вечно валидный» доступ.
    if (now == RTC_DEAD_SENTINEL) return build_verdict(resp, RES_EXPIRED);
    if (expires_at <= now) return build_verdict(resp, RES_EXPIRED);
    if (issued_at > now + g_state.cfg.clock_skew) return build_verdict(resp, RES_BAD_FORMAT);

    // 4. key_id + blacklist
    uint8_t key_id[16];
    compute_key_id(g_state.reader_id, phone_pub, issued_at, serial, key_id);
    if (blacklist_find(key_id) >= 0) return build_verdict(resp, RES_REVOKED_BLACKLIST);

    // 5. Bloom + whitelist
    if (g_state.bloom_bytes &&
        bloom_contains(g_state.bloom_bytes, g_state.filter_m_bits,
                       g_state.filter_k_hashes, g_state.filter_hash_seed, key_id)) {
        bool in_wl = false;
        for (uint16_t i = 0; i < g_state.whitelist_count; i++) {
            if (memcmp(g_state.whitelist[i].key_id, key_id, 16) == 0) {
                if (g_state.whitelist[i].expires_at > now) { in_wl = true; break; }
            }
        }
        if (!in_wl) return build_verdict(resp, RES_REVOKED_FILTER);
    }

    // 6. Verify server signature on issued_key: DOMAIN_KEY || bytes[0:87]
    {
        uint8_t to_verify[16 + 87];
        memcpy(to_verify, DOMAIN_KEY, 16);
        memcpy(to_verify + 16, issued_key, 87);
        if (!ed25519_verify(g_state.server_ed_pub, to_verify, sizeof(to_verify), server_sig))
            return build_verdict(resp, RES_BAD_SIGNATURE);
    }

    // 7. Verify phone_signature: DOMAIN_RSP || reader_id || used_nonce || reader_time_echo || key_id
    {
        uint8_t to_verify[16 + 16 + 32 + 8 + 16];
        memcpy(to_verify,        DOMAIN_RSP,             16);
        memcpy(to_verify + 16,   g_state.reader_id,      16);
        memcpy(to_verify + 32,   used_nonce,             32);
        memcpy(to_verify + 64,   req + 184,              8);   // reader_time_echo
        memcpy(to_verify + 72,   key_id,                 16);
        if (!ed25519_verify(phone_pub, to_verify, sizeof(to_verify), phone_sig))
            return build_verdict(resp, RES_BAD_SIGNATURE);
    }

    // RES_OK — H3 FOLD-IN: build the 42-B verdict, then APPEND the 192-B
    // reader-signed passage_receipt at offset 42 (response[42:234]). The phone
    // uploads the bare receipt body (response[42:234]) as the existing
    // "passage_receipt" report — backend parse/verify/dedup unchanged. The
    // verdict bytes [0:42] (incl. next_nonce @[10:42]) stay BYTE-IDENTICAL.
    size_t verdict_len = build_verdict(resp, RES_OK);   // 42
    build_passage_receipt(resp + 42, key_id, phone_pub, permit_id, issued_at, serial);
    return verdict_len + 192;                           // 234
}
