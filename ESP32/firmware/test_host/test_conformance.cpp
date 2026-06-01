// Host-side cross-implementation conformance test for the firmware crypto.
//
// Asserts the firmware C/C++ crypto reproduces the SAME golden vectors as the
// Backend reference (docs/test_vectors/protocol_v1.json, via the generated
// vectors_generated.h). This is the third side of the byte-exact protocol
// (Python backend + Kotlin Android + C++ firmware) — a single divergent bit
// breaks Ed25519 verify against a real reader.
//
// Pure modules only (no Arduino / ESP-IDF): blake2s, key_id, murmur3, bloom,
// domains. Build/run: see test_host/README.md (MSVC locally, gcc on CI).
//
// Returns 0 if all pass, 1 otherwise.

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "blake2s.h"
#include "key_id.h"
#include "murmur3.h"
#include "bloom.h"
#include "domains.h"
#include "ed25519.h"
#include "ble_frame.h"
#include "vectors_generated.h"

static int g_fail = 0;

static void check(bool ok, const char* name) {
    std::printf("  %s %s\n", ok ? "[PASS]" : "[FAIL]", name);
    if (!ok) g_fail++;
}

static bool eq(const uint8_t* a, const uint8_t* b, size_t n) {
    return std::memcmp(a, b, n) == 0;
}

// BLE framing: collect each PDU emitted by ble_frame_message into a flat buffer
// + per-frame lengths, so we can compare against the golden concat + lens.
struct CollectCtx {
    uint8_t  buf[2048];
    size_t   total;
    uint16_t lens[64];
    int      count;
};
static void collect_sink(void* ctx, const uint8_t* pdu, size_t len) {
    CollectCtx* c = static_cast<CollectCtx*>(ctx);
    std::memcpy(c->buf + c->total, pdu, len);
    c->total += len;
    c->lens[c->count++] = (uint16_t)len;
}
static void run_framing_vector(const uint8_t* payload, uint32_t plen, uint16_t max_pdu,
                               const uint8_t* concat, size_t concat_len,
                               const uint16_t* lens, int nframes, const char* label) {
    CollectCtx c;
    c.total = 0;
    c.count = 0;
    ble_frame_message(payload, plen, max_pdu, collect_sink, &c);
    bool ok = (c.count == nframes) &&
              (c.total == concat_len) &&
              (std::memcmp(c.buf, concat, concat_len) == 0) &&
              (std::memcmp(c.lens, lens, (size_t)nframes * sizeof(uint16_t)) == 0);
    char name[80];
    std::snprintf(name, sizeof(name), "ble_framing %s", label);
    check(ok, name);
}

// BLE reassembly (consume side): feed the golden PDUs through ble_reasm_feed (the
// same pure function the firmware OpWrite callback now uses) and expect the
// original payload back, with NEED_MORE until the LAST frame.
static void run_reasm_vector(const uint8_t* concat, const uint16_t* lens, int nframes,
                             const uint8_t* payload, uint32_t plen, const char* label) {
    uint8_t  buf[2048];
    uint32_t total_len = 0, got_len = 0;
    uint8_t  next_seq = 0;
    bool     in_progress = false;
    ble_reasm_status rs = BLE_REASM_IGNORED;
    bool partial_ok = true;
    size_t off = 0;
    for (int i = 0; i < nframes; i++) {
        rs = ble_reasm_feed(concat + off, lens[i], buf, sizeof(buf),
                            &total_len, &got_len, &next_seq, &in_progress);
        off += lens[i];
        if (i < nframes - 1 && rs != BLE_REASM_NEED_MORE) partial_ok = false;
    }
    bool ok = partial_ok && (rs == BLE_REASM_DONE) &&
              (got_len == plen) && (std::memcmp(buf, payload, plen) == 0);
    char name[80];
    std::snprintf(name, sizeof(name), "ble_reasm %s", label);
    check(ok, name);
}

