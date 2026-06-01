#include "bloom.h"
#include "murmur3.h"

bool bloom_contains(const uint8_t* bits, uint32_t m_bits,
                    uint8_t k_hashes, uint32_t hash_seed,
                    const uint8_t key_id[16]) {
    if (!bits || m_bits == 0 || k_hashes == 0) return false;
    for (uint8_t i = 0; i < k_hashes; i++) {
        uint32_t h = murmur3_x86_32(key_id, 16, hash_seed + i) % m_bits;
        if ((bits[h / 8] & (1u << (h % 8))) == 0) return false;
    }
    return true;
}
