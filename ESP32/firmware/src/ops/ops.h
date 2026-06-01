#pragma once
#include <stdint.h>
#include <stddef.h>
#include "../transport/op_sink.h"

// Result codes (shared §7).
#define RES_OK                 0x00
#define RES_BAD_SIGNATURE      0x01
#define RES_BAD_FORMAT         0x02
#define RES_BAD_NONCE          0x03
#define RES_NO_SLOT            0x04
#define RES_NOT_AUTHORIZED     0x05
#define RES_STALE              0x06
#define RES_WRONG_READER       0x07
#define RES_TIME_REGRESSION    0x08
#define RES_UNKNOWN_OPCODE     0x09
#define RES_INTERNAL_ERROR     0xFF

// ACCESS-specific result codes.
#define RES_EXPIRED            0x20
#define RES_REVOKED_BLACKLIST  0x21
#define RES_REVOKED_FILTER     0x22

// Result markers (shared §5.4 и §6).
#define MARK_ACCESS_VERDICT    0x81
#define MARK_FDI               0x91
#define MARK_OP_RESULT_TSYNC   0x92
#define MARK_OP_RESULT_FLT     0x93
#define MARK_BLK               0x94
#define MARK_OP_RESULT_REV     0x95
// H3 FOLD-IN: the standalone GET_PASSAGE_RECEIPT markers (0x96 PASSAGE_ENVELOPE /
// 0x97 PASSAGE_NONE) are RETIRED — the passage_receipt now rides the tail of the
// ACCESS_VERDICT response (response[42:234] on RES_OK), there is no separate op.
#define MARK_HANDOVER_TOKEN    0x99    // handover_token packet (shared §17.1) — reader-signed, 167 B

// HANDOVER op-result markers (NFC→BLE handover, shared §17.1). Generic OP_RESULT
// envelopes (make_op_result) for the PRESENT verify ack and ISSUE rejects.
#define MARK_OP_RESULT_HANDOVER 0x9A   // INNER_HANDOVER_PRESENT verify result (BLE)

// Inner opcodes (shared §3.1).
#define INNER_ACCESS           0x01
#define INNER_FDI              0x11
#define INNER_TIME_SYNC        0x12
#define INNER_FILTER_UPDATE    0x13
#define INNER_BLACKLIST        0x14
#define INNER_REVOKE_KEY       0x15
// 0x16 (GET_PASSAGE_RECEIPT) — RETIRED by H3: the receipt is folded into the
// ACCESS_VERDICT tail; the opcode is no longer dispatched.
#define INNER_HANDOVER_ISSUE   0x17    // NFC, phone→reader: issue handover_token (shared §17.1)
#define INNER_HANDOVER_PRESENT 0x18    // BLE, phone→reader: present handover_token (shared §17.1)

// Build INFO structure (shared §5.2). Returns 146 on success.
size_t build_info_bytes(uint8_t out[146]);

// Handlers.
size_t op_access(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_time_sync(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_filter_update(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);

// N2/B6 — FILTER_UPDATE from a flash-streamed package. The transport layer has
// already streamed the whole filter_package into `sink` (the inactive A/B slot).
// `courier_id` (16 B) and `pkg_header` (the first 56 B of the package — already
// in RAM from the first chunk) drive the header validations; the body + signature
// are read back from the sink (verify-from-flash + atomic swap, no 16 KB cap).
// Returns the result length (delivery_receipt envelope), like op_filter_update.
size_t op_filter_update_from_flash(const uint8_t* courier_id,
                                   const uint8_t* pkg_header, size_t pkg_header_len,
                                   op_sink* sink,
                                   uint8_t* resp, size_t resp_max);
size_t op_fdi(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_blacklist(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_revoke(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);

// HANDOVER — NFC→BLE handover (shared §17.1, plan 08 §4.3). See ops/handover.cpp.
//   op_handover_issue   — NFC only: captures the current fresh_nonce (the tap's
//                         PUSH_INFO nonce) into g_state.pending_handover and returns
//                         the 167-B reader-signed handover_token.
//   op_handover_present — BLE only: verifies the token + binding (tap_nonce,
//                         phone_pubkey, expiry) and, on success, authorizes the
//                         FILTER stream on THIS connection (handover_authorized).
size_t op_handover_issue(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);
size_t op_handover_present(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);

// PASSAGE — receipt builder (shared §15.3). H3 FOLD-IN: no session cache, no
// separate op. op_access calls this on the RES_OK path and APPENDS the resulting
// 192-B receipt to the ACCESS_VERDICT response at offset 42 (response[42:234]).
// Pure helper (in ops/passage.cpp) so the field layout + DOMAIN_PSG signing live
// in one host-testable place.
//   * out             — 192 B receipt (signed region out[0:128], sig out[128:192]);
//   * key_id          — 16 B, ключ, которым прошли;
//   * phone_pubkey    — 32 B, публичный ключ телефона (для backend lookup);
//   * permit_id       — 16 B, echo из issued_key;
//   * issued_key_issued_at, issued_key_serial — поля из issued_key (chain-of-custody).
void build_passage_receipt(uint8_t out[192],
                           const uint8_t key_id[16],
                           const uint8_t phone_pubkey[32],
                           const uint8_t permit_id[16],
                           uint64_t issued_key_issued_at,
                           uint32_t issued_key_serial);

// Helpers.
size_t make_op_result(uint8_t* resp, uint8_t in_reply_to, uint8_t marker, uint8_t code,
                      const uint8_t* ext, uint16_t ext_len);
