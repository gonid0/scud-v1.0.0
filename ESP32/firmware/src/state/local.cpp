#include "local.h"
#include "reader_state.h"
#include "../config.h"
#include "../hw/rtc.h"
#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

static const char* NS = NVS_NS_LOCAL;

bool load_local_state() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/true)) return false;

    uint16_t stored = p.getUShort("count", 0);
    // Honour the full static MAX here: persisted entries are existing revocations
    // and must never be silently dropped on boot (dropping one would be fail-open).
    if (stored > LOCAL_BLACKLIST_CAP) stored = LOCAL_BLACKLIST_CAP;

    memset(g_state.blacklist, 0, sizeof(g_state.blacklist));
    for (uint16_t i = 0; i < stored; i++) {
        char k_key[NVS_BL_KEYNAME_BUF], k_meta[NVS_BL_KEYNAME_BUF];
        snprintf(k_key,  sizeof(k_key),  NVS_KEYPREFIX_BL_KEY,  (unsigned)i);
        snprintf(k_meta, sizeof(k_meta), NVS_KEYPREFIX_BL_META, (unsigned)i);

        uint8_t key_id[16];
        uint8_t meta[17];
        if (p.getBytes(k_key,  key_id, 16) != 16) continue;
        if (p.getBytes(k_meta, meta,   17) != 17) continue;

        memcpy(g_state.blacklist[i].key_id, key_id, 16);
        memcpy(&g_state.blacklist[i].revoked_at, meta,      8);
        memcpy(&g_state.blacklist[i].expires_at, meta + 8,  8);
        g_state.blacklist[i].reason    = meta[16];
        g_state.blacklist[i].used_slot = true;
    }
    g_state.blacklist_count = stored;
    p.end();
    return true;
}

bool save_local_blacklist_to_nvs() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;
    p.clear();
    uint16_t i_out = 0;
    for (uint16_t i = 0; i < LOCAL_BLACKLIST_CAP; i++) {
        if (!g_state.blacklist[i].used_slot) continue;
        char k_key[NVS_BL_KEYNAME_BUF], k_meta[NVS_BL_KEYNAME_BUF];
        snprintf(k_key,  sizeof(k_key),  NVS_KEYPREFIX_BL_KEY,  (unsigned)i_out);
        snprintf(k_meta, sizeof(k_meta), NVS_KEYPREFIX_BL_META, (unsigned)i_out);

        uint8_t meta[17];
        memcpy(meta,     &g_state.blacklist[i].revoked_at, 8);
        memcpy(meta + 8, &g_state.blacklist[i].expires_at, 8);
        meta[16] = g_state.blacklist[i].reason;

        p.putBytes(k_key,  g_state.blacklist[i].key_id, 16);
        p.putBytes(k_meta, meta, 17);
        i_out++;
    }
    p.putUShort("count", i_out);
    p.end();
    g_state.blacklist_count = i_out;
    return true;
}

int blacklist_find(const uint8_t key_id[16]) {
    for (uint16_t i = 0; i < LOCAL_BLACKLIST_CAP; i++) {
        if (g_state.blacklist[i].used_slot &&
            memcmp(g_state.blacklist[i].key_id, key_id, 16) == 0) {
            return (int)i;
        }
    }
    return -1;
}

// Write into slot i, persist, return the save result.
static bool blacklist_store_at(uint16_t i, const uint8_t key_id[16],
                               uint64_t revoked_at, uint64_t expires_at, uint8_t reason) {
    memcpy(g_state.blacklist[i].key_id, key_id, 16);
    g_state.blacklist[i].revoked_at = revoked_at;
    g_state.blacklist[i].expires_at = expires_at;
    g_state.blacklist[i].reason     = reason;
    g_state.blacklist[i].used_slot  = true;
    return save_local_blacklist_to_nvs();
}

