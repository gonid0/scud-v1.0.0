# Serialization / deserialization for all binary protocol structures.
# All fields little-endian, packed (shared §1.1).

from __future__ import annotations

import struct
from dataclasses import dataclass

from scud.crypto.signing import (
    DOMAIN_FLT,
    DOMAIN_KEY,
    DOMAIN_TGR,
    sign_detached,
    verify_detached,
)


# ---------------------------------------------------------------------------
# issued_key  (151 B)  — shared §5.1
# ---------------------------------------------------------------------------
# Offset  Size  Field
# 0       1     format_version  (0x01)
# 1       16    reader_id
# 17      32    phone_pubkey
# 49      8     issued_at       (uint64 LE)
# 57      8     expires_at      (uint64 LE)
# 65      16    permit_id
# 81      4     serial          (uint32 LE)
# 85      2     payload
# 87      64    server_signature
# Total: 151 B


def pack_issued_key_header(
    reader_id: bytes,
    phone_pubkey: bytes,
    issued_at: int,
    expires_at: int,
    permit_id: bytes,
    serial: int,
    payload: bytes,
) -> bytes:
    """Pack 87-byte issued_key header (without signature)."""
    assert len(reader_id) == 16
    assert len(phone_pubkey) == 32
    assert len(permit_id) == 16
    assert len(payload) == 2
    return (
        b"\x01"
        + reader_id
        + phone_pubkey
        + issued_at.to_bytes(8, "little")
        + expires_at.to_bytes(8, "little")
        + permit_id
        + serial.to_bytes(4, "little")
        + payload
    )  # 87 B


def pack_issued_key(
    reader_id: bytes,
    phone_pubkey: bytes,
    issued_at: int,
    expires_at: int,
    permit_id: bytes,
    serial: int,
    payload: bytes,
    server_privkey: bytes,
) -> bytes:
    """Return complete 151-byte issued_key blob."""
    header = pack_issued_key_header(
        reader_id, phone_pubkey, issued_at, expires_at, permit_id, serial, payload
    )
    sig = sign_detached(server_privkey, DOMAIN_KEY, header)
    return header + sig  # 151 B


# ---------------------------------------------------------------------------
# time_authority_grant  (148 B)  — shared §5.11
# ---------------------------------------------------------------------------
# Offset  Size  Field
# 0       1     format_version  (0x01)
# 1       16    reader_id
# 17      32    authority_pubkey
# 49      16    authority_id
# 65      8     issued_at
# 73      8     expires_at
# 81      1     kind  (0x01=soft, 0x02=hard)
# 82      2     padding (zeroed)
# 84      64    server_signature  (over domain_TGR || bytes[0:84])
# Total: 148 B

KIND_SOFT = 0x01
KIND_HARD = 0x02


def pack_time_authority_grant_header(
    reader_id: bytes,
    authority_pubkey: bytes,
    authority_id: bytes,
    issued_at: int,
    expires_at: int,
    kind: int,
) -> bytes:
    """Pack 84-byte grant header (without signature)."""
    assert len(reader_id) == 16
    assert len(authority_pubkey) == 32
    assert len(authority_id) == 16
    assert kind in (KIND_SOFT, KIND_HARD)
    return (
        b"\x01"
        + reader_id
        + authority_pubkey
        + authority_id
        + issued_at.to_bytes(8, "little")
        + expires_at.to_bytes(8, "little")
        + bytes([kind])
        + b"\x00\x00"  # padding
    )  # 84 B


def pack_time_authority_grant(
    reader_id: bytes,
    authority_pubkey: bytes,
    authority_id: bytes,
    issued_at: int,
    expires_at: int,
    kind: int,
    server_privkey: bytes,
) -> bytes:
    """Return complete 148-byte time_authority_grant blob."""
    header = pack_time_authority_grant_header(
        reader_id, authority_pubkey, authority_id, issued_at, expires_at, kind
    )
    sig = sign_detached(server_privkey, DOMAIN_TGR, header)
    return header + sig  # 148 B


