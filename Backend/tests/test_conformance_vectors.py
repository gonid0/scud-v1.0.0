"""Conformance: the Backend reproduces the committed golden protocol vectors.

The vectors in docs/test_vectors/protocol_v1.json are the language-neutral
byte-exact contract. This test re-derives every value from the backend crypto
and asserts it equals the committed vector — so a backend change that alters
the wire bytes fails here, and the same corpus is the reference the Android /
firmware conformance tests check against. See docs/00_shared_protocol.
"""
from __future__ import annotations

import hashlib
import json
import os

import mmh3
from nacl.signing import SigningKey

from scud.crypto import signing as S
from scud.crypto.bloom import build_bloom
from scud.crypto.key_id import compute_key_id
from scud.crypto.serialization import (
    pack_issued_key,
    pack_time_authority_grant,
    parse_delivery_receipt,
    parse_fdi_blob,
    parse_passage_receipt,
)

VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "test_vectors", "protocol_v1.json"
)

with open(VECTORS_PATH, encoding="utf-8") as _f:
    VECTORS = json.load(_f)


def _b(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)


def test_signing_domains_match():
    backend = {}
    for name in dir(S):
        if name.startswith("DOMAIN_"):
            tag = getattr(S, name)
            if isinstance(tag, (bytes, bytearray)) and len(tag) == 16:
                label = bytes(tag).rstrip(b"\x00").decode("ascii")
                backend[label] = bytes(tag).hex()
    assert backend == VECTORS["domains"]
    # every tag is exactly 16 bytes
    for hexed in VECTORS["domains"].values():
        assert len(_b(hexed)) == 16


def test_blake2s_128():
    for v in VECTORS["blake2s_128"]:
        got = hashlib.blake2s(_b(v["input_hex"]), digest_size=16).hexdigest()
        assert got == v["expected_hex"]


def test_key_id():
    for v in VECTORS["key_id"]:
        got = compute_key_id(
            _b(v["reader_id_hex"]), _b(v["phone_pubkey_hex"]),
            v["issued_at"], v["serial"],
        ).hex()
        assert got == v["expected_hex"]


def test_ed25519_domain_separated():
    for v in VECTORS["ed25519"]:
        seed = _b(v["seed_hex"])
        pub = bytes(SigningKey(seed).verify_key)
        assert pub.hex() == v["pubkey_hex"]
        tag = _b(VECTORS["domains"][v["domain"]])
        payload = _b(v["payload_hex"])
        sig = S.sign_detached(seed, tag, payload)
        assert sig.hex() == v["signature_hex"]
        assert S.verify_detached(pub, tag, payload, sig)


def test_murmur3_x86_32():
    for v in VECTORS["murmur3_x86_32"]:
        got = mmh3.hash(_b(v["data_hex"]), v["seed"], signed=False)
        assert got == v["expected_u32"]


def test_bloom():
    for v in VECTORS["bloom"]:
        kids = [_b(x) for x in v["key_ids_hex"]]
        got = build_bloom(kids, v["m_bits"], v["k_hashes"], v["seed"]).hex()
        assert got == v["expected_hex"]


def test_issued_key_serialization():
    for v in VECTORS["issued_key"]:
        blob = pack_issued_key(
            _b(v["reader_id_hex"]), _b(v["phone_pubkey_hex"]),
            v["issued_at"], v["expires_at"], _b(v["permit_id_hex"]),
            v["serial"], _b(v["payload_hex"]), _b(v["server_seed_hex"]),
        )
        assert len(blob) == 151
        assert blob.hex() == v["expected_hex"]
        # server signature (DOMAIN_KEY over the 87-byte header) verifies
        header, sig = blob[:87], blob[87:]
        assert S.verify_detached(_b(v["server_pubkey_hex"]), S.DOMAIN_KEY, header, sig)
        # key_id derived from the header fields at their exact offsets
        assert compute_key_id(
            _b(v["reader_id_hex"]), _b(v["phone_pubkey_hex"]),
            v["issued_at"], v["serial"],
        ).hex() == v["key_id_hex"]


def test_time_grant_serialization():
    for v in VECTORS["time_grant"]:
        blob = pack_time_authority_grant(
            _b(v["reader_id_hex"]), _b(v["authority_pubkey_hex"]),
            _b(v["authority_id_hex"]), v["issued_at"], v["expires_at"],
            v["kind"], _b(v["server_seed_hex"]),
        )
        assert len(blob) == 148
        assert blob.hex() == v["expected_hex"]
        # server signature (DOMAIN_TGR over the 84-byte header) verifies
        header, sig = blob[:84], blob[84:]
        assert S.verify_detached(_b(v["server_pubkey_hex"]), S.DOMAIN_TGR, header, sig)


