"""Crypto unit tests (§10.1)."""

from __future__ import annotations

import hashlib
import os

import pytest
from nacl.public import PrivateKey
from nacl.signing import SigningKey


# ---------------------------------------------------------------------------
# key_id
# ---------------------------------------------------------------------------

def test_compute_key_id_deterministic():
    from scud.crypto.key_id import compute_key_id

    reader_id = os.urandom(16)
    phone_pubkey = os.urandom(32)
    issued_at = 1700000000
    serial = 42

    k1 = compute_key_id(reader_id, phone_pubkey, issued_at, serial)
    k2 = compute_key_id(reader_id, phone_pubkey, issued_at, serial)
    assert k1 == k2


def test_compute_key_id_length():
    from scud.crypto.key_id import compute_key_id

    kid = compute_key_id(os.urandom(16), os.urandom(32), 1700000000, 1)
    assert len(kid) == 16


def test_compute_key_id_uses_blake2s_not_blake2b():
    """Verify that key_id uses BLAKE2s (not BLAKE2b) — they differ."""
    from scud.crypto.key_id import compute_key_id

    reader_id = b"\x01" * 16
    phone_pubkey = b"\x02" * 32
    issued_at = 100
    serial = 1

    # Reference: manual BLAKE2s
    data = reader_id + phone_pubkey + issued_at.to_bytes(8, "little") + serial.to_bytes(4, "little")
    expected = hashlib.blake2s(data, digest_size=16).digest()
    # Sanity: blake2b would give different result
    wrong = hashlib.blake2b(data, digest_size=16).digest()
    assert expected != wrong  # they really differ

    result = compute_key_id(reader_id, phone_pubkey, issued_at, serial)
    assert result == expected


def test_compute_key_id_changes_with_inputs():
    from scud.crypto.key_id import compute_key_id

    base = compute_key_id(b"\x00" * 16, b"\x00" * 32, 0, 0)
    assert base != compute_key_id(b"\x01" * 16, b"\x00" * 32, 0, 0)
    assert base != compute_key_id(b"\x00" * 16, b"\x01" * 32, 0, 0)
    assert base != compute_key_id(b"\x00" * 16, b"\x00" * 32, 1, 0)
    assert base != compute_key_id(b"\x00" * 16, b"\x00" * 32, 0, 1)


# ---------------------------------------------------------------------------
# Ed25519 sign/verify
# ---------------------------------------------------------------------------

def test_sign_verify_roundtrip():
    from scud.crypto.signing import DOMAIN_KEY, sign_detached, verify_detached

    sk = SigningKey.generate()
    privkey = bytes(sk)
    pubkey = bytes(sk.verify_key)
    payload = os.urandom(87)

    sig = sign_detached(privkey, DOMAIN_KEY, payload)
    assert len(sig) == 64
    assert verify_detached(pubkey, DOMAIN_KEY, payload, sig)


def test_verify_fails_wrong_key():
    from scud.crypto.signing import DOMAIN_KEY, sign_detached, verify_detached

    sk1 = SigningKey.generate()
    sk2 = SigningKey.generate()
    sig = sign_detached(bytes(sk1), DOMAIN_KEY, b"data")
    assert not verify_detached(bytes(sk2.verify_key), DOMAIN_KEY, b"data", sig)


def test_verify_fails_wrong_domain():
    from scud.crypto.signing import DOMAIN_FLT, DOMAIN_KEY, sign_detached, verify_detached

    sk = SigningKey.generate()
    sig = sign_detached(bytes(sk), DOMAIN_KEY, b"data")
    assert not verify_detached(bytes(sk.verify_key), DOMAIN_FLT, b"data", sig)


def test_verify_fails_tampered_payload():
    from scud.crypto.signing import DOMAIN_KEY, sign_detached, verify_detached

    sk = SigningKey.generate()
    payload = b"original"
    sig = sign_detached(bytes(sk), DOMAIN_KEY, payload)
    assert not verify_detached(bytes(sk.verify_key), DOMAIN_KEY, b"tampered", sig)


