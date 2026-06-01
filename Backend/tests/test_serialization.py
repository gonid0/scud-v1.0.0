"""Serialization unit tests."""

from __future__ import annotations

import os

from nacl.signing import SigningKey


def _make_privkey() -> bytes:
    return bytes(SigningKey.generate())


def test_pack_issued_key_returns_151_bytes():
    from scud.crypto.serialization import pack_issued_key

    result = pack_issued_key(
        reader_id=os.urandom(16),
        phone_pubkey=os.urandom(32),
        issued_at=1700000000,
        expires_at=1700003600,
        permit_id=os.urandom(16),
        serial=1,
        payload=b"\x00\x00",
        server_privkey=_make_privkey(),
    )
    assert len(result) == 151, f"expected 151 B, got {len(result)}"


def test_pack_issued_key_header_is_87_bytes():
    from scud.crypto.serialization import pack_issued_key_header

    header = pack_issued_key_header(
        reader_id=os.urandom(16),
        phone_pubkey=os.urandom(32),
        issued_at=1700000000,
        expires_at=1700003600,
        permit_id=os.urandom(16),
        serial=42,
        payload=b"\x00\x00",
    )
    assert len(header) == 87


def test_pack_issued_key_format_version():
    from scud.crypto.serialization import pack_issued_key_header

    header = pack_issued_key_header(
        reader_id=b"\x01" * 16,
        phone_pubkey=b"\x02" * 32,
        issued_at=0,
        expires_at=1,
        permit_id=b"\x03" * 16,
        serial=0,
        payload=b"\x00\x00",
    )
    assert header[0] == 0x01, "format_version must be 0x01"


def test_pack_issued_key_reader_id_at_offset_1():
    from scud.crypto.serialization import pack_issued_key_header

    reader_id = bytes(range(16))
    header = pack_issued_key_header(
        reader_id=reader_id,
        phone_pubkey=b"\x00" * 32,
        issued_at=0,
        expires_at=1,
        permit_id=b"\x00" * 16,
        serial=0,
        payload=b"\x00\x00",
    )
    assert header[1:17] == reader_id


def test_pack_issued_key_signature_verifiable():
    from scud.crypto.signing import DOMAIN_KEY, verify_detached
    from scud.crypto.serialization import pack_issued_key

    sk = SigningKey.generate()
    privkey = bytes(sk)
    pubkey = bytes(sk.verify_key)

    full = pack_issued_key(
        reader_id=os.urandom(16),
        phone_pubkey=os.urandom(32),
        issued_at=1700000000,
        expires_at=1700003600,
        permit_id=os.urandom(16),
        serial=1,
        payload=b"\x00\x00",
        server_privkey=privkey,
    )
    header = full[:87]
    sig = full[87:]
    assert verify_detached(pubkey, DOMAIN_KEY, header, sig)


def test_pack_time_grant_returns_148_bytes():
    from scud.crypto.serialization import pack_time_authority_grant, KIND_SOFT

    result = pack_time_authority_grant(
        reader_id=os.urandom(16),
        authority_pubkey=os.urandom(32),
        authority_id=os.urandom(16),
        issued_at=1700000000,
        expires_at=1700086400,
        kind=KIND_SOFT,
        server_privkey=_make_privkey(),
    )
    assert len(result) == 148, f"expected 148 B, got {len(result)}"


def test_pack_time_grant_header_is_84_bytes():
    from scud.crypto.serialization import pack_time_authority_grant_header, KIND_SOFT

    header = pack_time_authority_grant_header(
        reader_id=os.urandom(16),
        authority_pubkey=os.urandom(32),
        authority_id=os.urandom(16),
        issued_at=1700000000,
        expires_at=1700086400,
        kind=KIND_SOFT,
    )
    assert len(header) == 84


def test_pack_time_grant_signature_verifiable():
    from scud.crypto.signing import DOMAIN_TGR, verify_detached
    from scud.crypto.serialization import pack_time_authority_grant, KIND_HARD

    sk = SigningKey.generate()
    full = pack_time_authority_grant(
        reader_id=os.urandom(16),
        authority_pubkey=os.urandom(32),
        authority_id=os.urandom(16),
        issued_at=1700000000,
        expires_at=1700086400,
        kind=KIND_HARD,
        server_privkey=bytes(sk),
    )
    header = full[:84]
    sig = full[84:]
    assert verify_detached(bytes(sk.verify_key), DOMAIN_TGR, header, sig)


def test_pack_filter_package_signature():
    from scud.crypto.serialization import pack_filter_package
    from scud.crypto.signing import DOMAIN_FLT, verify_detached

    sk = SigningKey.generate()
    reader_id = os.urandom(16)
    bloom = bytes(8)  # 64-bit empty filter

    full = pack_filter_package(
        reader_id=reader_id,
        filter_version=1,
        generated_at=1700000000,
        m_bits=64,
        k_hashes=1,
        hash_seed=0,
        bloom_bytes=bloom,
        whitelist=[],
        blacklist_delta=[],
        server_privkey=bytes(sk),
    )

    # Total = 56 (header) + 8 (bloom) + 0 + 0 + 64 (sig) = 128
    assert len(full) == 128

    # Signature: over domain_FLT || header(56) || body_without_sig
    header = full[:56]
    body_no_sig = full[56:-64]
    sig = full[-64:]
    assert verify_detached(bytes(sk.verify_key), DOMAIN_FLT, header + body_no_sig, sig)


def test_parse_delivery_receipt():
    from scud.crypto.serialization import parse_delivery_receipt
    import struct

    reader_id = os.urandom(16)
    afv = 42
    applied_at = 1700000000
    courier_id = os.urandom(16)
    raw = (
        reader_id
        + afv.to_bytes(8, "little")
        + applied_at.to_bytes(8, "little")
        + courier_id
        + os.urandom(64)  # fake sig
    )
    r = parse_delivery_receipt(raw)
    assert r.reader_id == reader_id
    assert r.applied_filter_version == afv
    assert r.applied_at == applied_at
    assert r.courier_id == courier_id


def test_parse_blk_plaintext_zero_entries():
    from scud.crypto.serialization import parse_blk_plaintext

    reader_id = os.urandom(16)
    reader_time = 1700000000
    last_filter = 5
    raw = (
        reader_id
        + reader_time.to_bytes(8, "little")
        + last_filter.to_bytes(8, "little")
        + (0).to_bytes(2, "little")
    )
    pt = parse_blk_plaintext(raw)
    assert pt.reader_id == reader_id
    assert pt.reader_time == reader_time
    assert pt.last_applied_filter_version == last_filter
    assert pt.entries == []
