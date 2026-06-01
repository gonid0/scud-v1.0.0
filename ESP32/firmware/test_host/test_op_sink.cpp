// Host test for the N2/B6 op_sink + two-pass verify-from-flash logic.
//
// Proves the verify-from-flash core WITHOUT hardware: a RAM-backed fake
// flash_slot_sink (it implements the SAME op_sink interface the device's SPIFFS
// flash_slot_sink does) is fed a valid golden filter_package, streamed in
// awkward chunk sizes, then verified via verify_filter_sink_sig — exactly the
// two passes (sig from the tail, body re-read from the start) the firmware runs
// against a real A/B slot. Flipping one body byte must flip the verdict to
// INVALID, and a too-short sink must reject.
//
// What is host-proven here: the op_sink abstraction (ram_sink + the fake flash
// sink), the back-read offsets, and the streaming Ed25519 verify over a sink.
// What stays hardware-only: the real SPIFFS File I/O, the BLE NimBLE host-task
// streaming, and the malloc(100 KB) of the active bloom under NimBLE.
//
// Build/run: see test_host/README.md (the op_sink.cpp source is added to the
// cl / g++ command). Returns 0 if all pass, 1 otherwise.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "domains.h"
#include "ed25519.h"
#include "op_sink.h"

static int g_fail = 0;
static void check(bool ok, const char* name) {
    std::printf("  %s %s\n", ok ? "[PASS]" : "[FAIL]", name);
    if (!ok) g_fail++;
}

static void put_u16le(uint8_t* p, uint16_t v) { p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; }
static void put_u32le(uint8_t* p, uint32_t v) {
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}
static void put_u64le(uint8_t* p, uint64_t v) {
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)((v >> (8 * i)) & 0xFF);
}

// ---------------------------------------------------------------------------
// Fake flash_slot_sink: a plain std::vector<uint8_t>. Same op_sink contract as
// the device's SPIFFS sink — append via write(), back-read via read(off), size
// via len(). This is the RAM-backed "fake flash" the task calls for.
// ---------------------------------------------------------------------------
struct fake_flash_ctx {
    std::vector<uint8_t> data;
    bool ok;
};
static bool ff_write(op_sink* s, const uint8_t* chunk, uint32_t len) {
    fake_flash_ctx* c = (fake_flash_ctx*)s->ctx;
    if (!c->ok) return false;
    c->data.insert(c->data.end(), chunk, chunk + len);
    return true;
}
static uint32_t ff_read(op_sink* s, uint32_t off, uint8_t* buf, uint32_t n) {
    fake_flash_ctx* c = (fake_flash_ctx*)s->ctx;
    if (off >= c->data.size()) return 0;
    uint32_t avail = (uint32_t)c->data.size() - off;
    if (n > avail) n = avail;
    std::memcpy(buf, c->data.data() + off, n);
    return n;
}
static uint32_t ff_len(op_sink* s) { return (uint32_t)((fake_flash_ctx*)s->ctx)->data.size(); }
static void fake_flash_init(op_sink* s, fake_flash_ctx* c) {
    c->data.clear();
    c->ok = true;
    s->write = ff_write;
    s->read  = ff_read;
    s->len   = ff_len;
    s->ctx   = c;
}

// Build a valid filter_package and sign it (server key) over DOMAIN_FLT||body.
// Layout (shared §5.6): format(1) reader_id(16) filter_version(8) generated_at(8)
//   m_bits(4) k(1) hash_seed(4) filter_bytes_len(4) whitelist_count(2)
//   bl_delta_count(2) | bloom(filter_bytes_len) | whitelist(wl*24) |
//   bl_delta(bld*16) | sig(64).
static std::vector<uint8_t> build_filter_package(const uint8_t seed[32],
                                                 const uint8_t pub[32],
                                                 uint32_t filter_bytes_len,
                                                 uint16_t wl_count,
                                                 uint16_t bld_count) {
    std::vector<uint8_t> pkg;
    uint8_t header[56] = {0};
    header[0] = 0x01;
    for (int i = 0; i < 16; i++) header[1 + i] = (uint8_t)(0xA0 + i);   // reader_id
    put_u64le(header + 17, 42);                                          // filter_version
    put_u64le(header + 25, 1700000000ull);                              // generated_at
    put_u32le(header + 33, filter_bytes_len * 8);                       // m_bits
    header[37] = 7;                                                      // k_hashes
    put_u32le(header + 38, 0xDEADBEEF);                                 // hash_seed
    put_u32le(header + 42, filter_bytes_len);                           // filter_bytes_len
    put_u16le(header + 46, wl_count);                                   // whitelist_count
    put_u16le(header + 48, bld_count);                                  // bl_delta_count
    pkg.insert(pkg.end(), header, header + 56);

    for (uint32_t i = 0; i < filter_bytes_len; i++)
        pkg.push_back((uint8_t)((i * 31 + 7) & 0xFF));                  // bloom bytes
    for (uint16_t w = 0; w < wl_count; w++)
        for (int b = 0; b < 24; b++) pkg.push_back((uint8_t)(w + b));   // whitelist entry
    for (uint16_t d = 0; d < bld_count; d++)
        for (int b = 0; b < 16; b++) pkg.push_back((uint8_t)(0xF0 + d + b)); // bl_delta entry

    // Sign DOMAIN_FLT || body, append the 64-B signature.
    std::vector<uint8_t> msg;
    msg.insert(msg.end(), DOMAIN_FLT, DOMAIN_FLT + 16);
    msg.insert(msg.end(), pkg.begin(), pkg.end());
    uint8_t sig[64];
    ed25519_sign(seed, pub, msg.data(), msg.size(), sig);
    pkg.insert(pkg.end(), sig, sig + 64);
    return pkg;
}

