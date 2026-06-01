// op_sink — pure impl (ram_sink + verify_filter_sink_sig). No Arduino/SPIFFS:
// the flash_slot_sink lives in state/authoritative.cpp (where the SPIFFS File
// is), but it implements this same op_sink vtable, so the verifier below works
// over either impl. Host-tested in test_host/.
#include "op_sink.h"
#include "../crypto/ed25519.h"
#include <string.h>

// --- ram_sink ---------------------------------------------------------------
static bool ram_write(op_sink* s, const uint8_t* chunk, uint32_t len) {
    ram_sink_ctx* c = (ram_sink_ctx*)s->ctx;
    if (!c->ok) return false;
    if (len > c->cap - c->used) { c->ok = false; return false; }   // overflow
    memcpy(c->buf + c->used, chunk, len);
    c->used += len;
    return true;
}

static uint32_t ram_read(op_sink* s, uint32_t off, uint8_t* buf, uint32_t n) {
    ram_sink_ctx* c = (ram_sink_ctx*)s->ctx;
    if (off >= c->used) return 0;
    uint32_t avail = c->used - off;
    if (n > avail) n = avail;
    memcpy(buf, c->buf + off, n);
    return n;
}

static uint32_t ram_len(op_sink* s) {
    return ((ram_sink_ctx*)s->ctx)->used;
}

void ram_sink_init(op_sink* s, ram_sink_ctx* c, uint8_t* buf, uint32_t cap) {
    c->buf  = buf;
    c->cap  = cap;
    c->used = 0;
    c->ok   = true;
    s->write = ram_write;
    s->read  = ram_read;
    s->len   = ram_len;
    s->ctx   = c;
}

// --- two-pass verify --------------------------------------------------------
bool verify_filter_sink_sig(op_sink* s, const uint8_t server_pub[32],
                            const uint8_t domain_flt[16]) {
    uint32_t total = s->len(s);
    if (total < 56u + 64u) return false;          // header(56) + sig(64) minimum

    // Pass 1: read the trailing 64-byte signature.
    uint8_t sig[64];
    if (s->read(s, total - 64, sig, 64) != 64) return false;

    ed25519_verify_stream_ctx vc;
    ed25519_verify_stream_init(&vc, sig, server_pub);
    ed25519_verify_stream_update(&vc, domain_flt, 16);

    // Pass 2: feed sink[0 : total-64] back in chunks.
    uint32_t remaining = total - 64;
    uint32_t off = 0;
    uint8_t  buf[256];
    while (remaining) {
        uint32_t want = remaining < sizeof(buf) ? remaining : (uint32_t)sizeof(buf);
        uint32_t got = s->read(s, off, buf, want);
        if (got == 0) return false;               // short read / error
        ed25519_verify_stream_update(&vc, buf, got);
        off       += got;
        remaining -= got;
    }
    return ed25519_verify_stream_final(&vc);
}
