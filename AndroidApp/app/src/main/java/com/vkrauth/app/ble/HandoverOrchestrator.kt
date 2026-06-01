package com.vkrauth.app.ble

import android.util.Log

/**
 * NFC→BLE handover orchestration (shared §17.1, plan 08 §4.3) — phone side.
 *
 * Full flow (courier delivering a filter to a mains reader advertising bulk+handover):
 * ```
 *   1. [NFC]  tap → ACCESS ok                                  (TapController / TapSession)
 *   2. [NFC]  send INNER_HANDOVER_ISSUE(phone_pubkey)          -> reader returns 167-B token
 *   3. [crypto] verify token reader_signature (DOMAIN_BLE)     (HandoverToken.verify)
 *   4. [BLE]  connect(reader_ble_addr from token/scan)         (BleSession.connect)
 *   5. [BLE]  runOperation(INNER_HANDOVER_PRESENT, token)      -> reader authorizes THIS conn
 *   6. [BLE]  stream INNER_FILTER_UPDATE                        (BleSession.runOperation)
 * ```
 *
 * Steps 4–6 are the BLE half and live here. Steps 1–2 (the NFC half that mints the
 * token) belong to the tap path; they are hardware-only (two radios + a physical
 * reader) and cannot be exercised in this environment.
 *
 * COMPILE-ONLY SEAM (runtime-unverified): the actual two-radio rendezvous — issuing
 * over NFC, then connecting BLE to the MAC the token carries while a real reader holds
 * the matching pending_handover — needs a phone + a mains reader. This orchestrator is
 * bounded and guarded so it compiles and unit-checks (token parse/verify is golden-
 * vector proven), but the live handoff is hardware-gated. See [presentAndDeliver].
 */
object HandoverOrchestrator {
    private const val TAG = "Handover"

    sealed interface Result {
        /** Filter delivered: the reader applied it and returned a delivery_receipt. */
        data class Delivered(val deliveryReceiptResult: ByteArray) : Result
        /** Reader rejected the PRESENT (binding/expiry/signature) — no delivery. */
        data class PresentRejected(val resultCode: Byte) : Result
        /** The token failed the phone-side reader-signature / binding sanity check. */
        data object TokenInvalid : Result
        /** Transport error (disconnect/timeout) somewhere in the BLE half. */
        data class TransportError(val message: String?) : Result
    }

    /**
     * BLE half of the handover (steps 4–6 above), on an ALREADY-CONNECTED session.
     *
     * @param session a [BleSession] already connected to the reader at the MAC the
     *   token carries (caller resolves [HandoverToken.bleAddrToMacString]).
     * @param token the raw 167-B handover_token obtained over NFC (step 2).
     * @param readerPubkey the reader Ed25519 pubkey the phone already trusts (from INFO).
     * @param filterUpdateOpBytes the full INNER_FILTER_UPDATE op payload to stream once
     *   authorized (opcode 0x13 + courier_id + filter_package).
     *
     * Returns a [Result]; never throws for protocol-level rejects (only wraps transport
     * exceptions as [Result.TransportError]). Bounded: a single PRESENT then a single
     * FILTER_UPDATE — no retry loop here (the caller decides whether to re-handover).
     */
    suspend fun presentAndDeliver(
        session: BleSession,
        token: ByteArray,
        readerPubkey: ByteArray,
        filterUpdateOpBytes: ByteArray,
    ): Result {
        // Step 3 (defence-in-depth): the phone verifies the token before presenting it,
        // so a corrupt/forged token never wastes a BLE round-trip. The reader re-checks
        // its own signature + binding authoritatively on PRESENT.
        val parsed = HandoverToken.parse(token) ?: return Result.TokenInvalid
        if (!HandoverToken.verify(readerPubkey, parsed)) return Result.TokenInvalid

        return try {
            // Step 5: present the token; reader authorizes the FILTER stream on THIS conn.
            val presentResult = session.runOperation(HandoverToken.buildPresentRequest(token))
            val presentCode = opResultCode(presentResult)
            if (presentCode != 0x00.toByte()) {
                Log.w(TAG, "PRESENT rejected code=0x%02X".format(presentCode.toInt() and 0xFF))
                return Result.PresentRejected(presentCode)
            }

            // Step 6: stream the filter. The reader (with handover_required armed) now
            // accepts INNER_FILTER_UPDATE because this connection is authorized.
            // TODO(hardware): exercise the full two-radio path end-to-end on a device —
            //  issue over NFC, connect BLE to the token's MAC, then this present+stream.
            val deliveryResult = session.runOperation(filterUpdateOpBytes, timeoutMs = 30_000)
            Result.Delivered(deliveryResult)
        } catch (e: Exception) {
            Log.e(TAG, "handover BLE half failed", e)
            Result.TransportError(e.message)
        }
    }

    /**
     * Decide, from a scan result, whether to take the handover path for a bulk filter
     * delivery: the reader must advertise BOTH bulk (BLE accepts the op) and handover
     * (it will authorize via a tap). Otherwise fall back to plain NFC / plain BLE-bulk.
     */
    fun shouldHandover(reader: ScannedReader): Boolean =
        reader.supportsBulk && reader.supportsHandover

    /** Result code byte of a generic OP_RESULT envelope (marker[0], in_reply_to[1], code[2]). */
    private fun opResultCode(result: ByteArray): Byte =
        if (result.size >= 3) result[2] else 0xFF.toByte()
}
