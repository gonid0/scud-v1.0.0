#pragma once
#include <stdint.h>
#include "../config.h"

struct NonceEntry {
    uint8_t  nonce[32];
    uint64_t issued_at_ms;
    bool     consumed;
    bool     used_slot;
};

struct WhitelistEntry {
    uint8_t  key_id[16];
    uint64_t expires_at;
};

struct BlacklistEntry {
    uint8_t  key_id[16];
    uint64_t revoked_at;
    uint64_t expires_at;
    uint8_t  reason;
    bool     used_slot;
};

// Phase 1: per-reader operational parameters provisionable via NVS (scud_imm).
// All fields are uint32_t so a single static table (reader_config.cpp) drives
// load/commit/SET/STATUS uniformly. Each field defaults to its config.h const,
// so an UNPROVISIONED or already-flashed reader (NVS key absent → getUInt
// returns the default) behaves identically. lock_duration_ms / ble_enabled are
// NOT here — they keep their own typed handling in immutable.cpp.
struct ReaderConfig {
    uint32_t clock_skew;       // CLOCK_SKEW_SECONDS
    uint32_t nonce_ttl_ms;     // NONCE_TTL_MS
    uint32_t nfc_deadline_ms;  // NFC_SESSION_DEADLINE_MS
    uint32_t ble_idle_ms;      // BLE_IDLE_TIMEOUT_MS
    uint32_t ts_drift;         // TIME_SYNC_DRIFT_S_PER_DAY
    uint32_t ts_boot;          // TIME_SYNC_BOOTSTRAP_WINDOW_S
    uint32_t passage_dir;      // PASSAGE_DEFAULT_DIRECTION
    uint32_t wl_max;           // MAX_WHITELIST_COUNT
    uint32_t bld_max;          // MAX_BL_DELTA_COUNT
    uint32_t cd_end;           // COOLDOWN_AFTER_END_MS
    uint32_t cd_grant;         // COOLDOWN_AFTER_GRANT_MS
    uint32_t pn532_retries;    // PN532_PASSIVE_RETRIES
    uint32_t rf_atr;           // PN532_RF_TIMING_ATR
    uint32_t rf_retry;         // PN532_RF_TIMING_RETRY
    uint32_t field_pause;      // FIELD_RESET_PAUSE_MS
    uint32_t push_retries;     // PUSH_INFO_RETRIES
    uint32_t push_delay;       // PUSH_INFO_RETRY_DELAY_MS
    uint32_t reinit_thr;       // PN532_FAIL_REINIT_THRESHOLD
    uint32_t nfc_detect;       // NFC_DETECT_TIMEOUT_MS
    uint32_t ble_mtu;          // BLE_REQUESTED_MTU
    uint32_t ble_info_defer;   // BLE_INFO_DEFER_MS
    // Phase 1b: provisioned SOFT-caps. The static arrays below stay sized at the
    // compile-time MAX (LOCAL_BLACKLIST_CAP / NONCE_RING_SIZE) — that is the HARD
    // max. These runtime caps only ever shrink usage; SET ranges are clamped to
    // <= the static MAX, so they can never exceed the array bounds. Default = MAX
    // (behaviour-identical when unprovisioned).
    uint32_t bl_cap;           // LOCAL_BLACKLIST_CAP (runtime soft-cap, <= MAX)
    uint32_t nonce_ring;       // NONCE_RING_SIZE     (runtime soft-cap, <= MAX)
    // T3 handover gate (shared §17.1 / plan 08 §4.3). Soft flag, default 0 =
    // current behaviour (no gate; the existing X2 BLE-filter path is unchanged).
    // When 1: INNER_FILTER_UPDATE over BLE requires a prior successful
    // INNER_HANDOVER_PRESENT on this connection (handover_authorized), else reject
    // (fail-closed). Provisioned via the CFG[] table (min/max 0/1, CLI key).
    uint32_t handover_required; // 0 = off (default), 1 = require handover for BLE filter
};

