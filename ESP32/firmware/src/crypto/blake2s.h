#pragma once
// BLAKE2s reference impl (RFC 7693). Используется для key_id (16-byte output).
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t h[8];
    uint32_t t[2];
    uint32_t f[2];
    uint8_t  buf[64];
    size_t   buflen;
    size_t   outlen;
    uint8_t  last_node;
} blake2s_state;

int blake2s_init(blake2s_state* S, size_t outlen);
int blake2s_update(blake2s_state* S, const void* in, size_t inlen);
int blake2s_final(blake2s_state* S, void* out, size_t outlen);

// Convenience one-shot API.
int blake2s(void* out, size_t outlen,
            const void* in, size_t inlen,
            const void* key, size_t keylen);

#ifdef __cplusplus
}
#endif