# ---------------------------------------------------------------------------
# filter_package  (variable)  — shared §5.5
# ---------------------------------------------------------------------------
# Header (56 B):
# 0   1   format_version (0x01)
# 1   16  reader_id
# 17  8   filter_version
# 25  8   generated_at
# 33  4   m_bits         (uint32 LE)
# 37  1   k_hashes
# 38  4   hash_seed      (uint32 LE)
# 42  4   filter_bytes_len
# 46  2   whitelist_count
# 48  2   blacklist_delta_count
# 50  6   padding (zeroed)
#
# Body:
#   bloom_bytes              (filter_bytes_len)
#   whitelist[]              (whitelist_count × 24)  — entry: key_id(16) + expires_at(8)
#   blacklist_delta[]        (blacklist_delta_count × 16)  — entry: key_id(16)
#   server_signature (64)


def pack_filter_package_header(
    reader_id: bytes,
    filter_version: int,
    generated_at: int,
    m_bits: int,
    k_hashes: int,
    hash_seed: int,
    filter_bytes_len: int,
    whitelist_count: int,
    blacklist_delta_count: int,
) -> bytes:
    """Pack 56-byte filter_package header."""
    assert len(reader_id) == 16
    return struct.pack(
        "<B16sQQIBIIHH6s",
        0x01,
        reader_id,
        filter_version,
        generated_at,
        m_bits,
        k_hashes,
        hash_seed,
        filter_bytes_len,
        whitelist_count,
        blacklist_delta_count,
        b"\x00" * 6,
    )  # 56 B


def pack_filter_package(
    reader_id: bytes,
    filter_version: int,
    generated_at: int,
    m_bits: int,
    k_hashes: int,
    hash_seed: int,
    bloom_bytes: bytes,
    whitelist: list[tuple[bytes, int]],  # (key_id, expires_at)
    blacklist_delta: list[bytes],         # [key_id, ...]
    server_privkey: bytes,
) -> bytes:
    """Serialize a complete signed filter_package.

    Signature covers: domain_FLT || header(56) || body(without sig).
    whitelist must already be sorted by key_id before calling.
    """
    filter_bytes_len = len(bloom_bytes)
    whitelist_count = len(whitelist)
    blacklist_delta_count = len(blacklist_delta)

    header = pack_filter_package_header(
        reader_id,
        filter_version,
        generated_at,
        m_bits,
        k_hashes,
        hash_seed,
        filter_bytes_len,
        whitelist_count,
        blacklist_delta_count,
    )

    # Build body without signature
    body = bytearray(bloom_bytes)
    for key_id, expires_at in whitelist:
        assert len(key_id) == 16
        body += key_id + expires_at.to_bytes(8, "little")
    for key_id in blacklist_delta:
        assert len(key_id) == 16
        body += key_id

    # Sign: domain_FLT || header || body
    sig = sign_detached(server_privkey, DOMAIN_FLT, header + bytes(body))
    return header + bytes(body) + sig


# ---------------------------------------------------------------------------
# delivery_receipt  (112 B)  — shared §5.7
# ---------------------------------------------------------------------------
# 0   16  reader_id
# 16  8   applied_filter_version
# 24  8   applied_at
# 32  16  courier_id
# 48  64  reader_signature  (over domain_RCP || bytes[0:48])


@dataclass
class DeliveryReceipt:
    reader_id: bytes
    applied_filter_version: int
    applied_at: int
    courier_id: bytes
    signature: bytes


def parse_delivery_receipt(raw: bytes) -> DeliveryReceipt:
    if len(raw) != 112:
        raise ValueError(f"delivery_receipt must be 112 B, got {len(raw)}")
    reader_id = raw[0:16]
    applied_filter_version = int.from_bytes(raw[16:24], "little")
    applied_at = int.from_bytes(raw[24:32], "little")
    courier_id = raw[32:48]
    signature = raw[48:112]
    return DeliveryReceipt(
        reader_id=reader_id,
        applied_filter_version=applied_filter_version,
        applied_at=applied_at,
        courier_id=courier_id,
        signature=signature,
    )


# ---------------------------------------------------------------------------
# FDI result  (241 B)  — shared §5.8
# ---------------------------------------------------------------------------
# 0    1   result_marker (0x91)
# 1    16  reader_id
# 17   8   filter_version
# 25   8   filter_delivered_at
# 33   104 encrypted_courier_blob
# 137  8   reader_time_now
# 145  64  reader_signature (over domain_FDI || bytes[0:145])
# 209  32  next_nonce


