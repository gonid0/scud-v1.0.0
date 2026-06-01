package com.vkrauth.app.ble

import com.vkrauth.app.data.crypto.Domains
import com.vkrauth.app.data.crypto.Ed25519
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * NFC→BLE handover_token (shared §17.1, plan 08 §4.3).
 *
 * A courier that just passed a physical NFC ACCESS at a mains+BLE reader asks the
 * reader (over NFC, INNER_HANDOVER_ISSUE) for a short-lived reader-signed token; it
 * then connects over BLE and presents it (INNER_HANDOVER_PRESENT) to authorize a
 * filter-delivery stream on that connection — bound to the physical tap via tap_nonce.
 *
 * Wire (167 B, reader-signed, marker 0x99):
 * ```
 *   off  size  field
 *   0    1     format_version = 0x99
 *   1    16    reader_id
 *   17   32    issued_to_phone_pubkey   (phone Ed25519 identity)
 *   49   6     reader_ble_addr          (the BLE MAC the phone connects to)
 *   55   32    tap_nonce                (the fresh_nonce in force during the tap)
 *   87   8     issued_at   (LE uint64, reader clock seconds)
 *   95   8     expires_at  (LE uint64; issued_at + HANDOVER_TOKEN_TTL_S)
 *   103  64    reader_signature = sign_reader(DOMAIN_BLE, bytes[0:103])
 * ```
 * All multi-byte integers little-endian. Signed region = bytes[0:103].
 *
 * The reader signs the token and verifies its OWN signature on PRESENT — the phone
 * never forges it; the signature proves integrity/non-forgery, the binding proves it
 * is the same tap + same phone. The phone verifies the signature too (against the
 * reader pubkey it already trusts from INFO) before bothering to present it.
 */
object HandoverToken {
    const val LEN = 167
    const val SIGNED_LEN = 103
    const val MARKER: Byte = 0x99.toByte()

    /** Inner opcodes (shared §3.1) — mirror of firmware ops.h. */
    const val OP_HANDOVER_ISSUE: Byte = 0x17
    const val OP_HANDOVER_PRESENT: Byte = 0x18

    data class Parsed(
        val readerId: ByteArray,
        val phonePubkey: ByteArray,
        val readerBleAddr: ByteArray,
        val tapNonce: ByteArray,
        val issuedAt: Long,
        val expiresAt: Long,
        val signedRange: ByteArray,   // bytes[0:103]
        val signature: ByteArray      // bytes[103:167]
    )

    /**
     * Parse a 167-B token. Returns null if the length/marker is wrong (does NOT
     * verify the signature — call [verify] for that).
     */
    fun parse(blob: ByteArray): Parsed? {
        if (blob.size != LEN || blob[0] != MARKER) return null
        val buf = ByteBuffer.wrap(blob).order(ByteOrder.LITTLE_ENDIAN)
        buf.position(1)
        val readerId = ByteArray(16).also { buf.get(it) }
        val phonePubkey = ByteArray(32).also { buf.get(it) }
        val bleAddr = ByteArray(6).also { buf.get(it) }
        val tapNonce = ByteArray(32).also { buf.get(it) }
        val issuedAt = buf.long
        val expiresAt = buf.long
        return Parsed(
            readerId = readerId,
            phonePubkey = phonePubkey,
            readerBleAddr = bleAddr,
            tapNonce = tapNonce,
            issuedAt = issuedAt,
            expiresAt = expiresAt,
            signedRange = blob.copyOfRange(0, SIGNED_LEN),
            signature = blob.copyOfRange(SIGNED_LEN, LEN)
        )
    }

    /** True if the token's reader_signature over DOMAIN_BLE || bytes[0:103] verifies. */
    fun verify(readerPubkey: ByteArray, token: Parsed): Boolean =
        Ed25519.verify(readerPubkey, Domains.BLE + token.signedRange, token.signature)

    /**
     * Build a token (reader-side / test packer). The phone never builds one at
     * runtime — only the reader does — but this keeps the layout in one place and
     * lets the conformance test pack+verify byte-for-byte against the golden vector.
     */
    fun build(
        readerId: ByteArray,
        phonePubkey: ByteArray,
        readerBleAddr: ByteArray,
        tapNonce: ByteArray,
        issuedAt: Long,
        expiresAt: Long,
        readerPrivKey: ByteArray
    ): ByteArray {
        require(readerId.size == 16) { "reader_id must be 16 B" }
        require(phonePubkey.size == 32) { "phone_pubkey must be 32 B" }
        require(readerBleAddr.size == 6) { "reader_ble_addr must be 6 B" }
        require(tapNonce.size == 32) { "tap_nonce must be 32 B" }
        val signed = ByteBuffer.allocate(SIGNED_LEN).order(ByteOrder.LITTLE_ENDIAN)
        signed.put(MARKER)
        signed.put(readerId)
        signed.put(phonePubkey)
        signed.put(readerBleAddr)
        signed.put(tapNonce)
        signed.putLong(issuedAt)
        signed.putLong(expiresAt)
        val signedBytes = signed.array()
        val sig = Ed25519.sign(readerPrivKey, Domains.BLE + signedBytes)
        return signedBytes + sig
    }

    /**
     * NFC payload for INNER_HANDOVER_ISSUE: inner_opcode(1) + phone_pubkey(32) = 33 B.
     * Sent over the tap after a successful ACCESS so the reader mints the token.
     */
    fun buildIssueRequest(phonePubkey: ByteArray): ByteArray {
        require(phonePubkey.size == 32) { "phone_pubkey must be 32 B" }
        return ByteArray(1 + 32).also {
            it[0] = OP_HANDOVER_ISSUE
            System.arraycopy(phonePubkey, 0, it, 1, 32)
        }
    }

    /**
     * BLE payload for INNER_HANDOVER_PRESENT: inner_opcode(1) + token(167) = 168 B.
     */
    fun buildPresentRequest(token: ByteArray): ByteArray {
        require(token.size == LEN) { "handover_token must be $LEN B" }
        return ByteArray(1 + LEN).also {
            it[0] = OP_HANDOVER_PRESENT
            System.arraycopy(token, 0, it, 1, LEN)
        }
    }

    /** Convert the 6-B reader_ble_addr (native LE) to a Android MAC string AA:BB:.. */
    fun bleAddrToMacString(addr: ByteArray): String {
        require(addr.size == 6) { "reader_ble_addr must be 6 B" }
        // Android BluetoothDevice MAC is big-endian text; NimBLE getNative() is LE,
        // so reverse to render the human/connectable form.
        return (5 downTo 0).joinToString(":") { "%02X".format(addr[it].toInt() and 0xFF) }
    }
}
