#include "authoritative.h"
#include "reader_state.h"
#include "local.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../crypto/domains.h"
#include "../crypto/ed25519.h"
#include "../transport/op_sink.h"
#include <Preferences.h>
#include <SPIFFS.h>
#include <FS.h>
#include <string.h>
#include <stdlib.h>

static const char* NS = NVS_NS_AUTH;

static bool spiffs_mounted = false;

static bool ensure_spiffs() {
    if (spiffs_mounted) return true;
    // Mount the custom "filter" partition via SPIFFS API.
    // The partition label set in partitions.csv is "filter".
    spiffs_mounted = SPIFFS.begin(/*formatOnFail=*/true, FILTER_FS_BASE_PATH,
                                  FILTER_FS_MAX_OPEN_FILES, FILTER_PARTITION_LABEL);
    return spiffs_mounted;
}

bool load_authoritative_state() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/true)) return false;

    g_state.filter_version        = p.getULong64("flt_ver", 0);
    g_state.filter_m_bits         = p.getUInt   ("flt_m",   0);
    g_state.filter_k_hashes       = p.getUChar  ("flt_k",   0);
    g_state.filter_hash_seed      = p.getUInt   ("flt_seed",0);
    g_state.filter_generated_at   = p.getULong64("flt_gen", 0);
    g_state.delivery_received_at  = p.getULong64("d_rcv",   0);
    p.getBytes("d_courier", g_state.delivery_courier_id, 16);

    g_state.time_sync_last_at     = p.getULong64("ts_last", 0);
    p.getBytes("ts_auth",   g_state.time_sync_last_authority_id, 16);
    g_state.time_sync_last_kind   = p.getUChar  ("ts_kind", 0);

    g_state.blacklist_count       = p.getUShort ("bl_cnt",  0);

    String slot = p.getString("cur_slot", DEFAULT_FILTER_SLOT);
    g_state.filter_current_slot = (slot.length() > 0 && slot[0] == FILTER_SLOT_B) ? FILTER_SLOT_B : FILTER_SLOT_A;

    p.end();
    return true;
}

bool save_time_sync_state_to_nvs() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;
    p.putULong64("ts_last", g_state.time_sync_last_at);
    p.putBytes  ("ts_auth", g_state.time_sync_last_authority_id, 16);
    p.putUChar  ("ts_kind", g_state.time_sync_last_kind);
    p.end();
    return true;
}

bool save_filter_meta_to_nvs() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;
    p.putULong64("flt_ver",  g_state.filter_version);
    p.putUInt   ("flt_m",    g_state.filter_m_bits);
    p.putUChar  ("flt_k",    g_state.filter_k_hashes);
    p.putUInt   ("flt_seed", g_state.filter_hash_seed);
    p.putULong64("flt_gen",  g_state.filter_generated_at);
    p.putString ("cur_slot", g_state.filter_current_slot == FILTER_SLOT_B ? "B" : DEFAULT_FILTER_SLOT);
    p.end();
    return true;
}

bool save_delivery_record_to_nvs() {
    Preferences p;
    if (!p.begin(NS, /*readOnly=*/false)) return false;
    p.putULong64("d_rcv",     g_state.delivery_received_at);
    p.putBytes  ("d_courier", g_state.delivery_courier_id, 16);
    p.end();
    return true;
}

// ---------------------------------------------------------------------------
// File-backed op_sink. Wraps an open SPIFFS File so the PURE verifier
// (verify_filter_sink_sig in op_sink.cpp) and the from-flash commit path can
// read a slot via the same op_sink interface the host fake-flash implements.
// write() appends at the end; read() back-reads at an absolute offset; len()
// is the current file size. Keeping SPIFFS behind this struct is exactly what
// makes the two-pass verify host-testable (see N2/B6 design).
// ---------------------------------------------------------------------------
struct file_sink_ctx {
    File*    f;        // open file handle (owned by the caller)
    uint32_t used;     // bytes appended via write() (or initial size for read-only)
    bool     ok;       // cleared on a failed write
};

static bool file_sink_write(op_sink* s, const uint8_t* chunk, uint32_t len) {
    file_sink_ctx* c = (file_sink_ctx*)s->ctx;
    if (!c->ok || !c->f) return false;
    if (c->f->write(chunk, len) != len) { c->ok = false; return false; }
    c->used += len;
    return true;
}

static uint32_t file_sink_read(op_sink* s, uint32_t off, uint8_t* buf, uint32_t n) {
    file_sink_ctx* c = (file_sink_ctx*)s->ctx;
    if (!c->f) return 0;
    if (!c->f->seek(off)) return 0;
    int got = c->f->read(buf, n);
    return got > 0 ? (uint32_t)got : 0;
}

static uint32_t file_sink_len(op_sink* s) {
    return ((file_sink_ctx*)s->ctx)->used;
}

static void file_sink_bind(op_sink* s, file_sink_ctx* c, File* f, uint32_t initial_used) {
    c->f    = f;
    c->used = initial_used;
    c->ok   = true;
    s->write = file_sink_write;
    s->read  = file_sink_read;
    s->len   = file_sink_len;
    s->ctx   = c;
}

