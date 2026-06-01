#pragma once
#include <stdint.h>
#include <stddef.h>

void ed25519_sign(const uint8_t priv[32], const uint8_t pub[32],
                  const uint8_t* message, size_t len,
                  uint8_t signature[64]);

bool ed25519_verify(const uint8_t pub[32],
                    const uint8_t* message, size_t len,
                    const uint8_t signature[64]);

// Derive public from private using Ed25519 key schedule.
void ed25519_derive_pub(const uint8_t priv[32], uint8_t pub[32]);

// ---------------------------------------------------------------------------
// Streaming Ed25519 verify (RFC 8032) — for messages too large to hold whole
// in RAM (e.g. a filter_package up to ~127 KB streamed straight to flash).
// Byte-identical result to ed25519_verify(); it just feeds the message in
// chunks. Mirrors Monocypher crypto_ed25519_check: h = SHA512(R || A || msg),
// reduce, then crypto_eddsa_check_equation. ctx is opaque (no Monocypher leak).
//
//   ed25519_verify_stream_ctx c;
//   ed25519_verify_stream_init(&c, signature, pubkey);
//   ed25519_verify_stream_update(&c, chunk0, n0);  // ... per chunk
//   bool ok = ed25519_verify_stream_final(&c);
// ---------------------------------------------------------------------------
typedef struct {
    uint64_t sha[32];          // opaque crypto_sha512_ctx (8-byte aligned, >= sizeof)
    uint8_t  signature[64];
    uint8_t  pubkey[32];
} ed25519_verify_stream_ctx;

void ed25519_verify_stream_init(ed25519_verify_stream_ctx* ctx,
                                const uint8_t signature[64], const uint8_t pub[32]);
void ed25519_verify_stream_update(ed25519_verify_stream_ctx* ctx,
                                  const uint8_t* msg, size_t len);
bool ed25519_verify_stream_final(ed25519_verify_stream_ctx* ctx);
