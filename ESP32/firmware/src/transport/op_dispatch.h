#pragma once
#include <stdint.h>
#include <stddef.h>

// X1 (partial — dispatch unification). The per-op dispatch + transport-policy
// decision used to be DUPLICATED in two places (transfer.cpp::dispatch_op for
// NFC and ble_channel.cpp's inline switch for BLE) that had already diverged once
// (the "BLE accepted ACCESS" bug class). This module is the SINGLE source of
// truth for the transport policy: which inner-opcodes ride which transport, and
// the exact (result-code, marker) bytes returned for each reject.
//
// Scope: this is the small / in-RAM dispatch path only — the one both call sites
// shared. The large-FILTER_UPDATE flash-streaming routes (transfer.cpp N2/B6 and
// ble_channel.cpp on_flash_op_complete) are HARDWARE-only and deliberately
// untouched (FW-ARC-01/N5/N4/N2); they keep their own handover gate. The framing
// /reassembly FSM unification + the L2CAP adapter remain deferred (T4 / full X1).

// Which transport a single op arrived on. The transport policy below branches on
// this — NFC carries physical proximity (the anti-relay defense for identity ops);
// BLE may be a ~10 m relay, so proximity-attested ops are refused on it.
enum OpTransport {
    OP_TRANSPORT_NFC,
    OP_TRANSPORT_BLE
};

// Dispatch by inner_opcode under the given transport policy. Fills `result`,
// returns the result length (0 = no result / unknown opcode — the caller logs).
//
// BYTE-IDENTICAL to the two former call sites: every accepted op routes to the
// same op_* handler, and every reject yields the same (code, marker) envelope on
// each transport as before this unification. See op_dispatch.cpp for the policy.
size_t dispatch_op(uint8_t inner, const uint8_t* op, size_t op_len,
                   uint8_t* result, size_t result_max, OpTransport t);
