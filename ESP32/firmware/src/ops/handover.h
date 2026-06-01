#pragma once
#include <stdint.h>
#include <stddef.h>

// NFC→BLE handover (shared §17.1, plan 08 §4.3).
//
// The handover lets a courier that has just passed a physical NFC ACCESS at a
// mains+BLE reader authorize a BLE filter-delivery stream WITHOUT a second full
// crypto cycle — the BLE stream is authorized by the physical tap, bound to the
// tap's fresh_nonce.
//
// Wire (handover_token, 167 B, reader-signed, marker 0x99):
//   off  size  field
//   0    1     format_version = 0x99
//   1    16    reader_id
//   17   32    issued_to_phone_pubkey   (phone Ed25519 identity)
//   49   6     reader_ble_addr          (the BLE MAC the phone connects to)
//   55   32    tap_nonce                (the fresh_nonce in force during the tap)
//   87   8     issued_at   (LE uint64, reader clock seconds)
//   95   8     expires_at  (LE uint64; issued_at + HANDOVER_TOKEN_TTL_S)
//   103  64    reader_signature = sign_reader(DOMAIN_BLE, bytes[0:103])
//
// All multi-byte integers little-endian (same convention as session_token §17 /
// existing receipts). Signed region = bytes[0:103].
#define HANDOVER_TOKEN_LEN        167
#define HANDOVER_TOKEN_SIGNED_LEN 103   // bytes[0:103] are signed under DOMAIN_BLE

// Build the handover_token + stage g_state.pending_handover. NFC only.
size_t op_handover_issue(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);

// Verify a presented handover_token + binding; authorize the BLE FILTER stream on
// success (one-shot). BLE only.
size_t op_handover_present(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