bool blacklist_append(const uint8_t key_id[16], uint64_t revoked_at, uint64_t expires_at, uint8_t reason) {
    if (blacklist_find(key_id) >= 0) return true;   // idempotent
    // Phase 1b: new appends are bounded by the provisioned soft-cap (<= static MAX).
    uint16_t cap = blacklist_cap();
    for (uint16_t i = 0; i < cap; i++) {
        if (!g_state.blacklist[i].used_slot) {
            return blacklist_store_at(i, key_id, revoked_at, expires_at, reason);
        }
    }
    // FW-ARC-06: blacklist is full → evict-expired-then-fail-closed. Try to
    // reclaim one slot whose revocation has 100% expired (expires_at>0 && < now)
    // — safe to drop. If a slot is reclaimed, store the new revocation there.
    // If NO entry is expired (all still live), FAIL (return false): the caller
    // must NOT acknowledge a revocation that wasn't stored (fail-closed).
    uint64_t now = rtc_now();
    if (now != 0) {
        for (uint16_t i = 0; i < cap; i++) {
            if (g_state.blacklist[i].used_slot &&
                g_state.blacklist[i].expires_at > 0 &&
                g_state.blacklist[i].expires_at < now) {
                return blacklist_store_at(i, key_id, revoked_at, expires_at, reason);
            }
        }
    }
    return false;   // full and nothing expired → fail-closed
}

void blacklist_remove(const uint8_t key_id[16]) {
    int idx = blacklist_find(key_id);
    if (idx < 0) return;
    memset(&g_state.blacklist[idx], 0, sizeof(g_state.blacklist[idx]));
    save_local_blacklist_to_nvs();
}

void expire_local_blacklist_entries() {
    uint64_t now = rtc_now();
    if (now == 0) return;
    bool dirty = false;
    for (uint16_t i = 0; i < LOCAL_BLACKLIST_CAP; i++) {
        if (g_state.blacklist[i].used_slot &&
            g_state.blacklist[i].expires_at > 0 &&
            g_state.blacklist[i].expires_at < now) {
            memset(&g_state.blacklist[i], 0, sizeof(g_state.blacklist[i]));
            dirty = true;
        }
    }
    if (dirty) save_local_blacklist_to_nvs();
}

void issue_nonce(const uint8_t nonce[32]) {
    uint64_t now_ms = millis();
    // Find first free or oldest slot.
    int slot = -1;
    uint64_t oldest_ms = UINT64_MAX;
    uint16_t ring = nonce_ring_cap();   // Phase 1b: provisioned soft-cap (<= MAX)
    for (int i = 0; i < ring; i++) {
        if (!g_state.nonce_ring[i].used_slot) { slot = i; break; }
        if (g_state.nonce_ring[i].issued_at_ms < oldest_ms) {
            oldest_ms = g_state.nonce_ring[i].issued_at_ms;
            slot = i;
        }
    }
    if (slot < 0) slot = 0;
    memcpy(g_state.nonce_ring[slot].nonce, nonce, 32);
    g_state.nonce_ring[slot].issued_at_ms = now_ms;
    g_state.nonce_ring[slot].consumed  = false;
    g_state.nonce_ring[slot].used_slot = true;
    // T3: remember the most recently issued nonce. After PUSH_INFO this is the
    // tap's fresh_nonce, which op_handover_issue binds into the handover_token.
    memcpy(g_state.last_issued_nonce, nonce, 32);
}

bool consume_nonce(const uint8_t nonce[32]) {
    uint64_t now_ms = millis();
    uint16_t ring = nonce_ring_cap();   // Phase 1b: provisioned soft-cap (<= MAX)
    for (int i = 0; i < ring; i++) {
        if (!g_state.nonce_ring[i].used_slot) continue;
        if (g_state.nonce_ring[i].consumed) continue;
        if ((now_ms - g_state.nonce_ring[i].issued_at_ms) > g_state.cfg.nonce_ttl_ms) continue;
        if (memcmp(g_state.nonce_ring[i].nonce, nonce, 32) == 0) {
            g_state.nonce_ring[i].consumed = true;
            return true;
        }
    }
    return false;
}

void cleanup_stale_nonces() {
    uint64_t now_ms = millis();
    uint16_t ring = nonce_ring_cap();   // Phase 1b: provisioned soft-cap (<= MAX)
    for (int i = 0; i < ring; i++) {
        if (!g_state.nonce_ring[i].used_slot) continue;
        if ((now_ms - g_state.nonce_ring[i].issued_at_ms) > g_state.cfg.nonce_ttl_ms ||
            g_state.nonce_ring[i].consumed) {
            memset(&g_state.nonce_ring[i], 0, sizeof(g_state.nonce_ring[i]));
        }
    }
}
