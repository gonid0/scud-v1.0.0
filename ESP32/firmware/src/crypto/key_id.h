#pragma once
#include <stdint.h>

// key_id = BLAKE2s-128(reader_id || phone_pubkey || issued_at_8LE || serial_4LE)
// См. shared §5.1.
void compute_key_id(const uint8_t reader_id[16],
                    const uint8_t phone_pubkey[32],
                    uint64_t issued_at,
                    uint32_t serial,
                    uint8_t out_key_id[16]);
