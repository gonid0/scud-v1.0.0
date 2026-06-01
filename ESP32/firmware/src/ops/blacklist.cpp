// GET_BLACKLIST handler (shared §5.9). Не требует nonce.
// Layout: cleartext envelope 29 B + blob + signature 64 + next_nonce 32.
#include "ops.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../state/local.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include "../crypto/sealed_box.h"
#include <esp_system.h>
#include <stdlib.h>
#include <string.h>

size_t op_blacklist(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    (void)req; (void)req_len;

    uint64_t now = rtc_now();

    // Count live entries.
    uint16_t N = 0;
    for (uint16_t i = 0; i < LOCAL_BLACKLIST_CAP; i++) {
        if (g_state.blacklist[i].used_slot) N++;
    }

    // Plaintext: 34 + 32*N (shared §5.9.1). Zero-init for deterministic padding bytes.
    size_t plain_len = 34 + 32 * (size_t)N;
    uint8_t* plaintext = (uint8_t*)malloc(plain_len);
    if (!plaintext) return 0;
    memset(plaintext, 0, plain_len);

    memcpy(plaintext, g_state.reader_id, 16);
    write_u64le(plaintext + 16, now);
    write_u64le(plaintext + 24, g_state.filter_version);
    write_u16le(plaintext + 32, N);

    uint16_t written = 0;
    for (uint16_t i = 0; i < LOCAL_BLACKLIST_CAP && written < N; i++) {
        if (!g_state.blacklist[i].used_slot) continue;
        uint8_t* e = plaintext + 34 + 32 * written;
        // Entry (32 B, shared §5.9.1): key_id(16) + revoked_at(8) + expires_at(8).
        // NOTE: reason/padding listed in the spec table are inside the entry's 32 B —
        // spec's size arithmetic (`32×N`, `8399 B for N=256`) is authoritative and
        // contradicts the field-offset rows beyond 32 B. We keep `reason` local-only.
        memcpy(e, g_state.blacklist[i].key_id, 16);
        write_u64le(e + 16, g_state.blacklist[i].revoked_at);
        write_u64le(e + 24, g_state.blacklist[i].expires_at);
        written++;
    }

    size_t blob_len = 32 + plain_len + 16;
    uint8_t* blob = (uint8_t*)malloc(blob_len);
    if (!blob) { free(plaintext); return 0; }

    uint8_t rnd[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(rnd + i * 4, &r, 4);
    }
    if (!sealed_box_encrypt(g_state.server_x_pub, plaintext, plain_len, blob, rnd)) {
        free(plaintext); free(blob); return 0;
    }
    free(plaintext);

    size_t total = 29 + blob_len + 64 + 32;
    if (total > resp_max) { free(blob); return 0; }

    resp[0] = MARK_BLK;
    memcpy(resp + 1, g_state.reader_id, 16);
    write_u64le(resp + 17, now);
    write_u16le(resp + 25, N);
    write_u16le(resp + 27, (uint16_t)blob_len);
    memcpy(resp + 29, blob, blob_len);
    free(blob);

    // Sign first 29 + blob_len bytes with DOMAIN_BLK.
    size_t signed_len = 29 + blob_len;
    {
        uint8_t* to_sign = (uint8_t*)malloc(16 + signed_len);
        if (!to_sign) return 0;
        memcpy(to_sign, DOMAIN_BLK, 16);
        memcpy(to_sign + 16, resp, signed_len);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, 16 + signed_len, resp + signed_len);
        free(to_sign);
    }

    uint8_t next_nonce[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(next_nonce + i * 4, &r, 4);
    }
    memcpy(resp + signed_len + 64, next_nonce, 32);
    issue_nonce(next_nonce);

    return total;
}
