#include "reader_config.h"
#include "reader_state.h"
#include "../config.h"
#include <Arduino.h>
#include <strings.h>
#include <stdlib.h>

// One row per per-reader operational parameter (Phase 1). Adding a parameter is
// a single new row here — load/commit/SET/STATUS all iterate this table.
//
//   key     NVS key (≤15 chars, ESP32 NVS limit)
//   cmd     SET-* serial command name (matched case-insensitively)
//   field   pointer-to-member into ReaderConfig (all uint32_t → uniform table)
//   defval  default == the config.h const (unprovisioned reader is identical)
//   minv..maxv  inclusive clamp range (docs/11 §6.1)
struct CfgParam {
    const char*           key;
    const char*           cmd;
    uint32_t ReaderConfig::* field;
    uint32_t              defval;
    uint32_t              minv;
    uint32_t              maxv;
};

static const CfgParam CFG[] = {
    { "skew",      "SET-CLOCK-SKEW",        &ReaderConfig::clock_skew,     CLOCK_SKEW_SECONDS,           5,      600    },
    { "nonce_ttl", "SET-NONCE-TTL",         &ReaderConfig::nonce_ttl_ms,   NONCE_TTL_MS,                 2000,   60000  },
    { "nfc_dl",    "SET-NFC-DEADLINE",      &ReaderConfig::nfc_deadline_ms,NFC_SESSION_DEADLINE_MS,      3000,   30000  },
    { "ble_idle",  "SET-BLE-IDLE",          &ReaderConfig::ble_idle_ms,    BLE_IDLE_TIMEOUT_MS,          5000,   300000 },
    { "ts_drift",  "SET-TSYNC-DRIFT",       &ReaderConfig::ts_drift,       TIME_SYNC_DRIFT_S_PER_DAY,    1,      60     },
    { "ts_boot",   "SET-TSYNC-BOOTSTRAP",   &ReaderConfig::ts_boot,        TIME_SYNC_BOOTSTRAP_WINDOW_S, 3600,   604800 },
    { "psg_dir",   "SET-PASSAGE-DIRECTION", &ReaderConfig::passage_dir,    PASSAGE_DEFAULT_DIRECTION,    1,      2      },
    { "wl_max",    "SET-WHITELIST-MAX",     &ReaderConfig::wl_max,         MAX_WHITELIST_COUNT,          32,     2048   },
    { "bld_max",   "SET-BL-DELTA-MAX",      &ReaderConfig::bld_max,        MAX_BL_DELTA_COUNT,           32,     2048   },
    { "cd_end",    "SET-COOLDOWN-END",      &ReaderConfig::cd_end,         COOLDOWN_AFTER_END_MS,        500,    10000  },
    { "cd_grant",  "SET-COOLDOWN-GRANT",    &ReaderConfig::cd_grant,       COOLDOWN_AFTER_GRANT_MS,      1000,   15000  },
    { "pn_retry",  "SET-PN532-RETRY",       &ReaderConfig::pn532_retries,  PN532_PASSIVE_RETRIES,        1,      255    },
    { "rf_atr",    "SET-RF-ATR",            &ReaderConfig::rf_atr,         PN532_RF_TIMING_ATR,          10,     15     },
    { "rf_retry",  "SET-RF-RETRY",          &ReaderConfig::rf_retry,       PN532_RF_TIMING_RETRY,        10,     15     },
    { "fld_pause", "SET-FIELD-PAUSE",       &ReaderConfig::field_pause,    FIELD_RESET_PAUSE_MS,         50,     500    },
    { "pi_retry",  "SET-PUSH-RETRIES",      &ReaderConfig::push_retries,   PUSH_INFO_RETRIES,            1,      10     },
    { "pi_delay",  "SET-PUSH-DELAY",        &ReaderConfig::push_delay,     PUSH_INFO_RETRY_DELAY_MS,     10,     500    },
    { "reinit",    "SET-REINIT-THRESHOLD",  &ReaderConfig::reinit_thr,     PN532_FAIL_REINIT_THRESHOLD,  1,      20     },
    { "nfc_det",   "SET-NFC-DETECT",        &ReaderConfig::nfc_detect,     NFC_DETECT_TIMEOUT_MS,        20,     500    },
    // ble_mtu floor is 243 (NOT below BLE_MAX_PDU=240): the framing always emits
    // BLE_MAX_PDU-sized chunks, so a negotiated MTU under that truncates PDUs.
    { "ble_mtu",   "SET-BLE-MTU",           &ReaderConfig::ble_mtu,        BLE_REQUESTED_MTU,            243,    517    },
    { "ble_defer", "SET-BLE-INFO-DEFER",    &ReaderConfig::ble_info_defer, BLE_INFO_DEFER_MS,            10,     500    },
    // Phase 1b: provisioned SOFT-caps for the local blacklist / nonce ring. The
    // maxv here equals the compile-time static array size — the runtime cap can
    // only ever shrink usage. RAISING THE MAX (maxv above LOCAL_BLACKLIST_CAP /
    // NONCE_RING_SIZE) REQUIRES RECOMPILING: the static arrays in reader_state.h
    // and RESULT_BUF_CAP are sized for LOCAL_BLACKLIST_CAP=256 / NONCE_RING_SIZE=8.
    { "bl_cap",    "SET-BLACKLIST-CAP",     &ReaderConfig::bl_cap,        LOCAL_BLACKLIST_CAP,          16,     LOCAL_BLACKLIST_CAP },
    { "nonce_ring","SET-NONCE-RING",        &ReaderConfig::nonce_ring,    NONCE_RING_SIZE,             2,      NONCE_RING_SIZE     },
    // T3 (plan 08 §4.3): require a verified NFC→BLE handover for BLE FILTER_UPDATE.
    // Default 0 = off (no behaviour change); 1 = fail-closed gate. Boolean range 0/1.
    { "ho_req",    "SET-HANDOVER-REQUIRED", &ReaderConfig::handover_required, HANDOVER_REQUIRED_DEFAULT, 0,  1                   },
};

static const size_t CFG_COUNT = sizeof(CFG) / sizeof(CFG[0]);

void reader_config_defaults() {
    for (size_t i = 0; i < CFG_COUNT; i++) {
        g_state.cfg.*(CFG[i].field) = CFG[i].defval;
    }
}

void reader_config_load(Preferences& p) {
    for (size_t i = 0; i < CFG_COUNT; i++) {
        g_state.cfg.*(CFG[i].field) = p.getUInt(CFG[i].key, CFG[i].defval);
    }
}

void reader_config_commit(Preferences& p) {
    for (size_t i = 0; i < CFG_COUNT; i++) {
        p.putUInt(CFG[i].key, g_state.cfg.*(CFG[i].field));
    }
}

bool reader_config_set(const char* cmd, const char* arg) {
    for (size_t i = 0; i < CFG_COUNT; i++) {
        if (strcasecmp(cmd, CFG[i].cmd) != 0) continue;
        if (!arg) { Serial.println(F("ERR missing value")); return true; }
        uint32_t v = (uint32_t)strtoul(arg, nullptr, 0);
        if (v < CFG[i].minv) v = CFG[i].minv;
        if (v > CFG[i].maxv) v = CFG[i].maxv;
        g_state.cfg.*(CFG[i].field) = v;
        Serial.printf("OK %s=%u\n", CFG[i].key, (unsigned)v);
        return true;
    }
    return false;
}

void reader_config_status() {
    for (size_t i = 0; i < CFG_COUNT; i++) {
        Serial.printf("%s=%u\n", CFG[i].key, (unsigned)(g_state.cfg.*(CFG[i].field)));
    }
}