@dataclass
class FDIBlob:
    reader_id: bytes
    filter_version: int
    filter_delivered_at: int
    encrypted_courier_blob: bytes
    reader_time_now: int
    reader_signature: bytes
    next_nonce: bytes


def parse_fdi_blob(raw: bytes) -> FDIBlob:
    if len(raw) != 241:
        raise ValueError(f"FDI blob must be 241 B, got {len(raw)}")
    if raw[0] != 0x91:
        raise ValueError(f"wrong result_marker: {raw[0]:#x}")
    reader_id = raw[1:17]
    filter_version = int.from_bytes(raw[17:25], "little")
    filter_delivered_at = int.from_bytes(raw[25:33], "little")
    encrypted_courier_blob = raw[33:137]  # 104 B
    reader_time_now = int.from_bytes(raw[137:145], "little")
    reader_signature = raw[145:209]
    next_nonce = raw[209:241]
    return FDIBlob(
        reader_id=reader_id,
        filter_version=filter_version,
        filter_delivered_at=filter_delivered_at,
        encrypted_courier_blob=encrypted_courier_blob,
        reader_time_now=reader_time_now,
        reader_signature=reader_signature,
        next_nonce=next_nonce,
    )


# ---------------------------------------------------------------------------
# BLK envelope  — shared §5.9
# ---------------------------------------------------------------------------
# 0    1   result_marker (0x94)
# 1    16  reader_id
# 17   8   reader_time
# 25   2   num_entries
# 27   2   blob_len
# 29   blob_len  encrypted_blob
# 29+blob_len   64  reader_signature
# ...  32  next_nonce


@dataclass
class BLKEnvelope:
    reader_id: bytes
    reader_time: int
    num_entries: int
    encrypted_blob: bytes
    reader_signature: bytes
    next_nonce: bytes


def parse_blk_envelope(raw: bytes) -> BLKEnvelope:
    if len(raw) < 29 + 64 + 32:
        raise ValueError(f"BLK envelope too short: {len(raw)} B")
    if raw[0] != 0x94:
        raise ValueError(f"wrong result_marker: {raw[0]:#x}")
    reader_id = raw[1:17]
    reader_time = int.from_bytes(raw[17:25], "little")
    num_entries = int.from_bytes(raw[25:27], "little")
    blob_len = int.from_bytes(raw[27:29], "little")
    expected = 29 + blob_len + 64 + 32
    if len(raw) != expected:
        raise ValueError(f"BLK envelope expected {expected} B, got {len(raw)}")
    encrypted_blob = raw[29 : 29 + blob_len]
    reader_signature = raw[29 + blob_len : 29 + blob_len + 64]
    next_nonce = raw[29 + blob_len + 64 : 29 + blob_len + 64 + 32]
    return BLKEnvelope(
        reader_id=reader_id,
        reader_time=reader_time,
        num_entries=num_entries,
        encrypted_blob=encrypted_blob,
        reader_signature=reader_signature,
        next_nonce=next_nonce,
    )


# ---------------------------------------------------------------------------
# BLK plaintext  — shared §5.9.1
# ---------------------------------------------------------------------------
# Plaintext: 34 + 32×N B
# 0   16  reader_id
# 16  8   reader_time
# 24  8   last_applied_filter_version
# 32  2   num_entries
# 34  32×N entries
#
# Entry (32 B): key_id(16) + revoked_at(8) + expires_at(8)
# NOTE: reason is NOT in the blob (stored locally on reader, not transmitted)


@dataclass
class BLKEntry:
    key_id: bytes
    revoked_at: int
    expires_at: int


@dataclass
class BLKPlaintext:
    reader_id: bytes
    reader_time: int
    last_applied_filter_version: int
    entries: list[BLKEntry]


