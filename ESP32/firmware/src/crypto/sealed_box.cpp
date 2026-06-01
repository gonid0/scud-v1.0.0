#include "sealed_box.h"
#include <string.h>
#include "monocypher.h"

// X25519 + IETF ChaCha20-Poly1305 (RFC 8439, 12-B nonce) — всё на Monocypher.
// mbedTLS в Arduino-сборке ESP32 собран без MBEDTLS_CHACHAPOLY_C, поэтому
// AEAD реализован вручную из crypto_chacha20_ietf + crypto_poly1305.
// Monocypher'овский crypto_aead_lock нельзя — у него XChaCha20 (24-B nonce).

static void u64_le(uint8_t out[8], uint64_t v) {
    for (int i = 0; i < 8; ++i) out[i] = (uint8_t)(v >> (8 * i));
}

bool sealed_box_encrypt(const uint8_t server_x25519_pub[32],
                        const uint8_t* plaintext, size_t pt_len,
                        uint8_t* out_blob,
                        const uint8_t random_32[32]) {
    uint8_t ephemeral_priv[32];
    memcpy(ephemeral_priv, random_32, 32);

    uint8_t* ephemeral_pub = out_blob;
    crypto_x25519_public_key(ephemeral_pub, ephemeral_priv);

    uint8_t shared[32];
    crypto_x25519(shared, ephemeral_priv, server_x25519_pub);
    crypto_wipe(ephemeral_priv, 32);

    // KDF (CRYPTO-05 "blender"): derive a uniform AEAD key from the raw X25519
    // secret via BLAKE2b(shared, 32). MUST match backend-decrypt exactly.
    uint8_t key[32];
    crypto_blake2b(key, 32, shared, 32);
    crypto_wipe(shared, 32);

    uint8_t nonce24[24];
    crypto_blake2b_ctx ctx;
    crypto_blake2b_init(&ctx, 24);
    crypto_blake2b_update(&ctx, ephemeral_pub, 32);
    crypto_blake2b_update(&ctx, server_x25519_pub, 32);
    crypto_blake2b_final(&ctx, nonce24);

    uint8_t nonce12[12];
    memcpy(nonce12, nonce24, 12);

    uint8_t* ct  = out_blob + 32;
    uint8_t* tag = out_blob + 32 + pt_len;

    // Poly1305 one-time key = ChaCha20(key, nonce12, counter=0)[:32].
    uint8_t zero64[64] = {0};
    uint8_t keystream0[64];
    crypto_chacha20_ietf(keystream0, zero64, 64, key, nonce12, 0);
    uint8_t poly_key[32];
    memcpy(poly_key, keystream0, 32);
    crypto_wipe(keystream0, 64);

    // Encrypt starting at counter=1.
    crypto_chacha20_ietf(ct, plaintext, pt_len, key, nonce12, 1);
    crypto_wipe(key, 32);

    // MAC input (AAD пустой): ct || pad16(ct) || len(aad)_le64 || len(ct)_le64.
    crypto_poly1305_ctx pctx;
    crypto_poly1305_init(&pctx, poly_key);
    crypto_poly1305_update(&pctx, ct, pt_len);
    size_t pad_ct = (16 - (pt_len % 16)) % 16;
    if (pad_ct) {
        uint8_t zeros[16] = {0};
        crypto_poly1305_update(&pctx, zeros, pad_ct);
    }
    uint8_t lens[16] = {0};
    u64_le(lens + 0, 0);
    u64_le(lens + 8, (uint64_t)pt_len);
    crypto_poly1305_update(&pctx, lens, 16);
    crypto_poly1305_final(&pctx, tag);
    crypto_wipe(poly_key, 32);

    return true;
}