def test_passage_receipt_serialization():
    # reader-built (no backend packer); the backend PARSES it -> round-trip + sig.
    for v in VECTORS["passage_receipt"]:
        blob = _b(v["expected_hex"])
        assert len(blob) == 192
        p = parse_passage_receipt(blob)
        assert p.reader_id == _b(v["reader_id_hex"])
        assert p.receipt_nonce == _b(v["receipt_nonce_hex"])
        assert p.key_id == _b(v["key_id_hex"])
        assert p.passed_at == v["passed_at"]
        assert p.direction == v["direction"]
        assert p.verdict == v["verdict"]
        assert p.session_seq == v["session_seq"]
        assert p.flags == v["flags"]
        assert p.phone_pubkey == _b(v["phone_pubkey_hex"])
        assert p.permit_id == _b(v["permit_id_hex"])
        assert p.issued_key_issued_at == v["issued_key_issued_at"]
        assert p.issued_key_serial == v["issued_key_serial"]
        # reader signature over DOMAIN_PSG || bytes[0:128]
        assert p.signed_payload == blob[:128]
        assert S.verify_detached(_b(v["reader_pubkey_hex"]), S.DOMAIN_PSG, p.signed_payload, p.signature)


def test_info_serialization():
    # reader-built; backend has no pack_info/parse_info, so reproduce the firmware
    # build_info_bytes layout manually and check it equals the golden blob + sig.
    for v in VECTORS["info"]:
        reader_seed = _b(v["reader_seed_hex"])
        header = (
            b"\x01"
            + _b(v["reader_id_hex"])
            + v["reader_time"].to_bytes(8, "little")
            + bytes([v["protocol_version"]])
            + v["max_apdu_size"].to_bytes(2, "little")
            + v["filter_version"].to_bytes(8, "little")
            + v["filter_delivered_at"].to_bytes(8, "little")
            + v["blacklist_count"].to_bytes(2, "little")
            + _b(v["fresh_nonce_hex"])
            + v["session_seq"].to_bytes(4, "little")
        )
        assert len(header) == 82
        blob = header + S.sign_detached(reader_seed, S.DOMAIN_INF, header)
        assert len(blob) == 146
        assert blob.hex() == v["expected_hex"]
        # signed range is exactly bytes[0:82]; reader signature verifies under DOMAIN_INF
        assert S.verify_detached(_b(v["reader_pubkey_hex"]), S.DOMAIN_INF, blob[:82], blob[82:])


def test_delivery_receipt_serialization():
    # reader-built (no backend packer); the backend PARSES it -> round-trip + sig.
    for v in VECTORS["delivery_receipt"]:
        blob = _b(v["expected_hex"])
        assert len(blob) == 112
        d = parse_delivery_receipt(blob)
        assert d.reader_id == _b(v["reader_id_hex"])
        assert d.applied_filter_version == v["applied_filter_version"]
        assert d.applied_at == v["applied_at"]
        assert d.courier_id == _b(v["courier_id_hex"])
        # reader signature over DOMAIN_RCP || bytes[0:48]
        assert S.verify_detached(_b(v["reader_pubkey_hex"]), S.DOMAIN_RCP, blob[:48], blob[48:])


def test_handover_token_serialization():
    # reader-built (no backend packer); NFC→BLE handover (shared §17.1, plan 08 §4.3).
    # Backend is NOT in this wire (reader<->phone), but it is the reference packer.
    # Parse the 167-B layout at exact offsets + verify the reader signature over
    # DOMAIN_BLE || bytes[0:103]. next/expiry fields are pinned constants.
    for v in VECTORS["handover_token"]:
        blob = _b(v["expected_hex"])
        assert len(blob) == 167
        assert blob[0] == 0x99  # format_version / marker
        assert blob[1:17] == _b(v["reader_id_hex"])
        assert blob[17:49] == _b(v["issued_to_phone_pubkey_hex"])
        assert blob[49:55] == _b(v["reader_ble_addr_hex"])
        assert blob[55:87] == _b(v["tap_nonce_hex"])
        assert int.from_bytes(blob[87:95], "little") == v["issued_at"]
        assert int.from_bytes(blob[95:103], "little") == v["expires_at"]
        # reader signature over DOMAIN_BLE || bytes[0:103]; bytes[103:167] is the sig
        assert S.verify_detached(
            _b(v["reader_pubkey_hex"]), S.DOMAIN_BLE, blob[:103], blob[103:167]
        )


def test_fdi_serialization():
    # reader-built; the backend PARSES it. encrypted_courier_blob (33:137) is opaque
    # filler in the vector — a runtime sealed box is non-deterministic, so the vector
    # pins framing + the reader signature over bytes[0:145], treating the blob as opaque.
    for v in VECTORS["fdi"]:
        blob = _b(v["expected_hex"])
        assert len(blob) == 241
        f = parse_fdi_blob(blob)
        assert blob[0] == 0x91
        assert f.reader_id == _b(v["reader_id_hex"])
        assert f.filter_version == v["filter_version"]
        assert f.filter_delivered_at == v["filter_delivered_at"]
        assert f.encrypted_courier_blob == _b(v["encrypted_courier_blob_hex"])
        assert f.reader_time_now == v["reader_time_now"]
        assert f.next_nonce == _b(v["next_nonce_hex"])
        # reader signature over DOMAIN_FDI || bytes[0:145]; next_nonce@209 is NOT signed
        assert S.verify_detached(_b(v["reader_pubkey_hex"]), S.DOMAIN_FDI, blob[:145], blob[145:209])
