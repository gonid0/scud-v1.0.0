#!/usr/bin/env python3
"""Emit a C header of the golden vectors from protocol_v1.json (single source).

The firmware has no JSON parser, so its conformance test consumes this
generated header instead of the JSON directly. The values come from the SAME
corpus the backend/Android tests use, so all three check identical bytes.

Run:  python docs/test_vectors/gen_c_header.py
Out:  ESP32/firmware/test_host/vectors_generated.h
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def carr(hexstr: str) -> tuple[str, int]:
    b = bytes.fromhex(hexstr)
    return "{" + ", ".join(f"0x{x:02x}" for x in b) + "}", len(b)


def main() -> None:
    j = json.load(open(os.path.join(HERE, "protocol_v1.json"), encoding="utf-8"))
    L: list[str] = [
        "// GENERATED from docs/test_vectors/protocol_v1.json by",
        "// docs/test_vectors/gen_c_header.py -- DO NOT EDIT.",
        "#pragma once",
        "#include <stdint.h>",
        "#include <stddef.h>",
        "",
    ]

    # signing domains: V_DOMAIN_<SUFFIX>[16]
    for label, hx in j["domains"].items():
        suffix = label.split("-")[1]  # RDR-KEY-v1 -> KEY
        arr, n = carr(hx)
        L.append(f"static const uint8_t V_DOMAIN_{suffix}[{n}] = {arr};")
    L.append("")

    # key_id
    kv = j["key_id"][0]
    for nm, key in (("READER", "reader_id_hex"), ("PHONE", "phone_pubkey_hex"),
                    ("EXPECTED", "expected_hex")):
        arr, n = carr(kv[key])
        L.append(f"static const uint8_t V_KEYID_{nm}[{n}] = {arr};")
    L.append(f"static const uint64_t V_KEYID_ISSUED = {kv['issued_at']}ULL;")
    L.append(f"static const uint32_t V_KEYID_SERIAL = {kv['serial']}u;")
    L.append("")

    # blake2s-128
    bv = j["blake2s_128"][0]
    arr, n = carr(bv["input_hex"])
    L.append(f"static const uint8_t V_BLAKE_IN[{n}] = {arr};")
    L.append(f"static const size_t V_BLAKE_IN_LEN = {n};")
    arr, n = carr(bv["expected_hex"])
    L.append(f"static const uint8_t V_BLAKE_EXPECTED[{n}] = {arr};")
    L.append("")

    # murmur3 x86_32
    mv = j["murmur3_x86_32"][0]
    arr, n = carr(mv["data_hex"])
    L.append(f"static const uint8_t V_MURMUR_DATA[{n}] = {arr};")
    L.append(f"static const int V_MURMUR_LEN = {n};")
    L.append(f"static const uint32_t V_MURMUR_SEED = {mv['seed']}u;")
    L.append(f"static const uint32_t V_MURMUR_EXPECTED = {mv['expected_u32']}u;")
    L.append("")

    # bloom (firmware only checks membership: bloom_contains)
    blv = j["bloom"][0]
    arr, n = carr(blv["expected_hex"])
    L.append(f"static const uint8_t V_BLOOM_BYTES[{n}] = {arr};")
    L.append(f"static const uint32_t V_BLOOM_M = {blv['m_bits']}u;")
    L.append(f"static const uint8_t V_BLOOM_K = {blv['k_hashes']}u;")
    L.append(f"static const uint32_t V_BLOOM_SEED = {blv['seed']}u;")
    for i, kh in enumerate(blv["key_ids_hex"]):
        arr, n = carr(kh)
        L.append(f"static const uint8_t V_BLOOM_KID{i}[{n}] = {arr};")
    L.append("")

    # Ed25519 domain-separated signature (Monocypher on firmware).
    # The signed message is domain_tag(16) || payload; domain tag = V_DOMAIN_<suffix>.
    ev = j["ed25519"][0]
    for nm, key in (("SEED", "seed_hex"), ("PUBKEY", "pubkey_hex"),
                    ("PAYLOAD", "payload_hex"), ("SIG", "signature_hex")):
        arr, n = carr(ev[key])
        L.append(f"static const uint8_t V_ED_{nm}[{n}] = {arr};")
    L.append(f"static const size_t V_ED_PAYLOAD_LEN = "
             f"{len(bytes.fromhex(ev['payload_hex']))};")
    L.append(f"// ed25519 vector domain = {ev['domain']} "
             f"-> use V_DOMAIN_{ev['domain'].split('-')[1]}")
    L.append("")

    # issued_key (151 B): parse layout + server signature (DOMAIN_KEY over header[0:87])
    ikv = j["issued_key"][0]
    arr, n = carr(ikv["expected_hex"])
    L.append(f"static const uint8_t V_IK_BLOB[{n}] = {arr};")
    arr, n = carr(ikv["server_pubkey_hex"])
    L.append(f"static const uint8_t V_IK_SERVER_PUB[{n}] = {arr};")
    arr, n = carr(ikv["key_id_hex"])
    L.append(f"static const uint8_t V_IK_KEYID[{n}] = {arr};")
    arr, n = carr(ikv["permit_id_hex"])
    L.append(f"static const uint8_t V_IK_PERMIT[{n}] = {arr};")
    L.append(f"static const uint64_t V_IK_EXPIRES = {ikv['expires_at']}ULL;")
    L.append("")

    # time_grant (148 B): parse layout + server signature (DOMAIN_TGR over header[0:84])
    tgv = j["time_grant"][0]
    arr, n = carr(tgv["expected_hex"])
    L.append(f"static const uint8_t V_TG_BLOB[{n}] = {arr};")
    arr, n = carr(tgv["server_pubkey_hex"])
    L.append(f"static const uint8_t V_TG_SERVER_PUB[{n}] = {arr};")
    arr, n = carr(tgv["authority_id_hex"])
    L.append(f"static const uint8_t V_TG_AUTHID[{n}] = {arr};")
    L.append(f"static const uint8_t V_TG_KIND = {tgv['kind']}u;")
    L.append("")

    # passage_receipt (192 B): parse layout + reader signature (DOMAIN_PSG over body[0:128])
    prv = j["passage_receipt"][0]
    arr, n = carr(prv["expected_hex"])
    L.append(f"static const uint8_t V_PR_BLOB[{n}] = {arr};")
    arr, n = carr(prv["reader_pubkey_hex"])
    L.append(f"static const uint8_t V_PR_READER_PUB[{n}] = {arr};")
    arr, n = carr(prv["key_id_hex"])
    L.append(f"static const uint8_t V_PR_KEYID[{n}] = {arr};")
    arr, n = carr(prv["permit_id_hex"])
    L.append(f"static const uint8_t V_PR_PERMIT[{n}] = {arr};")
    arr, n = carr(prv["phone_pubkey_hex"])
    L.append(f"static const uint8_t V_PR_PHONE[{n}] = {arr};")
    L.append(f"static const uint64_t V_PR_PASSED_AT = {prv['passed_at']}ULL;")
    L.append(f"static const uint32_t V_PR_SESSION_SEQ = {prv['session_seq']}u;")
    L.append(f"static const uint8_t  V_PR_DIRECTION = {prv['direction']}u;")
    L.append("")

    # INFO (146 B): parse layout + reader signature (DOMAIN_INF over header[0:82])
    iv = j["info"][0]
    arr, n = carr(iv["expected_hex"])
    L.append(f"static const uint8_t V_INFO_BLOB[{n}] = {arr};")
    arr, n = carr(iv["reader_id_hex"])
    L.append(f"static const uint8_t V_INFO_READER[{n}] = {arr};")
    arr, n = carr(iv["fresh_nonce_hex"])
    L.append(f"static const uint8_t V_INFO_NONCE[{n}] = {arr};")
    arr, n = carr(iv["reader_pubkey_hex"])
    L.append(f"static const uint8_t V_INFO_READER_PUB[{n}] = {arr};")
    L.append(f"static const uint64_t V_INFO_READER_TIME = {iv['reader_time']}ULL;")
    L.append(f"static const uint8_t  V_INFO_PROTO = {iv['protocol_version']}u;")
    L.append(f"static const uint16_t V_INFO_MAX_APDU = {iv['max_apdu_size']}u;")
    L.append(f"static const uint64_t V_INFO_FILTER_VER = {iv['filter_version']}ULL;")
    L.append(f"static const uint64_t V_INFO_FILTER_DELIV = {iv['filter_delivered_at']}ULL;")
    L.append(f"static const uint16_t V_INFO_BL_COUNT = {iv['blacklist_count']}u;")
    L.append(f"static const uint32_t V_INFO_SESSION_SEQ = {iv['session_seq']}u;")
    L.append("")

    # delivery_receipt (112 B): parse layout + reader signature (DOMAIN_RCP over bytes[0:48])
    drv = j["delivery_receipt"][0]
    arr, n = carr(drv["expected_hex"])
    L.append(f"static const uint8_t V_DR_BLOB[{n}] = {arr};")
    arr, n = carr(drv["reader_id_hex"])
    L.append(f"static const uint8_t V_DR_READER[{n}] = {arr};")
    arr, n = carr(drv["courier_id_hex"])
    L.append(f"static const uint8_t V_DR_COURIER[{n}] = {arr};")
    arr, n = carr(drv["reader_pubkey_hex"])
    L.append(f"static const uint8_t V_DR_READER_PUB[{n}] = {arr};")
    L.append(f"static const uint64_t V_DR_APPLIED_VER = {drv['applied_filter_version']}ULL;")
    L.append(f"static const uint64_t V_DR_APPLIED_AT = {drv['applied_at']}ULL;")
    L.append("")

    # FDI result (241 B): parse layout + reader signature (DOMAIN_FDI over bytes[0:145]).
    # encrypted_courier_blob @33 is opaque filler in the vector (a runtime sealed box is
    # non-deterministic); next_nonce @209 is NOT covered by the signature.
    fdv = j["fdi"][0]
    arr, n = carr(fdv["expected_hex"])
    L.append(f"static const uint8_t V_FDI_BLOB[{n}] = {arr};")
    arr, n = carr(fdv["reader_id_hex"])
    L.append(f"static const uint8_t V_FDI_READER[{n}] = {arr};")
    arr, n = carr(fdv["encrypted_courier_blob_hex"])
    L.append(f"static const uint8_t V_FDI_COURIER[{n}] = {arr};")
    arr, n = carr(fdv["next_nonce_hex"])
    L.append(f"static const uint8_t V_FDI_NONCE[{n}] = {arr};")
    arr, n = carr(fdv["reader_pubkey_hex"])
    L.append(f"static const uint8_t V_FDI_READER_PUB[{n}] = {arr};")
    L.append(f"static const uint64_t V_FDI_FILTER_VER = {fdv['filter_version']}ULL;")
    L.append(f"static const uint64_t V_FDI_DELIV = {fdv['filter_delivered_at']}ULL;")
    L.append(f"static const uint64_t V_FDI_TIME = {fdv['reader_time_now']}ULL;")
    L.append("")

    # handover_token (167 B, shared §17.1): parse layout + reader signature
    # (DOMAIN_BLE over bytes[0:103]). NFC→BLE handover (plan 08 §4.3). Reader-signed;
    # the reader verifies its OWN signature on PRESENT (integrity / non-forgery).
    hov = j["handover_token"][0]
    arr, n = carr(hov["expected_hex"])
    L.append(f"static const uint8_t V_HOV_BLOB[{n}] = {arr};")
    arr, n = carr(hov["reader_id_hex"])
    L.append(f"static const uint8_t V_HOV_READER[{n}] = {arr};")
    arr, n = carr(hov["issued_to_phone_pubkey_hex"])
    L.append(f"static const uint8_t V_HOV_PHONE[{n}] = {arr};")
    arr, n = carr(hov["reader_ble_addr_hex"])
    L.append(f"static const uint8_t V_HOV_BLEADDR[{n}] = {arr};")
    arr, n = carr(hov["tap_nonce_hex"])
    L.append(f"static const uint8_t V_HOV_TAPNONCE[{n}] = {arr};")
    arr, n = carr(hov["reader_pubkey_hex"])
    L.append(f"static const uint8_t V_HOV_READER_PUB[{n}] = {arr};")
    L.append(f"static const uint8_t  V_HOV_FORMAT = {hov['format_version']}u;")
    L.append(f"static const uint64_t V_HOV_ISSUED_AT = {hov['issued_at']}ULL;")
    L.append(f"static const uint64_t V_HOV_EXPIRES_AT = {hov['expires_at']}ULL;")
    L.append("")

    # BLE chunked framing (§16.5): produce-side conformance. Each vector = payload
    # + max_pdu + expected PDU sequence (concatenated bytes + per-frame lengths).
    # Skip the 1000 B stress case (index 6) for the C header — Android covers it.
    framing_fw_idx = [i for i, fv in enumerate(j["ble_framing"]) if fv["payload_len"] <= 500]
    for i in framing_fw_idx:
        fv = j["ble_framing"][i]
        arr, n = carr(fv["payload_hex"])
        L.append(f"static const uint8_t V_BLEF{i}_PAYLOAD[{n}] = {arr};")
        L.append(f"static const uint32_t V_BLEF{i}_PAYLOAD_LEN = {n}u;")
        L.append(f"static const uint16_t V_BLEF{i}_MAXPDU = {fv['max_pdu']}u;")
        concat = "".join(fv["frames_hex"])
        arr, n = carr(concat)
        L.append(f"static const uint8_t V_BLEF{i}_CONCAT[{n}] = {arr};")
        L.append(f"static const size_t V_BLEF{i}_CONCAT_LEN = {n};")
        lens = [len(bytes.fromhex(f)) for f in fv["frames_hex"]]
        lit = "{" + ", ".join(str(x) for x in lens) + "}"
        L.append(f"static const uint16_t V_BLEF{i}_LENS[{len(lens)}] = {lit};")
        L.append(f"static const int V_BLEF{i}_NFRAMES = {len(lens)};")
        L.append(f"static const char V_BLEF{i}_LABEL[] = {chr(34)}{fv['label']}{chr(34)};")
        L.append("")

    # APDU/NFC transport framing (§3.2, §4): parse each data body at exact offsets.
    af = j["apdu_framing"]
    oc = af["op_chunked"]
    arr, n = carr(oc["data_hex"]); L.append(f"static const uint8_t V_AF_OC_DATA[{n}] = {arr};")
    L.append(f"static const uint8_t  V_AF_OC_INNER = {oc['inner_opcode']}u;")
    L.append(f"static const uint32_t V_AF_OC_MSGID = {oc['msg_id']}u;")
    L.append(f"static const uint32_t V_AF_OC_TOTAL = {oc['total_len']}u;")
    L.append(f"static const uint16_t V_AF_OC_FCL = {oc['first_chunk_len']}u;")
    arr, n = carr(oc["first_chunk_hex"]); L.append(f"static const uint8_t V_AF_OC_CHUNK[{n}] = {arr};")
    os_ = af["op_single"]
    arr, n = carr(os_["data_hex"]); L.append(f"static const uint8_t V_AF_OS_DATA[{n}] = {arr};")
    L.append(f"static const uint8_t  V_AF_OS_INNER = {os_['inner_opcode']}u;")
    L.append(f"static const uint16_t V_AF_OS_OPLEN = {os_['op_len']}u;")
    arr, n = carr(os_["op_bytes_hex"]); L.append(f"static const uint8_t V_AF_OS_BYTES[{n}] = {arr};")
    rc = af["read_chunk_cmd"]
    arr, n = carr(rc["data_hex"]); L.append(f"static const uint8_t V_AF_RCCMD_DATA[{n}] = {arr};")
    L.append(f"static const uint32_t V_AF_RCCMD_MSGID = {rc['msg_id']}u;")
    L.append(f"static const uint32_t V_AF_RCCMD_OFFSET = {rc['offset']}u;")
    L.append(f"static const uint16_t V_AF_RCCMD_MAXCHUNK = {rc['max_chunk_len']}u;")
    arr, n = carr(rc["apdu_hex"]); L.append(f"static const uint8_t V_AF_RCCMD_APDU[{n}] = {arr};")
    rr = af["read_chunk_resp"]
    arr, n = carr(rr["data_hex"]); L.append(f"static const uint8_t V_AF_RCRESP_DATA[{n}] = {arr};")
    L.append(f"static const uint16_t V_AF_RCRESP_CHUNKLEN = {rr['chunk_len']}u;")
    L.append(f"static const uint8_t  V_AF_RCRESP_FLAGS = {rr['flags']}u;")
    arr, n = carr(rr["chunk_hex"]); L.append(f"static const uint8_t V_AF_RCRESP_CHUNK[{n}] = {arr};")
    pc = af["push_chunk_cmd"]
    arr, n = carr(pc["data_hex"]); L.append(f"static const uint8_t V_AF_PC_DATA[{n}] = {arr};")
    L.append(f"static const uint32_t V_AF_PC_MSGID = {pc['msg_id']}u;")
    L.append(f"static const uint32_t V_AF_PC_OFFSET = {pc['offset']}u;")
    L.append(f"static const uint32_t V_AF_PC_TOTAL = {pc['total']}u;")
    L.append(f"static const uint8_t  V_AF_PC_FLAGS = {pc['flags']}u;")
    L.append(f"static const uint16_t V_AF_PC_CHUNKLEN = {pc['chunk_len']}u;")
    arr, n = carr(pc["chunk_hex"]); L.append(f"static const uint8_t V_AF_PC_CHUNK[{n}] = {arr};")
    arr, n = carr(pc["apdu_hex"]); L.append(f"static const uint8_t V_AF_PC_APDU[{n}] = {arr};")
    rf = af["reference"]
    arr, n = carr(rf["data_hex"]); L.append(f"static const uint8_t V_AF_REF_DATA[{n}] = {arr};")
    L.append(f"static const uint32_t V_AF_REF_MSGID = {rf['msg_id']}u;")
    L.append("")

    out = os.path.join(HERE, "..", "..", "ESP32", "firmware", "test_host",
                       "vectors_generated.h")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