def parse_blk_plaintext(data: bytes) -> BLKPlaintext:
    if len(data) < 34:
        raise ValueError("BLK plaintext too short")
    reader_id = data[0:16]
    reader_time = int.from_bytes(data[16:24], "little")
    last_applied = int.from_bytes(data[24:32], "little")
    num_entries = int.from_bytes(data[32:34], "little")
    expected = 34 + 32 * num_entries
    if len(data) != expected:
        raise ValueError(f"BLK plaintext expected {expected} B, got {len(data)}")
    entries = []
    for i in range(num_entries):
        off = 34 + i * 32
        key_id = data[off : off + 16]
        revoked_at = int.from_bytes(data[off + 16 : off + 24], "little")
        expires_at = int.from_bytes(data[off + 24 : off + 32], "little")
        entries.append(BLKEntry(key_id=key_id, revoked_at=revoked_at, expires_at=expires_at))
    return BLKPlaintext(
        reader_id=reader_id,
        reader_time=reader_time,
        last_applied_filter_version=last_applied,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# FDI encrypted_courier_blob plaintext  (56 B)  — shared §5.8.1
# ---------------------------------------------------------------------------
# 0   16  reader_id
# 16  8   filter_version
# 24  16  courier_id
# 40  8   received_at
# 48  8   reader_time


@dataclass
class FDICourierPlaintext:
    reader_id: bytes
    filter_version: int
    courier_id: bytes
    received_at: int
    reader_time: int


def parse_fdi_courier_plaintext(data: bytes) -> FDICourierPlaintext:
    if len(data) != 56:
        raise ValueError(f"FDI courier plaintext must be 56 B, got {len(data)}")
    return FDICourierPlaintext(
        reader_id=data[0:16],
        filter_version=int.from_bytes(data[16:24], "little"),
        courier_id=data[24:40],
        received_at=int.from_bytes(data[40:48], "little"),
        reader_time=int.from_bytes(data[48:56], "little"),
    )


# ---------------------------------------------------------------------------
# passage_receipt  (192 B)  — shared §15.3
# ---------------------------------------------------------------------------
# Offset  Size  Field
# 0       1     format_version          (0x01)
# 1       16    reader_id
# 17      16    receipt_nonce           (random — backend dedup key)
# 33      16    key_id
# 49      8     passed_at               (uint64 LE)
# 57      1     direction               (0 unknown / 1 entry / 2 exit)
# 58      1     verdict                 (= 0x00 OK)
# 59      4     session_seq             (uint32 LE)
# 63      1     flags
# 64      32    phone_pubkey
# 96      16    permit_id
# 112     8     issued_key_issued_at
# 120     4     issued_key_serial       (uint32 LE)
# 124     4     padding
# 128     64    reader_signature        (Ed25519 over domain_PSG || bytes[0:128])

PASSAGE_RECEIPT_LEN = 192


@dataclass
class PassageReceipt:
    reader_id: bytes
    receipt_nonce: bytes
    key_id: bytes
    passed_at: int
    direction: int
    verdict: int
    session_seq: int
    flags: int
    phone_pubkey: bytes
    permit_id: bytes
    issued_key_issued_at: int
    issued_key_serial: int
    signature: bytes
    raw: bytes  # the entire 192 B blob — for direct re-storage

    @property
    def signed_payload(self) -> bytes:
        """The 128 B prefix that the reader signs (over domain_PSG || prefix)."""
        return self.raw[0:128]


def parse_passage_receipt(raw: bytes) -> PassageReceipt:
    if len(raw) != PASSAGE_RECEIPT_LEN:
        raise ValueError(
            f"passage_receipt must be {PASSAGE_RECEIPT_LEN} B, got {len(raw)}"
        )
    if raw[0] != 0x01:
        raise ValueError(f"unsupported passage_receipt format_version: {raw[0]:#x}")
    return PassageReceipt(
        reader_id=raw[1:17],
        receipt_nonce=raw[17:33],
        key_id=raw[33:49],
        passed_at=int.from_bytes(raw[49:57], "little"),
        direction=raw[57],
        verdict=raw[58],
        session_seq=int.from_bytes(raw[59:63], "little"),
        flags=raw[63],
        phone_pubkey=raw[64:96],
        permit_id=raw[96:112],
        issued_key_issued_at=int.from_bytes(raw[112:120], "little"),
        issued_key_serial=int.from_bytes(raw[120:124], "little"),
        signature=raw[128:192],
        raw=bytes(raw),
    )
