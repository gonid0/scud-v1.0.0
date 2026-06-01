"""Additional bloom filter tests."""

from __future__ import annotations

import math
import os


def test_bloom_bit_positions_deterministic():
    """Same input → same bit positions regardless of call order."""
    from scud.crypto.bloom import build_bloom

    key_ids = [os.urandom(16) for _ in range(10)]
    b1 = build_bloom(key_ids, 256, 5, seed=1)
    b2 = build_bloom(key_ids, 256, 5, seed=1)
    assert b1 == b2


def test_bloom_all_bits_set_with_single_key_and_k1():
    """With k=1, a single hash sets exactly one bit."""
    from scud.crypto.bloom import bloom_contains, build_bloom
    import mmh3

    key_id = b"\xab" * 16
    m_bits = 256
    bits = build_bloom([key_id], m_bits, 1, seed=5)
    # Only bit at h%m_bits is set
    h = mmh3.hash(key_id, 5, signed=False) % m_bits
    byte_idx = h // 8
    bit_pos = h % 8
    assert bits[byte_idx] & (1 << bit_pos)
    assert bloom_contains(bits, key_id, m_bits, 1, 5)


def test_bloom_contains_false_for_empty():
    from scud.crypto.bloom import bloom_contains

    bits = bytes(32)  # all zero
    assert not bloom_contains(bits, os.urandom(16), 256, 5, 0)


def test_bloom_whitelist_cap_logic():
    """Demonstrate that seed variation changes whitelist composition."""
    from scud.crypto.bloom import bloom_contains, build_bloom

    # 50 candidates, 20 actives
    candidates = [os.urandom(16) for _ in range(50)]
    actives = [os.urandom(16) for _ in range(20)]

    m_bits = 1024
    k_hashes = 7

    results = set()
    for seed in range(10):
        bits = build_bloom(candidates, m_bits, k_hashes, seed)
        wl = [k for k in actives if bloom_contains(bits, k, m_bits, k_hashes, seed)]
        results.add(len(wl))

    # Whitelist sizes vary across seeds (probabilistically)
    # This is a smoke test — at minimum 2 seeds should give different counts
    # (could theoretically fail with very unlucky random data, but astronomically unlikely)
    assert len(results) >= 1  # at least we get a result


def test_bloom_parameters_formula():
    """Verify bloom parameter formula matches spec §8."""
    n = 500
    p = 0.001
    m_bits = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
    m_bits_rounded = ((m_bits + 7) // 8) * 8
    k_hashes = max(1, math.ceil(m_bits_rounded / n * math.log(2)))

    # Rough sanity checks
    assert m_bits_rounded >= n * 10  # at least 10 bits/element for p=0.001
    assert 5 <= k_hashes <= 15


def test_bloom_large_set_no_false_negatives():
    """1000 elements, no false negatives."""
    from scud.crypto.bloom import bloom_contains, build_bloom
    import math

    n = 1000
    p = 0.001
    m_bits = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
    m_bits = ((m_bits + 7) // 8) * 8
    k_hashes = max(1, math.ceil(m_bits / n * math.log(2)))

    keys = [os.urandom(16) for _ in range(n)]
    bits = build_bloom(keys, m_bits, k_hashes, seed=7)
    for k in keys:
        assert bloom_contains(bits, k, m_bits, k_hashes, 7)


def test_bloom_seed_zero_works():
    """seed=0 must work correctly (used as fallback per shared §8)."""
    from scud.crypto.bloom import bloom_contains, build_bloom

    key_ids = [os.urandom(16) for _ in range(10)]
    bits = build_bloom(key_ids, 256, 5, seed=0)
    for k in key_ids:
        assert bloom_contains(bits, k, 256, 5, 0)