// FW-ARC-03: verify the Ed25519 server signature over the full filter_package
// stored in the slot file BEFORE the bloom/whitelist are parsed and trusted.
// Signed range is DOMAIN_FLT || package[0 : file_size-64]; the signature is the
// last 64 bytes (same layout op_filter_update verifies before persisting). A
// tampered flash filter would otherwise be trusted on boot. Streamed (no full
// in-RAM copy) so it works for packages up to MAX_FILTER_BYTES. Now delegates
// to the PURE verify_filter_sink_sig (op_sink.cpp) over a File-backed sink, so
// the device and the host fake-flash run byte-identical verify code.
static bool verify_filter_file_sig(File& f, uint32_t file_size) {
    file_sink_ctx fc;
    op_sink s;
    file_sink_bind(&s, &fc, &f, file_size);   // read-only: len == file size
    return verify_filter_sink_sig(&s, g_state.server_ed_pub, DOMAIN_FLT);
}

bool load_filter_from_flash() {
    if (!ensure_spiffs()) return false;

    const char* path = (g_state.filter_current_slot == FILTER_SLOT_B) ? FILTER_FILE_B : FILTER_FILE_A;
    if (!SPIFFS.exists(path)) return false;

    File f = SPIFFS.open(path, "r");
    if (!f) return false;

    uint32_t file_size = (uint32_t)f.size();

    // FW-ARC-03: verify the server signature over the whole package BEFORE
    // trusting any field. Refuse (no active filter) if invalid/malformed.
    if (!verify_filter_file_sig(f, file_size)) {
        f.close();
        Serial.println(F("[WARN] filter signature invalid; refusing to load (no active filter)"));
        return false;
    }
    // Re-position to the header for the trusted parse below.
    if (!f.seek(0)) { f.close(); return false; }

    // Read header (56 B).
    uint8_t header[56];
    if (f.read(header, 56) != 56) { f.close(); return false; }

    uint32_t filter_bytes_len = read_u32le(header + 42);
    uint16_t whitelist_count  = read_u16le(header + 46);
    uint16_t bl_delta_count   = read_u16le(header + 48);
    uint32_t m_bits           = read_u32le(header + 33);
    uint8_t  k_hashes         = header[37];
    uint32_t hash_seed        = read_u32le(header + 38);

    if (filter_bytes_len > MAX_FILTER_BYTES) { f.close(); return false; }

    // The signed package layout must hold exactly (same arithmetic op_filter_update
    // validates before persisting): header(56) + bloom + whitelist*24 + bl_delta*16
    // + sig(64). The bl_delta section sits after the whitelist; it is part of the
    // signed body even though load only consumes the bloom + whitelist.
    {
        uint64_t expected = 56ull + filter_bytes_len
                          + (uint64_t)whitelist_count * 24ull
                          + (uint64_t)bl_delta_count * 16ull
                          + 64ull;
        if (expected != file_size) {
            f.close();
            Serial.println(F("[WARN] filter package malformed (size mismatch); refusing to load"));
            return false;
        }
    }

    if (g_state.bloom_bytes) { free(g_state.bloom_bytes); g_state.bloom_bytes = nullptr; }
    g_state.bloom_bytes = (uint8_t*)malloc(filter_bytes_len);
    if (!g_state.bloom_bytes) { f.close(); return false; }
    g_state.bloom_bytes_len = filter_bytes_len;
    g_state.filter_m_bits   = m_bits;
    g_state.filter_k_hashes = k_hashes;
    g_state.filter_hash_seed= hash_seed;

    if (f.read(g_state.bloom_bytes, filter_bytes_len) != (int)filter_bytes_len) {
        f.close(); return false;
    }

    if (g_state.whitelist) { free(g_state.whitelist); g_state.whitelist = nullptr; }
    g_state.whitelist_count = whitelist_count;
    if (whitelist_count) {
        g_state.whitelist = (WhitelistEntry*)malloc(sizeof(WhitelistEntry) * whitelist_count);
        if (!g_state.whitelist) { f.close(); return false; }
        for (uint16_t i = 0; i < whitelist_count; i++) {
            uint8_t e[24];
            if (f.read(e, 24) != 24) { f.close(); return false; }
            memcpy(g_state.whitelist[i].key_id, e, 16);
            g_state.whitelist[i].expires_at = read_u64le(e + 16);
        }
    }
    f.close();
    return true;
}

