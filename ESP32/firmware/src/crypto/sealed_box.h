#pragma once
#include <stdint.h>
#include <stddef.h>

// Sealed box encrypt (см. shared §2.4): X25519 + ChaCha20-Poly1305(12B-nonce).
// out_blob layout: ephemeral_x25519_pub(32) || ciphertext(pt_len) || tag(16)
// Итого out_blob занимает 32 + pt_len + 16 байт.
// random_32 — случайный seed для ephemeral privkey (генерируется вызывающим через esp_random).
bool sealed_box_encrypt(const uint8_t server_x25519_pub[32],
                        const uint8_t* plaintext, size_t pt_len,
                        uint8_t* out_blob,
                        const uint8_t random_32[32]);