int main() {
    std::printf("firmware conformance vs docs/test_vectors/protocol_v1.json\n");

    // --- signing domain tags (16 B each) ---
    check(eq(DOMAIN_KEY, V_DOMAIN_KEY, 16), "domain RDR-KEY-v1");
    check(eq(DOMAIN_INF, V_DOMAIN_INF, 16), "domain RDR-INF-v1");
    check(eq(DOMAIN_RSP, V_DOMAIN_RSP, 16), "domain RDR-RSP-v1");
    check(eq(DOMAIN_FLT, V_DOMAIN_FLT, 16), "domain RDR-FLT-v1");
    check(eq(DOMAIN_RCP, V_DOMAIN_RCP, 16), "domain RDR-RCP-v1");
    check(eq(DOMAIN_BLK, V_DOMAIN_BLK, 16), "domain RDR-BLK-v1");
    check(eq(DOMAIN_FDI, V_DOMAIN_FDI, 16), "domain RDR-FDI-v1");
    check(eq(DOMAIN_TGR, V_DOMAIN_TGR, 16), "domain RDR-TGR-v1");
    check(eq(DOMAIN_TIM, V_DOMAIN_TIM, 16), "domain RDR-TIM-v1");
    check(eq(DOMAIN_REV, V_DOMAIN_REV, 16), "domain RDR-REV-v1");
    check(eq(DOMAIN_PSG, V_DOMAIN_PSG, 16), "domain RDR-PSG-v1");
    check(eq(DOMAIN_BLE, V_DOMAIN_BLE, 16), "domain RDR-BLE-v1");

    // --- key_id = BLAKE2s(reader_id || phone_pubkey || issued_at_LE || serial_LE) ---
    uint8_t kid[16];
    compute_key_id(V_KEYID_READER, V_KEYID_PHONE, V_KEYID_ISSUED, V_KEYID_SERIAL, kid);
    check(eq(kid, V_KEYID_EXPECTED, 16), "key_id (BLAKE2s)");

    // --- raw BLAKE2s-128 (streaming API) ---
    uint8_t bout[16];
    blake2s_state S;
    blake2s_init(&S, 16);
    blake2s_update(&S, V_BLAKE_IN, V_BLAKE_IN_LEN);
    blake2s_final(&S, bout, 16);
    check(eq(bout, V_BLAKE_EXPECTED, 16), "blake2s-128");

    // --- MurmurHash3 x86_32 (bloom hash) ---
    uint32_t mh = murmur3_x86_32(V_MURMUR_DATA, V_MURMUR_LEN, V_MURMUR_SEED);
    check(mh == V_MURMUR_EXPECTED, "murmur3_x86_32");

    // --- bloom membership (the reader checks, it does not build) ---
    check(bloom_contains(V_BLOOM_BYTES, V_BLOOM_M, V_BLOOM_K, V_BLOOM_SEED, V_BLOOM_KID0),
          "bloom_contains kid0");
    check(bloom_contains(V_BLOOM_BYTES, V_BLOOM_M, V_BLOOM_K, V_BLOOM_SEED, V_BLOOM_KID1),
          "bloom_contains kid1");

    // --- Ed25519 domain-separated signature (Monocypher) ---
    // message = domain_tag(16) || payload; vector domain is RDR-KEY-v1.
    uint8_t msg[16 + 64];
    std::memcpy(msg, V_DOMAIN_KEY, 16);
    std::memcpy(msg + 16, V_ED_PAYLOAD, V_ED_PAYLOAD_LEN);
    const size_t msglen = 16 + V_ED_PAYLOAD_LEN;
    uint8_t pub[32];
    ed25519_derive_pub(V_ED_SEED, pub);
    check(eq(pub, V_ED_PUBKEY, 32), "ed25519 derive_pub");
    uint8_t sig[64];
    ed25519_sign(V_ED_SEED, pub, msg, msglen, sig);
    check(eq(sig, V_ED_SIG, 64), "ed25519 signature (Monocypher == nacl == BouncyCastle)");
    check(ed25519_verify(pub, msg, msglen, sig), "ed25519 verify");

    // --- issued_key (151 B): parse layout + server signature ---
    // fmt(1) reader(16) phone(32) issued(8LE) expires(8LE) permit(16) serial(4LE) payload(2) sig(64)
    {
        const uint8_t* b = V_IK_BLOB;
        uint64_t issued = 0;
        for (int i = 0; i < 8; i++) issued |= (uint64_t)b[49 + i] << (8 * i);
        uint32_t serial = 0;
        for (int i = 0; i < 4; i++) serial |= (uint32_t)b[81 + i] << (8 * i);
        uint8_t kid[16];
        compute_key_id(b + 1, b + 17, issued, serial, kid);
        check(eq(kid, V_IK_KEYID, 16), "issued_key key_id (parsed offsets)");

        uint64_t expires = 0;
        for (int i = 0; i < 8; i++) expires |= (uint64_t)b[57 + i] << (8 * i);
        check(expires == V_IK_EXPIRES, "issued_key expires_at @57");
        check(eq(b + 65, V_IK_PERMIT, 16), "issued_key permit_id @65");

        uint8_t ikmsg[16 + 87];
        std::memcpy(ikmsg, DOMAIN_KEY, 16);
        std::memcpy(ikmsg + 16, b, 87);
        check(ed25519_verify(V_IK_SERVER_PUB, ikmsg, 16 + 87, b + 87),
              "issued_key server signature (DOMAIN_KEY)");
    }

    // --- time_grant (148 B): parse layout + server signature ---
    // fmt(1) reader(16) auth_pub(32) auth_id(16) issued(8) expires(8) kind(1) pad(2) sig(64)
    {
        const uint8_t* b = V_TG_BLOB;
        check(eq(b + 49, V_TG_AUTHID, 16), "time_grant authority_id @49");
        check(b[81] == V_TG_KIND, "time_grant kind @81");
        uint8_t tgmsg[16 + 84];
        std::memcpy(tgmsg, DOMAIN_TGR, 16);
        std::memcpy(tgmsg + 16, b, 84);
        check(ed25519_verify(V_TG_SERVER_PUB, tgmsg, 16 + 84, b + 84),
              "time_grant server signature (DOMAIN_TGR)");
    }

    // --- passage_receipt (192 B): parse layout + reader signature (DOMAIN_PSG over body[0:128]) ---
    // fmt(1) reader(16) nonce(16) key_id(16) passed(8) dir(1) verdict(1) sseq(4) flags(1)
    //   phone(32) permit(16) ik_issued(8) ik_serial(4) pad(4) sig(64)
    //
    // H3 FOLD-IN: this exact 192-B structure is now APPENDED to the ACCESS_VERDICT
    // response at offset 42 (response[42:234], present IFF result==RES_OK). The
    // signed region is response[42:170], the signature response[170:234]. The
    // receipt BYTES are byte-identical to this standalone vector, so this check
    // also proves the embedded slice. The phone uploads the bare body
    // (response[42:234]) as the existing "passage_receipt" report — backend
    // parse/verify/dedup unchanged. NOTE: a new ACCESS_VERDICT-with-receipt golden
    // vector (234-B-on-OK / 42-B-on-deny) must be regenerated into the JSON corpus
    // (docs/test_vectors) by the docs agent; this firmware-side check pins the
    // receipt-body layout in the meantime.
    {
        const uint8_t* b = V_PR_BLOB;
        check(b[0] == 0x01, "passage_receipt format_version @0");
        check(eq(b + 33, V_PR_KEYID, 16), "passage_receipt key_id @33");
        uint64_t passed = 0;
        for (int i = 0; i < 8; i++) passed |= (uint64_t)b[49 + i] << (8 * i);
        check(passed == V_PR_PASSED_AT, "passage_receipt passed_at @49");
        check(b[57] == V_PR_DIRECTION, "passage_receipt direction @57");
        check(b[58] == 0x00, "passage_receipt verdict @58 (OK)");
        uint32_t sseq = 0;
        for (int i = 0; i < 4; i++) sseq |= (uint32_t)b[59 + i] << (8 * i);
        check(sseq == V_PR_SESSION_SEQ, "passage_receipt session_seq @59");
        check(eq(b + 64, V_PR_PHONE, 32), "passage_receipt phone_pubkey @64");
        check(eq(b + 96, V_PR_PERMIT, 16), "passage_receipt permit_id @96");
        bool pad0 = true;
        for (int i = 124; i < 128; i++) if (b[i]) pad0 = false;
        check(pad0, "passage_receipt padding @124 zeroed");
        uint8_t prmsg[16 + 128];
        std::memcpy(prmsg, DOMAIN_PSG, 16);
        std::memcpy(prmsg + 16, b, 128);
        check(ed25519_verify(V_PR_READER_PUB, prmsg, 16 + 128, b + 128),
              "passage_receipt reader signature (DOMAIN_PSG)");
    }

    // --- INFO (146 B, shared §5.2): parse layout + reader signature (DOMAIN_INF over header[0:82]) ---
    // fmt(1) reader(16) time(8) proto(1) maxapdu(2) fver(8) fdeliv(8) blcount(2) nonce(32) sseq(4) sig(64)
    {
        const uint8_t* b = V_INFO_BLOB;
        check(b[0] == 0x01, "info format_version @0");
        check(eq(b + 1, V_INFO_READER, 16), "info reader_id @1");
        uint64_t rtime = 0;
        for (int i = 0; i < 8; i++) rtime |= (uint64_t)b[17 + i] << (8 * i);
        check(rtime == V_INFO_READER_TIME, "info reader_time @17");
        check(b[25] == V_INFO_PROTO, "info protocol_version @25");
        uint16_t mapdu = (uint16_t)(b[26] | (b[27] << 8));
        check(mapdu == V_INFO_MAX_APDU, "info max_apdu_size @26");
        uint64_t fver = 0;
        for (int i = 0; i < 8; i++) fver |= (uint64_t)b[28 + i] << (8 * i);
        check(fver == V_INFO_FILTER_VER, "info filter_version @28");
        uint64_t fdel = 0;
        for (int i = 0; i < 8; i++) fdel |= (uint64_t)b[36 + i] << (8 * i);
        check(fdel == V_INFO_FILTER_DELIV, "info filter_delivered_at @36");
        uint16_t blc = (uint16_t)(b[44] | (b[45] << 8));
        check(blc == V_INFO_BL_COUNT, "info blacklist_count @44");
        check(eq(b + 46, V_INFO_NONCE, 32), "info fresh_nonce @46");
        uint32_t sseq = 0;
        for (int i = 0; i < 4; i++) sseq |= (uint32_t)b[78 + i] << (8 * i);
        check(sseq == V_INFO_SESSION_SEQ, "info session_seq @78");
        uint8_t infmsg[16 + 82];
        std::memcpy(infmsg, DOMAIN_INF, 16);
        std::memcpy(infmsg + 16, b, 82);
        check(ed25519_verify(V_INFO_READER_PUB, infmsg, 16 + 82, b + 82),
              "info reader signature (DOMAIN_INF)");
    }

    // --- delivery_receipt (112 B, shared §5.7): parse layout + reader signature (DOMAIN_RCP over bytes[0:48]) ---
    // reader(16) applied_filter_version(8LE) applied_at(8LE) courier(16) sig(64)
    {
        const uint8_t* b = V_DR_BLOB;
        check(eq(b + 0, V_DR_READER, 16), "delivery_receipt reader_id @0");
        uint64_t ver = 0;
        for (int i = 0; i < 8; i++) ver |= (uint64_t)b[16 + i] << (8 * i);
        check(ver == V_DR_APPLIED_VER, "delivery_receipt applied_filter_version @16");
        uint64_t at = 0;
        for (int i = 0; i < 8; i++) at |= (uint64_t)b[24 + i] << (8 * i);
        check(at == V_DR_APPLIED_AT, "delivery_receipt applied_at @24");
        check(eq(b + 32, V_DR_COURIER, 16), "delivery_receipt courier_id @32");
        uint8_t drmsg[16 + 48];
        std::memcpy(drmsg, DOMAIN_RCP, 16);
        std::memcpy(drmsg + 16, b, 48);
        check(ed25519_verify(V_DR_READER_PUB, drmsg, 16 + 48, b + 48),
              "delivery_receipt reader signature (DOMAIN_RCP)");
    }

    // --- FDI result (241 B, shared §5.8): parse layout + reader signature (DOMAIN_FDI over bytes[0:145]) ---
    // marker(1)=0x91 reader(16) fver(8LE) fdeliv(8LE) courier_blob(104) rtime(8LE) sig(64) next_nonce(32)
    // encrypted_courier_blob @33 is opaque filler in the vector; next_nonce @209 is NOT in the signed range.
    {
        const uint8_t* b = V_FDI_BLOB;
        check(b[0] == 0x91, "fdi result_marker @0 (0x91)");
        check(eq(b + 1, V_FDI_READER, 16), "fdi reader_id @1");
        uint64_t fver = 0;
        for (int i = 0; i < 8; i++) fver |= (uint64_t)b[17 + i] << (8 * i);
        check(fver == V_FDI_FILTER_VER, "fdi filter_version @17");
        uint64_t fdel = 0;
        for (int i = 0; i < 8; i++) fdel |= (uint64_t)b[25 + i] << (8 * i);
        check(fdel == V_FDI_DELIV, "fdi filter_delivered_at @25");
        check(eq(b + 33, V_FDI_COURIER, 104), "fdi encrypted_courier_blob @33 (opaque filler)");
        uint64_t rt = 0;
        for (int i = 0; i < 8; i++) rt |= (uint64_t)b[137 + i] << (8 * i);
        check(rt == V_FDI_TIME, "fdi reader_time_now @137");
        check(eq(b + 209, V_FDI_NONCE, 32), "fdi next_nonce @209 (unsigned)");
        uint8_t fdmsg[16 + 145];
        std::memcpy(fdmsg, DOMAIN_FDI, 16);
        std::memcpy(fdmsg + 16, b, 145);
        check(ed25519_verify(V_FDI_READER_PUB, fdmsg, 16 + 145, b + 145),
              "fdi reader signature (DOMAIN_FDI over bytes[0:145])");
    }

    // --- handover_token (167 B, shared §17.1): parse layout + reader signature
    //     (DOMAIN_BLE over bytes[0:103]). NFC→BLE handover (plan 08 §4.3). The
    //     reader signs the token and (on PRESENT) verifies its OWN signature, so the
    //     conformance check is the reader-sig path the firmware op_handover_present
    //     runs at runtime — proven byte-identical across PyNaCl/BouncyCastle/Monocypher. ---
    // marker(1)=0x99 reader(16) phone(32) ble_addr(6) tap_nonce(32) issued(8LE) expires(8LE) sig(64)
    {
        const uint8_t* b = V_HOV_BLOB;
        check(b[0] == V_HOV_FORMAT && b[0] == 0x99, "handover_token format_version @0 (0x99)");
        check(eq(b + 1, V_HOV_READER, 16), "handover_token reader_id @1");
        check(eq(b + 17, V_HOV_PHONE, 32), "handover_token issued_to_phone_pubkey @17");
        check(eq(b + 49, V_HOV_BLEADDR, 6), "handover_token reader_ble_addr @49");
        check(eq(b + 55, V_HOV_TAPNONCE, 32), "handover_token tap_nonce @55");
        uint64_t issued = 0;
        for (int i = 0; i < 8; i++) issued |= (uint64_t)b[87 + i] << (8 * i);
        check(issued == V_HOV_ISSUED_AT, "handover_token issued_at @87");
        uint64_t expires = 0;
        for (int i = 0; i < 8; i++) expires |= (uint64_t)b[95 + i] << (8 * i);
        check(expires == V_HOV_EXPIRES_AT, "handover_token expires_at @95");
        check(expires == issued + 60, "handover_token expires_at == issued_at + TTL(60)");
        uint8_t hovmsg[16 + 103];
        std::memcpy(hovmsg, DOMAIN_BLE, 16);
        std::memcpy(hovmsg + 16, b, 103);
        check(ed25519_verify(V_HOV_READER_PUB, hovmsg, 16 + 103, b + 103),
              "handover_token reader signature (DOMAIN_BLE over bytes[0:103])");
    }

    // --- BLE chunked framing (§16.5): produce side reproduces the golden PDU
    //     sequence (chunk boundaries + headers) exactly. ble_frame_message is the
    //     SAME pure function notify_chunked uses on the device. ---
    run_framing_vector(V_BLEF0_PAYLOAD, V_BLEF0_PAYLOAD_LEN, V_BLEF0_MAXPDU,
                       V_BLEF0_CONCAT, V_BLEF0_CONCAT_LEN, V_BLEF0_LENS, V_BLEF0_NFRAMES, V_BLEF0_LABEL);
    run_framing_vector(V_BLEF1_PAYLOAD, V_BLEF1_PAYLOAD_LEN, V_BLEF1_MAXPDU,
                       V_BLEF1_CONCAT, V_BLEF1_CONCAT_LEN, V_BLEF1_LENS, V_BLEF1_NFRAMES, V_BLEF1_LABEL);
    run_framing_vector(V_BLEF2_PAYLOAD, V_BLEF2_PAYLOAD_LEN, V_BLEF2_MAXPDU,
                       V_BLEF2_CONCAT, V_BLEF2_CONCAT_LEN, V_BLEF2_LENS, V_BLEF2_NFRAMES, V_BLEF2_LABEL);
    run_framing_vector(V_BLEF3_PAYLOAD, V_BLEF3_PAYLOAD_LEN, V_BLEF3_MAXPDU,
                       V_BLEF3_CONCAT, V_BLEF3_CONCAT_LEN, V_BLEF3_LENS, V_BLEF3_NFRAMES, V_BLEF3_LABEL);
    run_framing_vector(V_BLEF4_PAYLOAD, V_BLEF4_PAYLOAD_LEN, V_BLEF4_MAXPDU,
                       V_BLEF4_CONCAT, V_BLEF4_CONCAT_LEN, V_BLEF4_LENS, V_BLEF4_NFRAMES, V_BLEF4_LABEL);
    run_framing_vector(V_BLEF5_PAYLOAD, V_BLEF5_PAYLOAD_LEN, V_BLEF5_MAXPDU,
                       V_BLEF5_CONCAT, V_BLEF5_CONCAT_LEN, V_BLEF5_LENS, V_BLEF5_NFRAMES, V_BLEF5_LABEL);

    // --- BLE reassembly (consume side, ble_reasm_feed): the golden PDUs reassemble
    //     back to the payload; the same pure function the OpWrite callback now uses. ---
    run_reasm_vector(V_BLEF0_CONCAT, V_BLEF0_LENS, V_BLEF0_NFRAMES, V_BLEF0_PAYLOAD, V_BLEF0_PAYLOAD_LEN, V_BLEF0_LABEL);
    run_reasm_vector(V_BLEF1_CONCAT, V_BLEF1_LENS, V_BLEF1_NFRAMES, V_BLEF1_PAYLOAD, V_BLEF1_PAYLOAD_LEN, V_BLEF1_LABEL);
    run_reasm_vector(V_BLEF2_CONCAT, V_BLEF2_LENS, V_BLEF2_NFRAMES, V_BLEF2_PAYLOAD, V_BLEF2_PAYLOAD_LEN, V_BLEF2_LABEL);
    run_reasm_vector(V_BLEF3_CONCAT, V_BLEF3_LENS, V_BLEF3_NFRAMES, V_BLEF3_PAYLOAD, V_BLEF3_PAYLOAD_LEN, V_BLEF3_LABEL);
    run_reasm_vector(V_BLEF4_CONCAT, V_BLEF4_LENS, V_BLEF4_NFRAMES, V_BLEF4_PAYLOAD, V_BLEF4_PAYLOAD_LEN, V_BLEF4_LABEL);
    run_reasm_vector(V_BLEF5_CONCAT, V_BLEF5_LENS, V_BLEF5_NFRAMES, V_BLEF5_PAYLOAD, V_BLEF5_PAYLOAD_LEN, V_BLEF5_LABEL);
    {
        // negative: a seq gap (skip frame 1 of the 3-PDU vector) must reset + ERROR.
        uint8_t buf[2048];
        uint32_t tl = 0, gl = 0;
        uint8_t ns = 0;
        bool ip = false;
        ble_reasm_status r0 = ble_reasm_feed(V_BLEF4_CONCAT, V_BLEF4_LENS[0], buf, sizeof(buf),
                                             &tl, &gl, &ns, &ip);
        size_t off2 = (size_t)V_BLEF4_LENS[0] + V_BLEF4_LENS[1];   // skip frame 1
        ble_reasm_status r2 = ble_reasm_feed(V_BLEF4_CONCAT + off2, V_BLEF4_LENS[2], buf, sizeof(buf),
                                             &tl, &gl, &ns, &ip);
        check(r0 == BLE_REASM_NEED_MORE && r2 == BLE_REASM_ERROR && !ip,
              "ble_reasm rejects seq gap (state reset)");
    }

    // --- APDU/NFC transport framing (§3.2, §4): the data bodies firmware
    //     transfer.cpp builds/parses, at the exact offsets reconciled byte-identical
    //     with Android. (parse-at-offsets, like the wire-structure checks above.) ---
    {
        auto rdu16 = [](const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); };
        auto rdu32 = [](const uint8_t* p) {
            return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24));
        };

        const uint8_t* b = V_AF_OC_DATA;          // OP_CHUNKED FETCH response
        check(b[0] == 0x02, "apdu op_chunked marker @0 (0x02)");
        check(b[1] == V_AF_OC_INNER, "apdu op_chunked inner_opcode @1");
        check(rdu32(b + 2) == V_AF_OC_MSGID, "apdu op_chunked msg_id @2");
        check(rdu32(b + 6) == V_AF_OC_TOTAL, "apdu op_chunked total_len @6");
        check(rdu16(b + 10) == V_AF_OC_FCL, "apdu op_chunked first_chunk_len @10");
        check(eq(b + 12, V_AF_OC_CHUNK, V_AF_OC_FCL), "apdu op_chunked first_chunk @12");

        b = V_AF_OS_DATA;                          // OP_SINGLE FETCH response
        check(b[0] == 0x01, "apdu op_single marker @0 (0x01)");
        check(b[1] == V_AF_OS_INNER, "apdu op_single inner_opcode @1");
        check(rdu16(b + 2) == V_AF_OS_OPLEN, "apdu op_single op_len @2");
        check(eq(b + 4, V_AF_OS_BYTES, V_AF_OS_OPLEN), "apdu op_single op_bytes @4");

        b = V_AF_RCCMD_DATA;                       // READ_CHUNK command body
        check(rdu32(b + 0) == V_AF_RCCMD_MSGID, "apdu read_chunk_cmd msg_id @0");
        check(rdu32(b + 4) == V_AF_RCCMD_OFFSET, "apdu read_chunk_cmd offset @4");
        check(rdu16(b + 8) == V_AF_RCCMD_MAXCHUNK, "apdu read_chunk_cmd max_chunk @8");
        check(V_AF_RCCMD_APDU[0] == 0x00 && V_AF_RCCMD_APDU[1] == 0xC3 && V_AF_RCCMD_APDU[4] == 0x0A &&
              eq(V_AF_RCCMD_APDU + 5, V_AF_RCCMD_DATA, 10) && V_AF_RCCMD_APDU[15] == 0x00,
              "apdu read_chunk_cmd APDU wrapper (00 C3 00 00 0A | body | 00)");

        b = V_AF_RCRESP_DATA;                      // READ_CHUNK response body
        check(rdu16(b + 0) == V_AF_RCRESP_CHUNKLEN, "apdu read_chunk_resp chunk_len @0");
        check(b[2] == V_AF_RCRESP_FLAGS && (b[2] & 0x01) != 0, "apdu read_chunk_resp flags @2 (LAST)");
        check(eq(b + 3, V_AF_RCRESP_CHUNK, V_AF_RCRESP_CHUNKLEN), "apdu read_chunk_resp chunk @3");

        b = V_AF_PC_DATA;                          // PUSH_CHUNK command body
        check(rdu32(b + 0) == V_AF_PC_MSGID, "apdu push_chunk msg_id @0");
        check(rdu32(b + 4) == V_AF_PC_OFFSET, "apdu push_chunk offset @4");
        check(rdu32(b + 8) == V_AF_PC_TOTAL, "apdu push_chunk total @8");
        check(b[12] == V_AF_PC_FLAGS, "apdu push_chunk flags @12");
        check(rdu16(b + 13) == V_AF_PC_CHUNKLEN, "apdu push_chunk chunk_len @13");
        check(eq(b + 15, V_AF_PC_CHUNK, V_AF_PC_CHUNKLEN), "apdu push_chunk chunk @15");
        check(V_AF_PC_APDU[0] == 0x00 && V_AF_PC_APDU[1] == 0xC4 &&
              V_AF_PC_APDU[4] == (uint8_t)(15 + V_AF_PC_CHUNKLEN),
              "apdu push_chunk APDU wrapper (00 C4 00 00 Lc)");

        b = V_AF_REF_DATA;                         // REFERENCE prev_result
        check(b[0] == 0xFF && b[1] == 0xFF, "apdu reference marker @0 (FF FF)");
        check(rdu32(b + 2) == V_AF_REF_MSGID, "apdu reference msg_id @2");
    }

    // --- streaming Ed25519 verify (T2 keystone: verify without holding the whole
    //     message in RAM, so a >16 KB filter_package can be streamed to flash) ---
    {
        uint8_t m[16 + 64];
        std::memcpy(m, V_DOMAIN_KEY, 16);
        std::memcpy(m + 16, V_ED_PAYLOAD, V_ED_PAYLOAD_LEN);
        const size_t mlen = 16 + V_ED_PAYLOAD_LEN;

        // (a) feed in awkward 7-byte chunks -> must validate
        ed25519_verify_stream_ctx c;
        ed25519_verify_stream_init(&c, V_ED_SIG, V_ED_PUBKEY);
        for (size_t o = 0; o < mlen; o += 7) {
            size_t n = (mlen - o) < 7 ? (mlen - o) : 7;
            ed25519_verify_stream_update(&c, m + o, n);
        }
        check(ed25519_verify_stream_final(&c), "stream verify (chunked) valid");

        // (b) equals the one-shot result
        ed25519_verify_stream_ctx c2;
        ed25519_verify_stream_init(&c2, V_ED_SIG, V_ED_PUBKEY);
        ed25519_verify_stream_update(&c2, m, mlen);
        check(ed25519_verify_stream_final(&c2) ==
              ed25519_verify(V_ED_PUBKEY, m, mlen, V_ED_SIG),
              "stream verify == one-shot verify");

        // (c) negative: corrupt one message byte -> must reject
        m[20] ^= 0x01;
        ed25519_verify_stream_ctx c3;
        ed25519_verify_stream_init(&c3, V_ED_SIG, V_ED_PUBKEY);
        ed25519_verify_stream_update(&c3, m, mlen);
        check(!ed25519_verify_stream_final(&c3), "stream verify rejects corruption");

        // (d) realistic: stream the issued_key server-sig (DOMAIN_KEY || header[0:87]) in 16-B chunks
        ed25519_verify_stream_ctx c4;
        ed25519_verify_stream_init(&c4, V_IK_BLOB + 87, V_IK_SERVER_PUB);
        ed25519_verify_stream_update(&c4, V_DOMAIN_KEY, 16);
        for (size_t o = 0; o < 87; o += 16) {
            size_t n = (87 - o) < 16 ? (87 - o) : 16;
            ed25519_verify_stream_update(&c4, V_IK_BLOB + o, n);
        }
        check(ed25519_verify_stream_final(&c4), "stream verify issued_key server-sig (chunked)");
    }

    std::printf("\n%d failure(s)\n", g_fail);
    return g_fail == 0 ? 0 : 1;
}