bool apply_new_filter(const uint8_t* filter_package, size_t total_len,
                      uint64_t filter_version,
                      uint32_t m_bits, uint8_t k_hashes, uint32_t hash_seed,
                      uint64_t generated_at,
                      uint32_t filter_bytes_len,
                      uint16_t whitelist_count,
                      const uint8_t* courier_id,
                      uint64_t received_at) {
    if (!ensure_spiffs()) return false;

    const char* new_path = (g_state.filter_current_slot == FILTER_SLOT_A) ? FILTER_FILE_B : FILTER_FILE_A;

    if (SPIFFS.exists(new_path)) SPIFFS.remove(new_path);
    File f = SPIFFS.open(new_path, "w");
    if (!f) return false;
    if (f.write(filter_package, total_len) != total_len) { f.close(); return false; }
    f.close();

    // Atomic swap: persist new active slot in NVS.
    g_state.filter_current_slot = (g_state.filter_current_slot == FILTER_SLOT_A) ? FILTER_SLOT_B : FILTER_SLOT_A;
    g_state.filter_version      = filter_version;
    g_state.filter_m_bits       = m_bits;
    g_state.filter_k_hashes     = k_hashes;
    g_state.filter_hash_seed    = hash_seed;
    g_state.filter_generated_at = generated_at;
    memcpy(g_state.delivery_courier_id, courier_id, 16);
    g_state.delivery_received_at = received_at;

    if (!save_filter_meta_to_nvs()) return false;
    if (!save_delivery_record_to_nvs()) return false;

    // Reload bloom into RAM.
    return load_filter_from_flash();
}

// ---------------------------------------------------------------------------
// N2/B6 — flash_slot_sink: stream a big FILTER_UPDATE straight into the
// INACTIVE slot. Module-static backing storage: only one FILTER_UPDATE is ever
// in flight per session, and the receive path is single-threaded (NFC main loop
// / BLE ble_loop_tick), so a single static File+ctx is safe and avoids a heap
// alloc on a memory-tight path.
// ---------------------------------------------------------------------------
static File           g_slot_file;
static file_sink_ctx  g_slot_ctx;

bool flash_slot_sink_open(op_sink* s) {
    if (!ensure_spiffs()) return false;
    // The inactive slot is the one that is NOT current — same target
    // apply_new_filter writes to. The active filter stays untouched until swap.
    const char* new_path = (g_state.filter_current_slot == FILTER_SLOT_A) ? FILTER_FILE_B : FILTER_FILE_A;
    if (SPIFFS.exists(new_path)) SPIFFS.remove(new_path);
    // "w+" → create/truncate for write, but also allow the back-read pass
    // (verify + parse) without reopening.
    g_slot_file = SPIFFS.open(new_path, "w+");
    if (!g_slot_file) return false;
    file_sink_bind(s, &g_slot_ctx, &g_slot_file, /*initial_used=*/0);
    return true;
}

void flash_slot_sink_close(op_sink* s) {
    (void)s;
    if (g_slot_file) {
        g_slot_file.flush();
        g_slot_file.close();
    }
    g_slot_ctx.f = nullptr;
}

bool commit_filter_from_flash(op_sink* s,
                              uint64_t filter_version,
                              uint32_t m_bits, uint8_t k_hashes, uint32_t hash_seed,
                              uint64_t generated_at,
                              uint32_t filter_bytes_len,
                              uint16_t whitelist_count,
                              uint16_t bl_delta_count,
                              const uint8_t* courier_id,
                              uint64_t received_at) {
    // Flush writes so the back-read sees the full package (the file stays open).
    if (g_slot_file) g_slot_file.flush();

    uint32_t total = s->len(s);

    // Layout sanity: the streamed size must match the header arithmetic
    // (same check load_filter_from_flash / op_filter_update apply). Reject
    // before verify if malformed — never swap on a structurally bad package.
    uint64_t expected = 56ull + (uint64_t)filter_bytes_len
                      + (uint64_t)whitelist_count * 24ull
                      + (uint64_t)bl_delta_count * 16ull
                      + 64ull;
    if ((uint64_t)total != expected) return false;

    // Pass 1+2: verify the server signature by reading the slot back. On
    // invalid → return WITHOUT swapping; the active filter is untouched.
    if (!verify_filter_sink_sig(s, g_state.server_ed_pub, DOMAIN_FLT)) return false;

    // Apply blacklist_delta (read from the slot): remove absorbed keys locally.
    // bl_delta sits at 56 + bloom + whitelist*24, each entry 16 B.
    uint32_t bl_off = 56u + filter_bytes_len + (uint32_t)whitelist_count * 24u;
    for (uint16_t i = 0; i < bl_delta_count; i++) {
        uint8_t key_id[16];
        if (s->read(s, bl_off + (uint32_t)i * 16u, key_id, 16) != 16) return false;
        blacklist_remove(key_id);
    }

    // Atomic swap: the freshly written slot becomes current. (No second copy —
    // the package is already persisted in that slot file.)
    g_slot_file.flush();
    g_slot_file.close();
    g_slot_ctx.f = nullptr;

    g_state.filter_current_slot = (g_state.filter_current_slot == FILTER_SLOT_A) ? FILTER_SLOT_B : FILTER_SLOT_A;
    g_state.filter_version      = filter_version;
    g_state.filter_m_bits       = m_bits;
    g_state.filter_k_hashes     = k_hashes;
    g_state.filter_hash_seed    = hash_seed;
    g_state.filter_generated_at = generated_at;
    memcpy(g_state.delivery_courier_id, courier_id, 16);
    g_state.delivery_received_at = received_at;

    if (!save_filter_meta_to_nvs()) return false;
    if (!save_delivery_record_to_nvs()) return false;

    // Reload bloom into RAM from the now-current slot.
    return load_filter_from_flash();
}
