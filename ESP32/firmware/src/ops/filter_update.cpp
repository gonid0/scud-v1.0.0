// FILTER_UPDATE handler (shared §5.6, §12).
// MVP buffered variant: phone передаёт весь filter_package в памяти.
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

static size_t make_flt_result(uint8_t* resp, uint8_t code,
                              const uint8_t* delivery_receipt_112) {
    uint8_t next_nonce[32];
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(next_nonce + i * 4, &r, 4);
    }
    issue_nonce(next_nonce);

    if (delivery_receipt_112 && code == RES_OK) {
        uint8_t ext[112 + 32];
        memcpy(ext, delivery_receipt_112, 112);
        memcpy(ext + 112, next_nonce, 32);
        return make_op_result(resp, INNER_FILTER_UPDATE, MARK_OP_RESULT_FLT, code, ext, 144);
    }
    return make_op_result(resp, INNER_FILTER_UPDATE, MARK_OP_RESULT_FLT, code, next_nonce, 32);
}

size_t op_filter_update(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    if (resp_max < FLT_RESP_MIN) return 0;

    // Layout: inner_opcode(1) + courier_id(16) + filter_package(var)
    if (req_len < 1 + 16 + 56 + 64) return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    const uint8_t* courier_id = req + 1;
    const uint8_t* fpkg       = req + 17;
    size_t fpkg_len           = req_len - 17;

    if (fpkg[0] != 0x01) return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    const uint8_t* fpkg_reader_id = fpkg + 1;
    uint64_t filter_version = read_u64le(fpkg + 17);
    uint64_t generated_at   = read_u64le(fpkg + 25);
    uint32_t m_bits         = read_u32le(fpkg + 33);
    uint8_t  k_hashes       = fpkg[37];
    uint32_t hash_seed      = read_u32le(fpkg + 38);
    uint32_t filter_bytes_len = read_u32le(fpkg + 42);
    uint16_t whitelist_count  = read_u16le(fpkg + 46);
    uint16_t bl_delta_count   = read_u16le(fpkg + 48);

    uint64_t now = rtc_now();

    if (memcmp(fpkg_reader_id, g_state.reader_id, 16) != 0)
        return make_flt_result(resp, RES_WRONG_READER, nullptr);
    if (filter_version <= g_state.filter_version)
        return make_flt_result(resp, RES_STALE, nullptr);
    if (now && generated_at > now + g_state.cfg.clock_skew)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if ((m_bits & 7u) != 0 || filter_bytes_len != m_bits / 8u)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if (filter_bytes_len > MAX_FILTER_BYTES)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if (whitelist_count > g_state.cfg.wl_max || bl_delta_count > g_state.cfg.bld_max)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    size_t expected = 56 + filter_bytes_len + whitelist_count * 24 + bl_delta_count * 16 + 64;
    if (fpkg_len != expected) return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    // Verify server_signature: DOMAIN_FLT || fpkg[0 .. fpkg_len - 64]
    const uint8_t* sig = fpkg + (fpkg_len - 64);
    {
        // T2: streaming verify — feed the message incrementally instead of copying
        // DOMAIN_FLT||body into a fresh ~127 KB buffer for the one-shot check. Same
        // result (host-tested), and the body never needs a second full copy in RAM.
        // (Full removal of the transfer-layer 16 KB op_buf cap — streaming straight
        // to a flash slot and re-reading for this verify — is the remaining step.)
        ed25519_verify_stream_ctx vc;
        ed25519_verify_stream_init(&vc, sig, g_state.server_ed_pub);
        ed25519_verify_stream_update(&vc, DOMAIN_FLT, 16);
        ed25519_verify_stream_update(&vc, fpkg, fpkg_len - 64);
        if (!ed25519_verify_stream_final(&vc))
            return make_flt_result(resp, RES_BAD_SIGNATURE, nullptr);
    }

    // Apply delta: remove absorbed keys from local blacklist.
    const uint8_t* bl_delta = fpkg + 56 + filter_bytes_len + whitelist_count * 24;
    for (uint16_t i = 0; i < bl_delta_count; i++) {
        blacklist_remove(bl_delta + i * 16);
    }

    // Persist filter file + meta.
    uint64_t applied_at = now ? now : 0;
    if (!apply_new_filter(fpkg, fpkg_len, filter_version,
                          m_bits, k_hashes, hash_seed, generated_at,
                          filter_bytes_len, whitelist_count,
                          courier_id, applied_at)) {
        return make_flt_result(resp, RES_INTERNAL_ERROR, nullptr);
    }

    // Build delivery_receipt (112 B).
    uint8_t receipt[112];
    memcpy(receipt, g_state.reader_id, 16);
    write_u64le(receipt + 16, filter_version);
    write_u64le(receipt + 24, applied_at);
    memcpy(receipt + 32, courier_id, 16);
    {
        uint8_t to_sign[16 + 48];
        memcpy(to_sign, DOMAIN_RCP, 16);
        memcpy(to_sign + 16, receipt, 48);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, sizeof(to_sign), receipt + 48);
    }

    return make_flt_result(resp, RES_OK, receipt);
}

