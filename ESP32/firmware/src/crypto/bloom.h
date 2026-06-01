#pragma once
#include <stdint.h>
#include <stddef.h>

bool bloom_contains(const uint8_t* bits, uint32_t m_bits,
                    uint8_t k_hashes, uint32_t hash_seed,
                    const uint8_t key_id[16]);