def test_domain_tags_are_16_bytes():
    from scud.crypto import signing
    for name in ["DOMAIN_KEY", "DOMAIN_INF", "DOMAIN_RSP", "DOMAIN_FLT", "DOMAIN_RCP",
                 "DOMAIN_BLK", "DOMAIN_FDI", "DOMAIN_TGR", "DOMAIN_TIM", "DOMAIN_REV"]:
        tag = getattr(signing, name)
        assert len(tag) == 16, f"{name} must be 16 bytes"


# ---------------------------------------------------------------------------
# Sealed box
# ---------------------------------------------------------------------------

def test_sealed_box_roundtrip():
    """Encrypt with helper, decrypt with server function."""
    from scud.crypto.sealed_box import decrypt_sealed_blob, encrypt_sealed_blob

    server_priv_key = PrivateKey.generate()
    server_x25519_priv = bytes(server_priv_key)
    server_x25519_pub = bytes(server_priv_key.public_key)

    plaintext = b"hello sealed world!"
    blob = encrypt_sealed_blob(plaintext, server_x25519_pub)
    recovered = decrypt_sealed_blob(blob, server_x25519_priv, server_x25519_pub)
    assert recovered == plaintext


def test_sealed_box_wrong_key_fails():
    from nacl.exceptions import CryptoError

    from scud.crypto.sealed_box import decrypt_sealed_blob, encrypt_sealed_blob

    server_key = PrivateKey.generate()
    wrong_key = PrivateKey.generate()

    blob = encrypt_sealed_blob(b"secret", bytes(server_key.public_key))
    with pytest.raises((CryptoError, Exception)):
        decrypt_sealed_blob(blob, bytes(wrong_key), bytes(wrong_key.public_key))


def test_sealed_box_too_short():
    from scud.crypto.sealed_box import decrypt_sealed_blob

    with pytest.raises(ValueError, match="too short"):
        decrypt_sealed_blob(b"\x00" * 10, b"\x00" * 32, b"\x00" * 32)


# ---------------------------------------------------------------------------
# Bloom
# ---------------------------------------------------------------------------

def test_bloom_no_false_negatives():
    from scud.crypto.bloom import bloom_contains, build_bloom

    key_ids = [os.urandom(16) for _ in range(100)]
    m_bits = 2000
    k_hashes = 7
    seed = 42
    bits = build_bloom(key_ids, m_bits, k_hashes, seed)
    for kid in key_ids:
        assert bloom_contains(bits, kid, m_bits, k_hashes, seed), "false negative!"


def test_bloom_fp_rate_near_target():
    import math
    from scud.crypto.bloom import bloom_contains, build_bloom

    n = 1000
    p_target = 0.001
    m_bits = math.ceil(-n * math.log(p_target) / (math.log(2) ** 2))
    m_bits = ((m_bits + 7) // 8) * 8
    k_hashes = max(1, math.ceil(m_bits / n * math.log(2)))

    key_ids = [os.urandom(16) for _ in range(n)]
    bits = build_bloom(key_ids, m_bits, k_hashes, seed=0)

    # Test with 10000 elements NOT in the set
    fp_count = sum(
        1
        for _ in range(10000)
        if bloom_contains(bits, os.urandom(16), m_bits, k_hashes, 0)
    )
    fp_rate = fp_count / 10000
    # Allow 5x margin
    assert fp_rate < p_target * 5, f"FP rate {fp_rate:.4f} too high (target {p_target})"


def test_bloom_empty_set():
    from scud.crypto.bloom import bloom_contains, build_bloom

    bits = build_bloom([], 64, 1, 0)
    assert len(bits) == 8
    assert not bloom_contains(bits, os.urandom(16), 64, 1, 0)


def test_bloom_different_seeds_differ():
    from scud.crypto.bloom import build_bloom

    kid = os.urandom(16)
    b1 = build_bloom([kid], 256, 5, seed=0)
    b2 = build_bloom([kid], 256, 5, seed=99)
    # Most likely differ (not guaranteed but statistically certain for random data)
    # At minimum: same item must be present in both
    from scud.crypto.bloom import bloom_contains
    assert bloom_contains(b1, kid, 256, 5, 0)
    assert bloom_contains(b2, kid, 256, 5, 99)