// N2/B6 — from-flash variant. The transport layer has already streamed the full
// filter_package into `sink` (the inactive A/B slot). Only the package header
// (first 56 B) is in RAM; everything else (bloom/whitelist/bl_delta/sig) is read
// back from flash. Header validations mirror op_filter_update exactly; the
// signature verify + blacklist-delta + atomic swap happen in
// commit_filter_from_flash (verify-from-flash, no 16 KB op_buf). This keeps the
// hot small-op RAM path (op_filter_update above) untouched.
size_t op_filter_update_from_flash(const uint8_t* courier_id,
                                   const uint8_t* fpkg, size_t fpkg_header_len,
                                   op_sink* sink,
                                   uint8_t* resp, size_t resp_max) {
    if (resp_max < FLT_RESP_MIN) return 0;

    // Need the full 56-B package header in RAM to validate before trusting flash.
    if (fpkg_header_len < 56) return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if (fpkg[0] != 0x01)      return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    const uint8_t* fpkg_reader_id = fpkg + 1;
    uint64_t filter_version   = read_u64le(fpkg + 17);
    uint64_t generated_at     = read_u64le(fpkg + 25);
    uint32_t m_bits           = read_u32le(fpkg + 33);
    uint8_t  k_hashes         = fpkg[37];
    uint32_t hash_seed        = read_u32le(fpkg + 38);
    uint32_t filter_bytes_len = read_u32le(fpkg + 42);
    uint16_t whitelist_count  = read_u16le(fpkg + 46);
    uint16_t bl_delta_count   = read_u16le(fpkg + 48);

    uint64_t now = rtc_now();

    if (memcmp(fpkg_reader_id, g_state.reader_id, 16) != 0)
        return make_flt_result(resp, RES_WRONG_READER, nullptr);
    if (filter_version <= g_state.filter_version)
        return make_flt_result(resp, RES_STALE, nullptr);
    if (now && generated_at > now + g_state.cfg.clock_skew)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if ((m_bits & 7u) != 0 || filter_bytes_len != m_bits / 8u)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if (filter_bytes_len > MAX_FILTER_BYTES)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);
    if (whitelist_count > g_state.cfg.wl_max || bl_delta_count > g_state.cfg.bld_max)
        return make_flt_result(resp, RES_BAD_FORMAT, nullptr);

    // Size match (vs streamed length) + signature verify + delta + atomic swap.
    // commit returns false on size-mismatch OR bad signature → the active filter
    // is never swapped (RES_BAD_SIGNATURE covers both: a forged/short package).
    uint64_t applied_at = now ? now : 0;
    if (!commit_filter_from_flash(sink, filter_version, m_bits, k_hashes, hash_seed,
                                  generated_at, filter_bytes_len, whitelist_count,
                                  bl_delta_count, courier_id, applied_at)) {
        return make_flt_result(resp, RES_BAD_SIGNATURE, nullptr);
    }

    // Build delivery_receipt (112 B) — identical to the RAM path.
    uint8_t receipt[112];
    memcpy(receipt, g_state.reader_id, 16);
    write_u64le(receipt + 16, filter_version);
    write_u64le(receipt + 24, applied_at);
    memcpy(receipt + 32, courier_id, 16);
    {
        uint8_t to_sign[16 + 48];
        memcpy(to_sign, DOMAIN_RCP, 16);
        memcpy(to_sign + 16, receipt, 48);
        ed25519_sign(g_state.reader_ed_priv, g_state.reader_ed_pub,
                     to_sign, sizeof(to_sign), receipt + 48);
    }

    return make_flt_result(resp, RES_OK, receipt);
}
