#pragma once
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// MurmurHash3 x86 32-bit (Austin Appleby, public domain).
// Результат должен совпадать между firmware, backend и Android.
uint32_t murmur3_x86_32(const void* key, int len, uint32_t seed);

#ifdef __cplusplus
}
#endif
