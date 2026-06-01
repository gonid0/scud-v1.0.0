#!/usr/bin/env python3
"""Generate golden conformance vectors for the SCUD byte-exact protocol.

Single source of truth: the Backend reference implementation. Every
implementation (Python backend, Kotlin Android, C++ firmware) MUST reproduce
these vectors exactly — that is the whole point of the byte-exact contract
(docs/00_shared_protocol). A single bit of divergence breaks Ed25519 verify.

Run:  python docs/test_vectors/generate.py
Output: docs/test_vectors/protocol_v1.json  (committed; consumed by the
        per-implementation conformance tests).

Inputs are fixed/deterministic so the output is reproducible. Ed25519 is
deterministic (RFC 8032) given a fixed seed, so signatures are stable.
Sealed-box / passage receipts are intentionally excluded here (they involve
fresh randomness and per-reader keys).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "Backend", "src"))

import mmh3  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402

from scud.crypto import signing as S  # noqa: E402
from scud.crypto.bloom import build_bloom  # noqa: E402
from scud.crypto.key_id import compute_key_id  # noqa: E402
from scud.crypto.serialization import (  # noqa: E402
    pack_issued_key,
    pack_time_authority_grant,
)


def h(b: bytes) -> str:
    return b.hex()


# --- BLE chunked framing reference (§16.5) -----------------------------------
# Transport framing is reader<->phone only (no backend wire impl), so THIS is the
# normative reference the firmware (notify_chunked / inbound reassembly) and
# Android (writeChunked / ReassemblyBuf) must both reproduce. PDU layout:
#   [seq 1B] [flags 1B] [total_len 4B LE if HAS_TOTAL] [chunk_bytes]
# flags bit0 = LAST, bit1 = HAS_TOTAL (only the first PDU, seq=0, carries total_len).
_FLAG_LAST = 0x01
_FLAG_HAS_TOTAL = 0x02


def ble_frame(payload: bytes, max_pdu: int) -> list[bytes]:
    """Split payload into PDUs exactly as firmware/Android do (same arithmetic)."""
    frames: list[bytes] = []
    n = len(payload)
    sent = 0
    seq = 0
    while sent < n:
        is_first = seq == 0
        header = 6 if is_first else 2           # seq+flags(+total_len on first)
        can_send = max_pdu - header
        assert can_send >= 1, "max_pdu too small for header"
        this = min(n - sent, can_send)
        is_last = sent + this == n
        flags = (_FLAG_LAST if is_last else 0) | (_FLAG_HAS_TOTAL if is_first else 0)
        pdu = bytes([seq & 0xFF, flags])
        if is_first:
            pdu += n.to_bytes(4, "little")
        pdu += payload[sent:sent + this]
        frames.append(pdu)
        sent += this
        seq += 1
    return frames


def ble_reassemble(frames: list[bytes]) -> bytes:
    """Inverse of ble_frame — mirrors ReassemblyBuf.feed / firmware onWrite."""
    out = b""
    expect = 0
    total = -1
    for pdu in frames:
        seq, flags = pdu[0], pdu[1]
        off = 2
        if flags & _FLAG_HAS_TOTAL:
            total = int.from_bytes(pdu[2:6], "little")
            off = 6
            out = b""
            expect = 0
        assert seq == expect, f"seq gap: {seq} != {expect}"
        out += pdu[off:]
        expect = (expect + 1) & 0xFF
        if flags & _FLAG_LAST:
            assert total < 0 or len(out) == total, "total_len mismatch"
            return out
    raise ValueError("frame stream ended without LAST")


def build_corpus() -> dict:
    # --- signing domain tags (16 B each) ---
    domains: dict[str, str] = {}
    for name in sorted(dir(S)):
        if name.startswith("DOMAIN_"):
            tag = getattr(S, name)
            if isinstance(tag, (bytes, bytearray)) and len(tag) == 16:
                label = bytes(tag).rstrip(b"\x00").decode("ascii")
                domains[label] = h(bytes(tag))

    # --- BLAKE2s-128 raw ---
    blake_in = b"scud-conformance-v1"
    blake2s_128 = [{
        "input_hex": h(blake_in),
        "expected_hex": hashlib.blake2s(blake_in, digest_size=16).hexdigest(),
    }]

    # --- key_id = BLAKE2s(reader_id || phone_pubkey || issued_at_8LE || serial_4LE) ---
    reader_id = bytes(range(16))
    phone_pubkey = bytes(range(0x10, 0x30))
    issued_at = 1_700_000_000
    serial = 42
    kid = compute_key_id(reader_id, phone_pubkey, issued_at, serial)
    key_id_vec = [{
        "reader_id_hex": h(reader_id),
        "phone_pubkey_hex": h(phone_pubkey),
        "issued_at": issued_at,
        "serial": serial,
        "expected_hex": h(kid),
    }]

    # --- Ed25519 domain-separated signature (deterministic, fixed seed) ---
    seed = bytes([0x11]) * 32
    sk = SigningKey(seed)
    pub = bytes(sk.verify_key)
    payload = bytes(range(20))
    sig = S.sign_detached(seed, S.DOMAIN_KEY, payload)
    assert S.verify_detached(pub, S.DOMAIN_KEY, payload, sig)
    ed25519 = [{
        "seed_hex": h(seed),
        "pubkey_hex": h(pub),
        "domain": bytes(S.DOMAIN_KEY).rstrip(b"\x00").decode("ascii"),
        "payload_hex": h(payload),
        "signature_hex": h(sig),
    }]

    # --- MurmurHash3 x86_32 (bloom hash), unsigned ---
    mseed = 1234
    murmur3 = [{
        "data_hex": h(kid),
        "seed": mseed,
        "expected_u32": mmh3.hash(kid, mseed, signed=False),
    }]

    # --- Bloom filter bytes ---
    kids = [kid, bytes([0xFF]) * 16]
    m_bits, k_hashes, bseed = 256, 3, 99
    bloom = [{
        "key_ids_hex": [h(x) for x in kids],
        "m_bits": m_bits,
        "k_hashes": k_hashes,
        "seed": bseed,
        "expected_hex": h(build_bloom(kids, m_bits, k_hashes, bseed)),
    }]

    # --- issued_key (151 B, shared §5.1): server-built, signed under DOMAIN_KEY ---
    server_seed = bytes([0x22]) * 32
    server_pub = bytes(SigningKey(server_seed).verify_key)
    ik_reader = bytes(range(16))
    ik_phone = bytes(range(0x10, 0x30))
    ik_issued = 1_700_000_000
    ik_expires = 1_700_086_400
    ik_permit = bytes(range(0x30, 0x40))
    ik_serial = 42
    ik_payload = b"\x00\x00"
    ik_bytes = pack_issued_key(
        ik_reader, ik_phone, ik_issued, ik_expires, ik_permit, ik_serial,
        ik_payload, server_seed,
    )
    assert len(ik_bytes) == 151
    issued_key = [{
        "reader_id_hex": h(ik_reader),
        "phone_pubkey_hex": h(ik_phone),
        "issued_at": ik_issued,
        "expires_at": ik_expires,
        "permit_id_hex": h(ik_permit),
        "serial": ik_serial,
        "payload_hex": h(ik_payload),
        "server_seed_hex": h(server_seed),
        "server_pubkey_hex": h(server_pub),
        "key_id_hex": h(compute_key_id(ik_reader, ik_phone, ik_issued, ik_serial)),
        "expected_hex": h(ik_bytes),
    }]

    # --- time_authority_grant (148 B, shared §5.11): server-built, DOMAIN_TGR ---
    tg_reader = bytes(range(16))
    tg_auth_pub = bytes(range(0x40, 0x60))
    tg_auth_id = bytes(range(0x60, 0x70))
    tg_issued = 1_700_000_000
    tg_expires = 1_700_090_000
    tg_kind = 0x01  # soft
    tg_bytes = pack_time_authority_grant(
        tg_reader, tg_auth_pub, tg_auth_id, tg_issued, tg_expires, tg_kind, server_seed,
    )
    assert len(tg_bytes) == 148
    time_grant = [{
        "reader_id_hex": h(tg_reader),
        "authority_pubkey_hex": h(tg_auth_pub),
        "authority_id_hex": h(tg_auth_id),
        "issued_at": tg_issued,
        "expires_at": tg_expires,
        "kind": tg_kind,
        "server_seed_hex": h(server_seed),
        "server_pubkey_hex": h(server_pub),
        "expected_hex": h(tg_bytes),
    }]

    # --- passage_receipt (192 B, shared §15.3): reader-built, signed under DOMAIN_PSG ---
    # Backend has no packer (only parse_passage_receipt); build manually, mirroring firmware
    # op_passage (ESP32/.../ops/passage.cpp) and tests/test_passage.py::_build_receipt. Runtime
    # fields (nonce/passed_at/direction/flags) are PINNED for determinism; direction/verdict/flags
    # match what op_passage hardcodes (0x01/0x00/0x01).
    reader_seed = bytes([0x33]) * 32  # reader_ed25519_priv (fixed; phone/server cannot forge)
    reader_pub = bytes(SigningKey(reader_seed).verify_key)
    pr_reader_id = bytes(range(16))
    pr_nonce = bytes(range(0x50, 0x60))
    pr_key_id = bytes(range(0x60, 0x70))
    pr_passed_at = 1_700_000_500
    pr_direction, pr_verdict, pr_session_seq, pr_flags = 0x01, 0x00, 7, 0x01
    pr_phone = bytes(range(0x10, 0x30))
    pr_permit = bytes(range(0x30, 0x40))
    pr_ik_issued, pr_ik_serial = 1_700_000_000, 42
    pr_body = (
        b"\x01" + pr_reader_id + pr_nonce + pr_key_id
        + pr_passed_at.to_bytes(8, "little")
        + bytes([pr_direction, pr_verdict])
        + pr_session_seq.to_bytes(4, "little")
        + bytes([pr_flags]) + pr_phone + pr_permit
        + pr_ik_issued.to_bytes(8, "little")
        + pr_ik_serial.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"  # padding @124
    )
    assert len(pr_body) == 128
    pr_bytes = pr_body + S.sign_detached(reader_seed, S.DOMAIN_PSG, pr_body)
    assert len(pr_bytes) == 192
    passage_receipt = [{
        "reader_id_hex": h(pr_reader_id), "receipt_nonce_hex": h(pr_nonce),
        "key_id_hex": h(pr_key_id), "passed_at": pr_passed_at,
        "direction": pr_direction, "verdict": pr_verdict,
        "session_seq": pr_session_seq, "flags": pr_flags,
        "phone_pubkey_hex": h(pr_phone), "permit_id_hex": h(pr_permit),
        "issued_key_issued_at": pr_ik_issued, "issued_key_serial": pr_ik_serial,
        "reader_seed_hex": h(reader_seed), "reader_pubkey_hex": h(reader_pub),
        "expected_hex": h(pr_bytes),
    }]

    # --- INFO (146 B, shared §5.2): reader-built, signed under DOMAIN_INF over header[0:82] ---
    # Backend has no pack_info/parse_info; build manually, mirroring firmware build_info_bytes
    # (ops_common.cpp). Same reader key as passage_receipt (one reader signs both).
    inf_reader_id = bytes(range(16))
    inf_reader_time = 1_700_000_000
    inf_proto, inf_max_apdu = 1, 240          # PROTOCOL_VERSION, MAX_APDU_DATA_SIZE
    inf_filter_ver, inf_filter_deliv = 7, 1_699_990_000
    inf_bl_count = 3
    inf_nonce = bytes(range(0x40, 0x60))
    inf_session_seq = 5
    inf_header = (
        b"\x01" + inf_reader_id
        + inf_reader_time.to_bytes(8, "little")
        + bytes([inf_proto])
        + inf_max_apdu.to_bytes(2, "little")
        + inf_filter_ver.to_bytes(8, "little")
        + inf_filter_deliv.to_bytes(8, "little")
        + inf_bl_count.to_bytes(2, "little")
        + inf_nonce
        + inf_session_seq.to_bytes(4, "little")
    )
    assert len(inf_header) == 82
    inf_bytes = inf_header + S.sign_detached(reader_seed, S.DOMAIN_INF, inf_header)
    assert len(inf_bytes) == 146
    info = [{
        "reader_id_hex": h(inf_reader_id),
        "reader_time": inf_reader_time,
        "protocol_version": inf_proto,
        "max_apdu_size": inf_max_apdu,
        "filter_version": inf_filter_ver,
        "filter_delivered_at": inf_filter_deliv,
        "blacklist_count": inf_bl_count,
        "fresh_nonce_hex": h(inf_nonce),
        "session_seq": inf_session_seq,
        "reader_seed_hex": h(reader_seed),
        "reader_pubkey_hex": h(reader_pub),
        "expected_hex": h(inf_bytes),
    }]

    # --- delivery_receipt (112 B, shared §5.7): reader-built, signed under DOMAIN_RCP over bytes[0:48] ---
    # Backend has parse_delivery_receipt (no packer); build manually, mirroring firmware
    # op_filter_update (ESP32/.../ops/filter_update.cpp). Same reader key as above.
    dr_reader_id = bytes(range(16))
    dr_filter_version = 7
    dr_applied_at = 1_700_000_700
    dr_courier_id = bytes(range(0x70, 0x80))
    dr_body = (
        dr_reader_id
        + dr_filter_version.to_bytes(8, "little")
        + dr_applied_at.to_bytes(8, "little")
        + dr_courier_id
    )
    assert len(dr_body) == 48
    dr_bytes = dr_body + S.sign_detached(reader_seed, S.DOMAIN_RCP, dr_body)
    assert len(dr_bytes) == 112
    delivery_receipt = [{
        "reader_id_hex": h(dr_reader_id),
        "applied_filter_version": dr_filter_version,
        "applied_at": dr_applied_at,
        "courier_id_hex": h(dr_courier_id),
        "reader_seed_hex": h(reader_seed), "reader_pubkey_hex": h(reader_pub),
        "expected_hex": h(dr_bytes),
    }]

    # --- FDI result (241 B, shared §5.8): reader-built, signed under DOMAIN_FDI over bytes[0:145] ---
    # Backend has parse_fdi_blob (no packer); build manually. The 104-B
    # encrypted_courier_blob is a sealed box at runtime (random ephemeral key -> NOT
    # byte-deterministic), so the vector uses DETERMINISTIC FILLER there: the conformance
    # check proves the 241-B framing and the reader signature over bytes[0:145], treating
    # the blob as opaque (its sealed-box crypto is non-reproducible by construction).
    # next_nonce (offset 209) is AFTER the signature and is NOT in the signed range.
    fdi_reader_id = bytes(range(16))
    fdi_filter_version = 7
    fdi_filter_delivered_at = 1_700_000_800
    fdi_courier_blob = bytes(i & 0xFF for i in range(104))  # deterministic filler (opaque)
    fdi_reader_time_now = 1_700_000_900
    fdi_next_nonce = bytes(range(0x90, 0xB0))  # 32 B
    fdi_signed = (
        b"\x91" + fdi_reader_id
        + fdi_filter_version.to_bytes(8, "little")
        + fdi_filter_delivered_at.to_bytes(8, "little")
        + fdi_courier_blob
        + fdi_reader_time_now.to_bytes(8, "little")
    )
    assert len(fdi_signed) == 145
    fdi_bytes = (
        fdi_signed
        + S.sign_detached(reader_seed, S.DOMAIN_FDI, fdi_signed)
        + fdi_next_nonce
    )
    assert len(fdi_bytes) == 241
    fdi = [{
        "result_marker": 0x91,
        "reader_id_hex": h(fdi_reader_id),
        "filter_version": fdi_filter_version,
        "filter_delivered_at": fdi_filter_delivered_at,
        "encrypted_courier_blob_hex": h(fdi_courier_blob),
        "reader_time_now": fdi_reader_time_now,
        "next_nonce_hex": h(fdi_next_nonce),
        "reader_seed_hex": h(reader_seed), "reader_pubkey_hex": h(reader_pub),
        "expected_hex": h(fdi_bytes),
    }]

    # --- handover_token (167 B, shared §17.1): reader-built, signed under DOMAIN_BLE over bytes[0:103] ---
    # NFC→BLE handover (plan 08 §4.3). Backend is NOT in this wire (reader<->phone),
    # but it is the reference packer (same style as delivery_receipt/INFO/FDI): a
    # manual pack+sign with the test reader priv. Runtime fields (issued_at/expires_at/
    # tap_nonce/ble_addr) are PINNED constants for determinism — NOT Date.now. Same
    # reader key as passage_receipt/INFO/delivery_receipt (one reader signs all).
    #   off  size  field
    #   0    1     format_version = 0x99
    #   1    16    reader_id
    #   17   32    issued_to_phone_pubkey
    #   49   6     reader_ble_addr
    #   55   32    tap_nonce
    #   87   8     issued_at  (LE u64)
    #   95   8     expires_at (LE u64)
    #   103  64    reader_signature = sign_reader(DOMAIN_BLE, bytes[0:103])
    ho_reader_id = bytes(range(16))
    ho_phone_pubkey = bytes(range(0x10, 0x30))           # phone Ed25519 identity (32)
    ho_ble_addr = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])  # reader BLE MAC (6)
    ho_tap_nonce = bytes(range(0x40, 0x60))              # the tap's fresh_nonce (32)
    ho_issued_at = 1_700_000_000
    ho_ttl_s = 60
    ho_expires_at = ho_issued_at + ho_ttl_s              # issued_at + HANDOVER_TOKEN_TTL_S
    ho_signed = (
        b"\x99" + ho_reader_id + ho_phone_pubkey + ho_ble_addr + ho_tap_nonce
        + ho_issued_at.to_bytes(8, "little")
        + ho_expires_at.to_bytes(8, "little")
    )
    assert len(ho_signed) == 103
    ho_bytes = ho_signed + S.sign_detached(reader_seed, S.DOMAIN_BLE, ho_signed)
    assert len(ho_bytes) == 167
    handover_token = [{
        "format_version": 0x99,
        "reader_id_hex": h(ho_reader_id),
        "issued_to_phone_pubkey_hex": h(ho_phone_pubkey),
        "reader_ble_addr_hex": h(ho_ble_addr),
        "tap_nonce_hex": h(ho_tap_nonce),
        "issued_at": ho_issued_at,
        "expires_at": ho_expires_at,
        "reader_seed_hex": h(reader_seed), "reader_pubkey_hex": h(reader_pub),
        "expected_hex": h(ho_bytes),
    }]

    # --- BLE chunked framing (§16.5): reader<->phone transport (no backend impl) ---
    # ble_frame/ble_reassemble above ARE the reference. Firmware (notify_chunked)
    # and Android (writeChunked/ReassemblyBuf) must reproduce the exact PDU sequence
    # — chunk boundaries + headers. Cases pin the off-by-one-prone boundaries.
    def _mk_framing(label: str, n: int, max_pdu: int) -> dict:
        payload = bytes(i & 0xFF for i in range(n))
        frames = ble_frame(payload, max_pdu)
        assert ble_reassemble(frames) == payload  # round-trip self-check
        return {
            "label": label,
            "max_pdu": max_pdu,
            "payload_len": n,
            "payload_hex": h(payload),
            "frame_count": len(frames),
            "frames_hex": [h(f) for f in frames],
        }

    ble_framing = [
        _mk_framing("info_single_pdu", 146, 240),       # INFO 146 B -> 1 PDU
        _mk_framing("first_pdu_exact_fit", 234, 240),   # 240-6 -> still exactly 1 PDU
        _mk_framing("first_pdu_plus_one", 235, 240),    # boundary: spills to 2 PDUs
        _mk_framing("access_two_pdu", 256, 240),        # ACCESS 256 B -> 2 PDUs
        _mk_framing("multi_three_pdu", 500, 240),       # 3 PDUs
        _mk_framing("small_mtu_unnegotiated", 50, 20),  # MTU 23 -> max_pdu 20, many PDUs
        _mk_framing("large_payload", 1000, 240),        # stress: multi-PDU
    ]

    # --- APDU/NFC transport framing (§3.2, §4.5-4.7): reader(PN532)<->phone(HCE) ---
    # No backend wire impl; this generator is the reference. Each vector pins the
    # exact data-body field offsets that firmware (transport/transfer.cpp) and
    # Android (hce/TapController) must agree on. All six were reconciled
    # consistent across spec+firmware+android (0 blocker discrepancies). All
    # multi-byte integers are little-endian. Offsets are within each data body
    # (FETCH responses include the leading status/marker byte; command bodies
    # exclude the APDU CLA/INS/P1/P2/Lc header — emitted separately as apdu_hex).
    def u16(n: int) -> bytes:
        return n.to_bytes(2, "little")

    def u32(n: int) -> bytes:
        return n.to_bytes(4, "little")

    a_inner = 0x13                                  # FILTER_UPDATE inner_opcode
    a_msg_id = 0x11223344
    a_total = 1000
    a_fcl = 234
    a_first_chunk = bytes(i & 0xFF for i in range(a_fcl))
    a_offset = 234
    a_max_chunk = 240
    a_chunk = bytes(i & 0xFF for i in range(64))

    # OP_CHUNKED FETCH response payload: [0x02][inner][msg_id 4][total 4][fcl 2][first_chunk]
    op_chunked_body = bytes([0x02, a_inner]) + u32(a_msg_id) + u32(a_total) + u16(a_fcl) + a_first_chunk
    assert len(op_chunked_body) == 12 + a_fcl
    # OP_SINGLE FETCH response payload: [0x01][inner][op_len 2][op_bytes]
    os_inner = 0x01
    os_bytes = bytes(0x40 + (i & 0x3F) for i in range(24))
    op_single_body = bytes([0x01, os_inner]) + u16(len(os_bytes)) + os_bytes
    assert len(op_single_body) == 4 + 24
    # READ_CHUNK command data body: [msg_id 4][offset 4][max_chunk 2]; APDU 00 C3 00 00 0A | body | 00
    rc_cmd_body = u32(a_msg_id) + u32(a_offset) + u16(a_max_chunk)
    rc_cmd_apdu = bytes([0x00, 0xC3, 0x00, 0x00, len(rc_cmd_body)]) + rc_cmd_body + bytes([0x00])
    assert len(rc_cmd_body) == 10
    # READ_CHUNK response data body: [chunk_len 2][flags 1][chunk]  (flags bit0 = LAST)
    rc_resp_body = u16(len(a_chunk)) + bytes([0x01]) + a_chunk
    # PUSH_CHUNK command data body: [msg_id 4][offset 4][total 4][flags 1][chunk_len 2][chunk]
    pc_body = u32(a_msg_id) + u32(a_offset) + u32(a_total) + bytes([0x00]) + u16(len(a_chunk)) + a_chunk
    pc_apdu = bytes([0x00, 0xC4, 0x00, 0x00, len(pc_body)]) + pc_body + bytes([0x00])
    assert len(pc_body) == 15 + len(a_chunk)
    # REFERENCE prev_result: [0xFF 0xFF][msg_id 4]; in FETCH cmd 00 C2 00 00 06 | body | 00
    ref_body = bytes([0xFF, 0xFF]) + u32(a_msg_id)
    ref_apdu = bytes([0x00, 0xC2, 0x00, 0x00, len(ref_body)]) + ref_body + bytes([0x00])
    assert len(ref_body) == 6

    apdu_framing = {
        "_doc": "reader<->phone NFC framing (§3.2/§4); offsets within each data_hex; integers little-endian.",
        "op_chunked": {
            "marker": 0x02, "inner_opcode": a_inner, "msg_id": a_msg_id,
            "total_len": a_total, "first_chunk_len": a_fcl,
            "first_chunk_hex": h(a_first_chunk), "data_hex": h(op_chunked_body),
        },
        "op_single": {
            "marker": 0x01, "inner_opcode": os_inner, "op_len": len(os_bytes),
            "op_bytes_hex": h(os_bytes), "data_hex": h(op_single_body),
        },
        "read_chunk_cmd": {
            "msg_id": a_msg_id, "offset": a_offset, "max_chunk_len": a_max_chunk,
            "data_hex": h(rc_cmd_body), "apdu_hex": h(rc_cmd_apdu),
        },
        "read_chunk_resp": {
            "chunk_len": len(a_chunk), "flags": 0x01, "chunk_hex": h(a_chunk),
            "data_hex": h(rc_resp_body),
        },
        "push_chunk_cmd": {
            "msg_id": a_msg_id, "offset": a_offset, "total": a_total, "flags": 0x00,
            "chunk_len": len(a_chunk), "chunk_hex": h(a_chunk),
            "data_hex": h(pc_body), "apdu_hex": h(pc_apdu),
        },
        "reference": {
            "msg_id": a_msg_id, "data_hex": h(ref_body), "apdu_hex": h(ref_apdu),
        },
    }

    return {
        "_meta": {
            "version": 1,
            "note": (
                "Golden conformance vectors for the SCUD byte-exact protocol. "
                "Generated from the Backend reference implementation; every "
                "implementation (Python/Kotlin/C++) MUST reproduce these exactly."
            ),
            "regenerate": "python docs/test_vectors/generate.py",
        },
        "domains": domains,
        "blake2s_128": blake2s_128,
        "key_id": key_id_vec,
        "ed25519": ed25519,
        "murmur3_x86_32": murmur3,
        "bloom": bloom,
        "issued_key": issued_key,
        "time_grant": time_grant,
        "passage_receipt": passage_receipt,
        "info": info,
        "delivery_receipt": delivery_receipt,
        "fdi": fdi,
        "handover_token": handover_token,
        "ble_framing": ble_framing,
        "apdu_framing": apdu_framing,
    }


def main() -> None:
    corpus = build_corpus()
    out = os.path.join(HERE, "protocol_v1.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
