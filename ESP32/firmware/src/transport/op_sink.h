// op_sink — unified receive/back-read abstraction for assembled operations
// (N2/B6). PURE module: no Arduino / SPIFFS / NimBLE here, so the host test
// links it directly and proves the two-pass verify-from-flash logic on a
// RAM-backed fake sink (test_host/), exactly as the device proves it on a real
// SPIFFS A/B slot.
//
// Why an abstraction. The transport layer (NFC transfer.cpp + BLE
// ble_channel.cpp) used to assemble every chunked op into a single RAM buffer
// capped at TRANSFER_BUFFER_CAP (16 KB). A per-reader bloom filter_package can
// reach ~100 KB, so big FILTER_UPDATE packets did not fit. Routing those to a
// flash_slot_sink streams the chunks straight into the inactive A/B slot (no
// 16 KB cap, no second in-RAM copy) and the signature is then verified by
// reading the slot back. Small/hot ops (ACCESS …) keep the fast RAM path.
//
// The sink exposes append (write), random back-read (read) and current length
// (len). The verifier below only needs read()+len(), so it is impl-agnostic.
#pragma once
#include <stdint.h>
#include <stddef.h>

// Opaque sink: a tiny vtable + ctx. Both the RAM and the flash implementations
// fill these in. All three ops return success/short-count semantics described
// per-field; a sink in a failed state returns false from write / 0 from read.
typedef struct op_sink {
    // Append `len` bytes; returns true iff all bytes were stored.
    bool (*write)(struct op_sink* s, const uint8_t* chunk, uint32_t len);
    // Back-read up to `n` bytes at absolute `off`; returns bytes actually read
    // (0 on out-of-range / error). Must be callable after writing finished.
    uint32_t (*read)(struct op_sink* s, uint32_t off, uint8_t* buf, uint32_t n);
    // Total bytes appended so far.
    uint32_t (*len)(struct op_sink* s);
    void* ctx;          // impl-private state (RAM buffer view, or File handle)
} op_sink;

// --- ram_sink: wraps a caller-owned buffer (the existing ≤16 KB behaviour). ---
// Pure, host-safe. Used for small ops and as the host-test reference sink.
typedef struct {
    uint8_t* buf;       // caller storage
    uint32_t cap;       // capacity of buf
    uint32_t used;      // bytes written so far
    bool     ok;        // cleared on an overflowing write
} ram_sink_ctx;

void ram_sink_init(op_sink* s, ram_sink_ctx* c, uint8_t* buf, uint32_t cap);

// --- two-pass verify-from-sink (the host-proven core of N2/B6) ---
// Verifies the trailing Ed25519 signature over a filter_package stored in a
// sink, WITHOUT holding the whole package in RAM: signature is the last 64 B
// of the sink, the signed message is DOMAIN_FLT(16) || sink[0 : len-64], read
// back in chunks. Byte-identical to op_filter_update's one-shot verify; this
// is the same scheme verify_filter_file_sig used, lifted behind op_sink so the
// flash path and the host fake share one implementation.
//
//   server_pub : 32 B server Ed25519 public key
//   domain_flt : 16 B DOMAIN_FLT tag (passed in to keep this module crypto-only)
// Returns true iff the signature is valid. A sink shorter than header+sig
// (56+64) is rejected.
bool verify_filter_sink_sig(op_sink* s, const uint8_t server_pub[32],
                            const uint8_t domain_flt[16]);
