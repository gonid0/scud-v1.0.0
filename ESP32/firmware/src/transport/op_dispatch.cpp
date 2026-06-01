#include "op_dispatch.h"
#include "../ops/ops.h"
#include "../state/reader_state.h"
#include <Arduino.h>

// X1 (partial): the single transport-policy source of truth. Produces results
// BYTE-IDENTICAL on BOTH transports (transfer.cpp NFC + ble_channel BLE inline
// switch) — the dispatch_op invariant. The (op, transport) → handler table:
//
//   op                NFC                       BLE
//   ----------------  ------------------------  --------------------------------
//   ACCESS            op_access                 op_access            (H1: both)
//   FDI               op_fdi                    op_fdi               (H1: both)
//   TIME_SYNC         op_time_sync              op_time_sync         (H1: both)
//   REVOKE_KEY        op_revoke                 op_revoke            (H1: both)
//   FILTER_UPDATE     op_filter_update          [handover gate] op_filter_update
//   BLACKLIST         op_blacklist              op_blacklist
//   HANDOVER_ISSUE    op_handover_issue         reject UNKNOWN_OPCODE + HANDOVER
//   HANDOVER_PRESENT  reject UNKNOWN_OPCODE +   op_handover_present
//                       HANDOVER
//   (default)         0                         0
//
// H1 TRANSPORT UNIFICATION — agreed model: ALL ops ride BOTH BLE and NFC. The
// former X3 NFC-only rejects for FDI / TIME_SYNC / REVOKE_KEY are REMOVED; they
// now route to their transport-agnostic handlers on both channels, exactly like
// ACCESS. The relay/wormhole risk is explicitly ACCEPTED; the only BLE-specific
// mitigation is the Android per-reader confirm-session UX (not a reader boundary).
// ACCESS stays OPEN on BLE (no handover gate). HANDOVER_ISSUE (NFC-only) /
// HANDOVER_PRESENT (BLE-only) and the FILTER_UPDATE handover_required gate are
// left exactly as they were. This is NOT a wire-format change — result bytes are
// byte-identical across transports, so conformance golden vectors do not change.
//
// H3 FOLD-IN: the standalone GET_PASSAGE_RECEIPT op (inner 0x16 / op_passage) is
// REMOVED. The passage_receipt now rides the ACCESS_VERDICT tail (built inline in
// op_access on RES_OK), so there is no INNER_PASSAGE arm here anymore.
size_t dispatch_op(uint8_t inner, const uint8_t* op, size_t op_len,
                   uint8_t* result, size_t result_max, OpTransport t) {
    switch (inner) {
        // ---- H1: ACCESS, FDI, TIME_SYNC, REVOKE_KEY ride BOTH transports. The
        // handlers are transport-agnostic; relay risk is accepted (see header). ----
        case INNER_ACCESS:
            return op_access(op, op_len, result, result_max);
        case INNER_FDI:
            return op_fdi(op, op_len, result, result_max);
        case INNER_TIME_SYNC:
            return op_time_sync(op, op_len, result, result_max);
        case INNER_REVOKE_KEY:
            return op_revoke(op, op_len, result, result_max);

        // ---- Bulk: server-signed, version-monotonic. Relay-safe; rides both. ----
        case INNER_FILTER_UPDATE:
            // T3 gate (plan 08 §4.3): when handover_required is provisioned, a BLE
            // FILTER_UPDATE must be authorized by a prior verified NFC→BLE handover
            // on THIS connection. Default (handover_required=0) keeps the existing
            // X2 path untouched — nothing regresses. Fail-closed when set + missing.
            // NFC has no gate (the tap itself is the proximity proof).
            if (t == OP_TRANSPORT_BLE &&
                g_state.cfg.handover_required && !g_state.handover_authorized) {
                Serial.println("[BLE] FILTER_UPDATE rejected: handover required, not authorized");
                return make_op_result(result, INNER_FILTER_UPDATE,
                                      MARK_OP_RESULT_FLT, RES_NOT_AUTHORIZED,
                                      nullptr, 0);
            }
            return op_filter_update(op, op_len, result, result_max);
        case INNER_BLACKLIST:
            return op_blacklist(op, op_len, result, result_max);

        // ---- HANDOVER (shared §17.1, plan 08 §4.3). ----
        case INNER_HANDOVER_ISSUE:
            // ISSUE is NFC-only (the token is born from a physical tap). Reject over
            // BLE — a phone cannot mint itself a handover without tapping.
            if (t == OP_TRANSPORT_NFC) {
                return op_handover_issue(op, op_len, result, result_max);
            }
            Serial.println("[BLE] HANDOVER_ISSUE over BLE rejected (NFC-only)");
            return make_op_result(result, INNER_HANDOVER_ISSUE,
                                  MARK_OP_RESULT_HANDOVER, RES_UNKNOWN_OPCODE,
                                  nullptr, 0);
        case INNER_HANDOVER_PRESENT:
            // PRESENT is BLE-only: the token is presented on the BLE connection it
            // authorizes (verify the handover_token + binding; on success authorize
            // the FILTER stream on this connection). Presenting it over NFC is
            // meaningless and must not pass — reject (fail-closed).
            if (t == OP_TRANSPORT_BLE) {
                return op_handover_present(op, op_len, result, result_max);
            }
            return make_op_result(result, INNER_HANDOVER_PRESENT,
                                  MARK_OP_RESULT_HANDOVER, RES_UNKNOWN_OPCODE,
                                  nullptr, 0);

        default:
            return 0;
    }
}
