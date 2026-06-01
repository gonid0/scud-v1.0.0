# Bloom filter using MurmurHash3 x86_32 via mmh3.
# Shared §2.5: bit_position = MurmurHash3_x86_32(key_id, seed=hash_seed+i) % m_bits

import mmh3


def build_bloom(
    key_ids: list[bytes],
    m_bits: int,
    k_hashes: int,
    seed: int,
) -> bytes:
    """Build bloom filter bytes from key_ids."""
    assert m_bits % 8 == 0, "m_bits must be divisible by 8"
    bits = bytearray(m_bits // 8)
    for key_id in key_ids:
        for i in range(k_hashes):
            h = mmh3.hash(key_id, seed + i, signed=False) % m_bits
            bits[h // 8] |= 1 << (h % 8)
    return bytes(bits)


def bloom_contains(
    bits: bytes,
    key_id: bytes,
    m_bits: int,
    k_hashes: int,
    seed: int,
) -> bool:
    """Return True if key_id is (possibly) in the bloom filter."""
    for i in range(k_hashes):
        h = mmh3.hash(key_id, seed + i, signed=False) % m_bits
        if not (bits[h // 8] & (1 << (h % 8))):
            return False
    return True
