// TIME_SYNC handler (shared §5.13, §10).
#include "ops.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../state/reader_state.h"
#include "../state/local.h"
#include "../state/authoritative.h"
#include "../hw/rtc.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include <esp_system.h>
#include <string.h>

static size_t make_result_with_next_nonce(uint8_t* resp, uint8_t code) {
    uint8_t ext[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(ext + i * 4, &r, 4);
    }
    issue_nonce(ext);
    return make_op_result(resp, INNER_TIME_SYNC, MARK_OP_RESULT_TSYNC, code, ext, 32);
}

#define TS_BAD(code) make_result_with_next_nonce(resp, (code))

size_t op_time_sync(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (resp_max < 45) return 0;
    if (req_len != 289) return TS_BAD(RES_BAD_FORMAT);

    const uint8_t* grant     = req + 1;       // 148 B
    const uint8_t* statement = req + 149;     // 140 B

    if (grant[0] != 0x01 || statement[0] != 0x01) return TS_BAD(RES_BAD_FORMAT);

    // Parse grant (shared §5.11).
    const uint8_t* g_reader_id  = grant + 1;
    const uint8_t* auth_pubkey  = grant + 17;
    const uint8_t* auth_id      = grant + 49;
    uint64_t g_issued_at   = read_u64le(grant + 65);
    uint64_t g_expires_at  = read_u64le(grant + 73);
    uint8_t  g_kind        = grant[81];
    const uint8_t* g_sig   = grant + 84;

    // Parse statement (shared §5.12).
    const uint8_t* st_reader_id = statement + 1;
    const uint8_t* st_auth_id   = statement + 17;
    uint64_t st_new_time        = read_u64le(statement + 33);
    const uint8_t* st_nonce     = statement + 41;
    uint8_t st_kind             = statement[73];
    const uint8_t* st_sig       = statement + 76;

    if (memcmp(g_reader_id,  g_state.reader_id, 16) != 0) return TS_BAD(RES_WRONG_READER);
    if (memcmp(st_reader_id, g_state.reader_id, 16) != 0) return TS_BAD(RES_WRONG_READER);
    if (memcmp(st_auth_id,   auth_id,           16) != 0) return TS_BAD(RES_BAD_FORMAT);
    if (st_kind != g_kind) return TS_BAD(RES_BAD_FORMAT);

    if (!consume_nonce(st_nonce)) return TS_BAD(RES_BAD_NONCE);

    uint64_t now = rtc_now();
    if (g_expires_at <= now) return TS_BAD(RES_NOT_AUTHORIZED);

    (void)g_issued_at;

    // Shared §10: verify signatures BEFORE the soft/hard policy branch.
    // Otherwise unauthenticated statements could probe/trigger TIME_REGRESSION paths.

    // Verify grant signature (server).
    {
        uint8_t to_verify[16 + 84];
        memcpy(to_verify,       DOMAIN_TGR, 16);
        memcpy(to_verify + 16,  grant,      84);
        if (!ed25519_verify(g_state.server_ed_pub, to_verify, sizeof(to_verify), g_sig))
            return TS_BAD(RES_BAD_SIGNATURE);
    }

    // Verify statement signature (authority).
    {
        uint8_t to_verify[16 + 76];
        memcpy(to_verify,       DOMAIN_TIM, 16);
        memcpy(to_verify + 16,  statement,  76);
        if (!ed25519_verify(auth_pubkey, to_verify, sizeof(to_verify), st_sig))
            return TS_BAD(RES_BAD_SIGNATURE);
    }

    // Soft/hard policy (shared §10).
    if (g_kind == 0x02) {
        // HARD — apply unconditionally.
    } else if (g_kind == 0x01) {
        // SOFT: достаточно, чтобы DS3231 была живая (возвращает ненулевое время).
        if (now == RTC_DEAD_SENTINEL) return TS_BAD(RES_NOT_AUTHORIZED);

        // Первая SOFT-синхронизация (ещё ни одного sync'а в NVS): допускаем
        // широкое окно — 1 сутки. Это нужно, чтобы на свежепровижиненном ридере,
        // у которого DS3231 стартовал с условного «2001 года», можно было
        // привести часы к правильному времени без HARD grant'а. После первой
        // удачной синхронизации переключаемся на строгую политику.
        int64_t window;
        if (g_state.time_sync_last_at == 0) {
            window = (int64_t)g_state.cfg.ts_boot;   // 24 часа (дефолт)
        } else {
            uint64_t elapsed = (now > g_state.time_sync_last_at)
                               ? (now - g_state.time_sync_last_at) : 0;
            uint64_t days = elapsed / TIME_SYNC_SECONDS_PER_DAY;
            if (days < TIME_SYNC_MIN_WINDOW_DAYS) days = TIME_SYNC_MIN_WINDOW_DAYS;
            window = (int64_t)g_state.cfg.ts_drift * (int64_t)days;
        }
        int64_t diff = (int64_t)now - (int64_t)st_new_time;
        if (diff < 0) diff = -diff;
        if (diff > window) return TS_BAD(RES_TIME_REGRESSION);
    } else {
        return TS_BAD(RES_BAD_FORMAT);
    }

    // Apply.
    rtc_set(st_new_time);
    g_state.time_sync_last_at = st_new_time;
    memcpy(g_state.time_sync_last_authority_id, auth_id, 16);
    g_state.time_sync_last_kind = g_kind;
    save_time_sync_state_to_nvs();

    return make_result_with_next_nonce(resp, RES_OK);
}
