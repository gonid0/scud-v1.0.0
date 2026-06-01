#include "immutable.h"
#include "reader_state.h"
#include "reader_config.h"
#include "../config.h"
#include <Preferences.h>
#include <string.h>

static const char* NS = NVS_NS_IMMUTABLE;

bool load_immutable_config() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/true)) return false;

    // Phase 1: per-reader operational config (defaults to config.h consts when a
    // key is absent). Loaded BEFORE the provisioned check so g_state.cfg holds
    // valid defaults even on an unprovisioned reader (provisioning CLI reads them).
    reader_config_load(p);

    uint8_t flag = p.getUChar("provisioned", 0);
    if (flag != 1) { p.end(); return false; }

    size_t n = 0;
    n = p.getBytes("reader_id",      g_state.reader_id, 16);      if (n != 16) { p.end(); return false; }
    n = p.getBytes("rd_ed_priv",     g_state.reader_ed_priv, 32); if (n != 32) { p.end(); return false; }
    n = p.getBytes("rd_ed_pub",      g_state.reader_ed_pub, 32);  if (n != 32) { p.end(); return false; }
    n = p.getBytes("srv_ed_pub",     g_state.server_ed_pub, 32);  if (n != 32) { p.end(); return false; }
    n = p.getBytes("srv_x_pub",      g_state.server_x_pub, 32);   if (n != 32) { p.end(); return false; }
    n = p.getBytes("group_id",       g_state.reader_group_id, 16);if (n != 16) { p.end(); return false; }

    {
        // Defensive clamp on load: a reader provisioned before the SET-LOCK-DURATION
        // bound (or a corrupt NVS value) is pulled into the safe range at boot.
        uint16_t ld = p.getUShort("lock_dur", DEFAULT_LOCK_SIGNAL_DURATION_MS);
        if (ld < LOCK_DURATION_MIN_MS) ld = LOCK_DURATION_MIN_MS;
        if (ld > LOCK_DURATION_MAX_MS) ld = LOCK_DURATION_MAX_MS;
        g_state.lock_duration_ms = ld;
    }
    g_state.ble_enabled      = p.getUChar("ble_en", DEFAULT_BLE_ENABLED) != 0;   // B8: default on
    p.end();
    return true;
}

bool commit_immutable_config() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;

    bool ok = true;
    ok &= p.putBytes("reader_id",  g_state.reader_id, 16)       == 16;
    ok &= p.putBytes("rd_ed_priv", g_state.reader_ed_priv, 32)  == 32;
    ok &= p.putBytes("rd_ed_pub",  g_state.reader_ed_pub, 32)   == 32;
    ok &= p.putBytes("srv_ed_pub", g_state.server_ed_pub, 32)   == 32;
    ok &= p.putBytes("srv_x_pub",  g_state.server_x_pub, 32)    == 32;
    ok &= p.putBytes("group_id",   g_state.reader_group_id, 16) == 16;
    ok &= p.putUShort("lock_dur",  g_state.lock_duration_ms)    > 0;
    ok &= p.putUChar("ble_en",     g_state.ble_enabled ? 1 : 0) > 0;   // B8

    // Phase 1: persist per-reader operational config (same scud_imm namespace).
    reader_config_commit(p);

    if (ok) ok &= p.putUChar("provisioned", 1) > 0;

    p.end();
    return ok;
}

bool wipe_immutable_config() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;
    p.clear();
    p.end();
    return true;
}