// Stream a package into a sink in `chunk` sized pieces (mimics READ_CHUNK /
// BLE PDU boundaries crossing the package arbitrarily).
static void stream(op_sink* s, const std::vector<uint8_t>& pkg, uint32_t chunk) {
    for (uint32_t off = 0; off < pkg.size(); off += chunk) {
        uint32_t n = (uint32_t)pkg.size() - off;
        if (n > chunk) n = chunk;
        s->write(s, pkg.data() + off, n);
    }
}

int main() {
    std::printf("op_sink + two-pass verify-from-flash (N2/B6, RAM-backed fake flash)\n");

    // Server Ed25519 keypair (test-local; the verify uses pub only).
    uint8_t seed[32];
    for (int i = 0; i < 32; i++) seed[i] = (uint8_t)(i * 7 + 1);
    uint8_t pub[32];
    ed25519_derive_pub(seed, pub);

    // A ~40 KB filter (well over the 16 KB TRANSFER_BUFFER_CAP) with whitelist
    // + bl_delta sections, so the verify spans many back-read chunks.
    const uint32_t FBL = 40000;
    std::vector<uint8_t> pkg = build_filter_package(seed, pub, FBL, /*wl*/3, /*bld*/2);

    // --- ram_sink sanity: write + back-read round-trips ---
    {
        std::vector<uint8_t> store(pkg.size());
        op_sink s; ram_sink_ctx c;
        ram_sink_init(&s, &c, store.data(), (uint32_t)store.size());
        stream(&s, pkg, 250);
        bool len_ok = (s.len(&s) == pkg.size());
        uint8_t tail[64];
        bool read_ok = (s.read(&s, (uint32_t)pkg.size() - 64, tail, 64) == 64) &&
                       (std::memcmp(tail, pkg.data() + pkg.size() - 64, 64) == 0);
        check(len_ok && read_ok, "ram_sink write/len/back-read round-trip");
    }

    // --- fake flash sink: valid package streams + verifies VALID ---
    {
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        stream(&s, pkg, 250);   // awkward 250-B chunks
        check(s.len(&s) == pkg.size(), "fake-flash streamed full package length");
        check(verify_filter_sink_sig(&s, pub, DOMAIN_FLT),
              "fake-flash two-pass verify VALID (golden package)");
    }

    // --- fake flash sink: streamed in 7-B chunks still VALID (boundary-agnostic) ---
    {
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        stream(&s, pkg, 7);
        check(verify_filter_sink_sig(&s, pub, DOMAIN_FLT),
              "fake-flash verify VALID with 7-B streaming chunks");
    }

    // --- negative: flip one body byte -> verify INVALID (no swap on device) ---
    {
        std::vector<uint8_t> bad = pkg;
        bad[100] ^= 0x01;       // a bloom byte inside the signed body
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        stream(&s, bad, 250);
        check(!verify_filter_sink_sig(&s, pub, DOMAIN_FLT),
              "fake-flash verify INVALID after body byte flip");
    }

    // --- negative: flip one signature byte -> INVALID ---
    {
        std::vector<uint8_t> bad = pkg;
        bad[bad.size() - 1] ^= 0x80;
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        stream(&s, bad, 250);
        check(!verify_filter_sink_sig(&s, pub, DOMAIN_FLT),
              "fake-flash verify INVALID after signature byte flip");
    }

    // --- negative: wrong domain tag -> INVALID (domain separation enforced) ---
    {
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        stream(&s, pkg, 250);
        check(!verify_filter_sink_sig(&s, pub, DOMAIN_RCP),
              "fake-flash verify INVALID with wrong domain tag");
    }

    // --- negative: sink shorter than header+sig -> reject ---
    {
        op_sink s; fake_flash_ctx c; fake_flash_init(&s, &c);
        uint8_t tiny[56 + 64 - 1] = {0};
        s.write(&s, tiny, sizeof(tiny));
        check(!verify_filter_sink_sig(&s, pub, DOMAIN_FLT),
              "verify rejects undersized sink (< header+sig)");
    }

    std::printf("\n%d failure(s)\n", g_fail);
    return g_fail == 0 ? 0 : 1;
}
