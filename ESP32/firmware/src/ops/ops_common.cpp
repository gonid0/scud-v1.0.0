#include "ops.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../state/local.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include <esp_system.h>
#include <string.h>

// Generic OP_RESULT (shared §6).
// Layout: [marker 1][in_reply_to 1][result 1][reader_time 8][ext_len 2 LE][ext]
size_t make_op_result(uint8_t* resp, uint8_t in_reply_to, uint8_t marker, uint8_t code,
                      const uint8_t* ext, uint16_t ext_len) {
    resp[0] = marker;
    resp[1] = in_reply_to;
    resp[2] = code;
    write_u64le(resp + 3, rtc_now());
    write_u16le(resp + 11, ext_len);
    if (ext_len && ext) memcpy(resp + 13, ext, ext_len);
    return 13 + ext_len;
}

// Build INFO (shared §5.2). Size = 146.
size_t build_info_bytes(uint8_t out[146]) {
    out[0] = 0x01;                                   // format_version
    memcpy(out + 1, g_state.reader_id, 16);          // reader_id
    write_u64le(out + 17, rtc_now());                // reader_time
    out[25] = PROTOCOL_VERSION;                      // protocol_version
    write_u16le(out + 26, MAX_APDU_DATA_SIZE);       // max_apdu_size
    write_u64le(out + 28, g_state.filter_version);
    write_u64le(out + 36, g_state.delivery_received_at);
    write_u16le(out + 44, g_state.blacklist_count);

    // fresh_nonce (32 B random)
    uint8_t nonce[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(nonce + i * 4, &r, 4);
    }
    memcpy(out + 46, nonce, 32);
    issue_nonce(nonce);

    write_u32le(out + 78, g_state.session_seq);      // session_seq

    // Sign bytes[0:82] with DOMAIN_INF prefix.
    uint8_t to_sign[16 + 82];
    memcpy(to_sign, DOMAIN_INF, 16);
    memcpy(to_sign + 16, out, 82);
    ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub, to_sign, sizeof(to_sign), out + 82);
    return 146;
}
