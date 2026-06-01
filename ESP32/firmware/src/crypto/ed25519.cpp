// Обёртка над Monocypher 4.x, опциональный модуль monocypher-ed25519.
// ВАЖНО: используем именно crypto_ed25519_* (SHA-512, RFC 8032),
// а НЕ crypto_eddsa_* из core Monocypher — тот построен на BLAKE2b
// и несовместим с libsodium/BouncyCastle/PyNaCl.
//
// API (из monocypher-ed25519.h):
//   crypto_ed25519_key_pair(secret_key[64], public_key[32], seed[32])
//   crypto_ed25519_sign(signature[64], secret_key[64], msg, msg_len)
//   crypto_ed25519_check(signature[64], public_key[32], msg, msg_len)
#include "ed25519.h"
#include <string.h>
#include "monocypher.h"
#include "monocypher-ed25519.h"

void ed25519_sign(const uint8_t priv[32], const uint8_t pub[32],
                  const uint8_t* message, size_t len,
                  uint8_t signature[64]) {
    (void)pub;
    uint8_t seed_copy[32];
    uint8_t secret_key[64];
    uint8_t pk[32];
    memcpy(seed_copy, priv, 32);
    crypto_ed25519_key_pair(secret_key, pk, seed_copy);   // seed_copy wiped inside
    crypto_ed25519_sign(signature, secret_key, message, len);
    crypto_wipe(secret_key, 64);
}

bool ed25519_verify(const uint8_t pub[32],
                    const uint8_t* message, size_t len,
                    const uint8_t signature[64]) {
    return crypto_ed25519_check(signature, pub, message, len) == 0;
}

void ed25519_derive_pub(const uint8_t priv[32], uint8_t pub[32]) {
    uint8_t seed_copy[32];
    uint8_t secret_key[64];
    memcpy(seed_copy, priv, 32);
    crypto_ed25519_key_pair(secret_key, pub, seed_copy);
    crypto_wipe(secret_key, 64);
}

// --- streaming verify: replicate crypto_ed25519_check's hash_reduce+equation,
//     but feed the message incrementally so it never has to live whole in RAM. ---
void ed25519_verify_stream_init(ed25519_verify_stream_ctx* ctx,
                                const uint8_t signature[64], const uint8_t pub[32]) {
    static_assert(sizeof(crypto_sha512_ctx) <= sizeof(ctx->sha),
                  "opaque sha buffer too small for crypto_sha512_ctx");
    memcpy(ctx->signature, signature, 64);
    memcpy(ctx->pubkey, pub, 32);
    crypto_sha512_ctx* s = reinterpret_cast<crypto_sha512_ctx*>(ctx->sha);
    crypto_sha512_init(s);
    crypto_sha512_update(s, signature, 32);   // R = signature[0:32]
    crypto_sha512_update(s, pub, 32);         // A = public key
}

void ed25519_verify_stream_update(ed25519_verify_stream_ctx* ctx,
                                  const uint8_t* msg, size_t len) {
    crypto_sha512_update(reinterpret_cast<crypto_sha512_ctx*>(ctx->sha), msg, len);
}

bool ed25519_verify_stream_final(ed25519_verify_stream_ctx* ctx) {
    uint8_t hash64[64], h_ram[32];
    crypto_sha512_final(reinterpret_cast<crypto_sha512_ctx*>(ctx->sha), hash64);
    crypto_eddsa_reduce(h_ram, hash64);       // 64 -> 32 mod L
    return crypto_eddsa_check_equation(ctx->signature, ctx->pubkey, h_ram) == 0;
}
