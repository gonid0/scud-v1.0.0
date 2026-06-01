// FDI handler (shared §5.8). Result 241 B. Не требует nonce.
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
#include <string.h>

size_t op_fdi(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (resp_max < 241) return 0;
    if (req_len < 1) return 0;
    (void)req;

    uint64_t now = rtc_now();

    // 1. Prepare encrypted_courier_blob plaintext (56 B).
    uint8_t plaintext[56];
    memcpy(plaintext, g_state.reader_id, 16);
    write_u64le(plaintext + 16, g_state.filter_version);
    memcpy(plaintext + 24, g_state.delivery_courier_id, 16);
    write_u64le(plaintext + 40, g_state.delivery_received_at);
    write_u64le(plaintext + 48, now);

    uint8_t blob[104];    // 32 + 56 + 16
    uint8_t rnd[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(rnd + i * 4, &r, 4);
    }
    if (!sealed_box_encrypt(g_state.server_x_pub, plaintext, 56, blob, rnd))
        return 0;

    // 2. Compose cleartext FDI (145 B header + signature 64 + next_nonce 32 = 241).
    resp[0] = MARK_FDI;
    memcpy(resp + 1, g_state.reader_id, 16);
    write_u64le(resp + 17, g_state.filter_version);
    write_u64le(resp + 25, g_state.delivery_received_at);
    memcpy(resp + 33, blob, 104);
    write_u64le(resp + 137, now);

    // Sign first 145 bytes with DOMAIN_FDI prefix.
    {
        uint8_t to_sign[16 + 145];
        memcpy(to_sign, DOMAIN_FDI, 16);
        memcpy(to_sign + 16, resp, 145);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, sizeof(to_sign), resp + 145);
    }

    // next_nonce tail
    uint8_t next_nonce[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(next_nonce + i * 4, &r, 4);
    }
    memcpy(resp + 209, next_nonce, 32);
    issue_nonce(next_nonce);

    return 241;
}
