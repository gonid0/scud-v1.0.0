#include "key_id.h"
#include "blake2s.h"
#include "../utils/little_endian.h"

void compute_key_id(const uint8_t reader_id[16],
                    const uint8_t phone_pubkey[32],
                    uint64_t issued_at,
                    uint32_t serial,
                    uint8_t out_key_id[16]) {
    blake2s_state S;
    blake2s_init(&S, 16);
    blake2s_update(&S, reader_id, 16);
    blake2s_update(&S, phone_pubkey, 32);
    uint8_t buf[12];
    write_u64le(buf,     issued_at);
    write_u32le(buf + 8, serial);
    blake2s_update(&S, buf, 12);
    blake2s_final(&S, out_key_id, 16);
}