// T3 — NFC→BLE handover binding (shared §17.1, plan 08 §4.3). Captured at the NFC
// tap by op_handover_issue; consumed (one-shot) by op_handover_present over BLE.
// Lives in RAM only — the handover window is seconds, no NVS persistence needed.
struct PendingHandover {
    uint8_t  tap_nonce[32];     // the fresh_nonce the reader expected at the tap's PUSH_INFO
    uint8_t  phone_pubkey[32];  // the phone identity the token was issued to
    uint64_t expires_at;        // issued_at + HANDOVER_TOKEN_TTL_S (reader clock seconds)
    bool     valid;             // true between ISSUE and a successful PRESENT (then consumed)
};

struct ReaderState {
    // Immutable (cached from NVS "scud_imm")
    uint8_t  reader_id[16];
    uint8_t  reader_ed_priv[32];
    uint8_t  reader_ed_pub[32];
    uint8_t  server_ed_pub[32];
    uint8_t  server_x_pub[32];
    uint8_t  reader_group_id[16];
    uint16_t lock_duration_ms;
    bool     ble_enabled;          // B8: рантайм-gate BLE (NVS scud_imm:ble_en)

    // T3 handover (shared §17.1): the BLE MAC the phone connects to, bound into the
    // handover_token. Populated by ble_init() when the peripheral comes up (the
    // local NimBLE address, 6 B); all-zero on battery/BLE-disabled builds (the token
    // is only ever issued/presented on mains+BLE readers, so zero never ships).
    uint8_t  reader_ble_addr[6];

    // Phase 1: per-reader operational parameters (NVS scud_imm, see reader_config.cpp).
    ReaderConfig cfg;

    // Authoritative (NVS "scud_auth")
    uint64_t filter_version;
    uint32_t filter_m_bits;
    uint8_t  filter_k_hashes;
    uint32_t filter_hash_seed;
    uint64_t filter_generated_at;
    uint8_t  delivery_courier_id[16];
    uint64_t delivery_received_at;
    uint64_t time_sync_last_at;
    uint8_t  time_sync_last_authority_id[16];
    uint8_t  time_sync_last_kind;
    uint16_t blacklist_count;
    char     filter_current_slot;         // 'A' or 'B'

    // In-RAM bloom data (loaded from flash on boot)
    uint8_t*  bloom_bytes;
    uint32_t  bloom_bytes_len;
    WhitelistEntry* whitelist;
    uint16_t  whitelist_count;

    // Local blacklist (NVS "scud_local")
    BlacklistEntry blacklist[LOCAL_BLACKLIST_CAP];

    // Nonce ring (RAM only)
    NonceEntry nonce_ring[NONCE_RING_SIZE];

    // T3 — the most recently issued fresh_nonce (mirror of the last issue_nonce()
    // call). After PUSH_INFO it is the tap's fresh_nonce; op_handover_issue captures
    // it as the binding tap_nonce. RAM only, not security-critical on its own (the
    // binding security comes from the reader signature over the token).
    uint8_t  last_issued_nonce[32];

    // Transfer layer session bookkeeping
    uint32_t session_seq;

    // T3 — NFC→BLE handover (shared §17.1). pending_handover is staged at the NFC
    // tap (op_handover_issue) and consumed one-shot by the BLE present
    // (op_handover_present). handover_authorized is the per-connection authorization
    // flag the BLE FILTER gate checks; it is reset on (dis)connect (ble_channel.cpp)
    // and set true only by a verified PRESENT.
    PendingHandover pending_handover;
    bool            handover_authorized;
};

extern ReaderState g_state;

// Phase 1b: runtime soft-cap accessors. Clamp the provisioned cap to the static
// array MAX so a bad/legacy NVS value can never index out of bounds. A 0 cap
// (never reachable via the SET clamp ranges, but possible from a corrupt NVS or
// a not-yet-defaulted cfg) falls back to the static MAX.
static inline uint16_t blacklist_cap() {
    uint32_t c = g_state.cfg.bl_cap;
    if (c == 0 || c > LOCAL_BLACKLIST_CAP) c = LOCAL_BLACKLIST_CAP;
    return (uint16_t)c;
}
static inline uint16_t nonce_ring_cap() {
    uint32_t c = g_state.cfg.nonce_ring;
    if (c == 0 || c > NONCE_RING_SIZE) c = NONCE_RING_SIZE;
    return (uint16_t)c;
}
