package com.vkrauth.app.data.crypto

import java.nio.ByteBuffer
import java.nio.ByteOrder

object Serialization {

    data class InfoStruct(
        val formatVersion: Int,
        val readerId: ByteArray,
        val readerTime: Long,
        val protocolVersion: Int,
        val maxApduSize: Int,
        val filterVersion: Long,
        val filterDeliveredAt: Long,
        val blacklistCount: Int,
        val freshNonce: ByteArray,
        val sessionSeq: Int,
        val readerSignature: ByteArray,
        val signedRange: ByteArray
    )

    data class AccessVerdict(
        val result: Byte,
        val readerTime: Long,
        val nextNonce: ByteArray,
        /**
         * H3 fold-in: the 192-B reader-signed passage_receipt body, present IFF
         * result==RES_OK (response was 234 B). null on every deny (42-B response).
         * This is the SAME body the phone uploads as the "passage_receipt" report
         * (formerly fetched via the separate GET_PASSAGE_RECEIPT op). Bytes [0:42]
         * stay byte-identical to the legacy 42-B verdict; the receipt occupies the
         * appended tail response[42:234].
         */
        val passageReceipt: ByteArray? = null
    ) {
        val resultName: String
            get() = when (result) {
                0x00.toByte() -> "OK"
                0x20.toByte() -> "EXPIRED"
                0x21.toByte() -> "REVOKED_BLACKLIST"
                0x22.toByte() -> "REVOKED_FILTER"
                0x01.toByte() -> "BAD_SIGNATURE"
                0x02.toByte() -> "BAD_FORMAT"
                0x03.toByte() -> "BAD_NONCE"
                0x07.toByte() -> "WRONG_READER"
                else -> "ERR_%02X".format(result.toInt() and 0xFF)
            }
    }

    fun parseInfo(bytes: ByteArray): InfoStruct {
        require(bytes.size == 146) { "INFO must be 146 B, got ${bytes.size}" }
        val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val formatVersion = buf.get().toInt() and 0xFF
        require(formatVersion == 1) { "unsupported INFO format_version $formatVersion" }
        val readerId = ByteArray(16).also { buf.get(it) }
        val readerTime = buf.long
        val protocolVersion = buf.get().toInt() and 0xFF
        val maxApduSize = buf.short.toInt() and 0xFFFF
        val filterVersion = buf.long
        val filterDeliveredAt = buf.long
        val blacklistCount = buf.short.toInt() and 0xFFFF
        val freshNonce = ByteArray(32).also { buf.get(it) }
        val sessionSeq = buf.int
        val signature = ByteArray(64).also { buf.get(it) }
        return InfoStruct(
            formatVersion, readerId, readerTime, protocolVersion, maxApduSize,
            filterVersion, filterDeliveredAt, blacklistCount,
            freshNonce, sessionSeq, signature,
            signedRange = bytes.copyOfRange(0, 82)
        )
    }

    /**
     * H3 PINNED WIRE LAYOUT (identical across firmware + android + docs + conformance):
     *   [0]      marker = 0x81 (MARK_ACCESS_VERDICT)
     *   [1]      result  (0x00 = RES_OK, otherwise a deny code)
     *   [2:10]   reader_time (uint64 LE)
     *   [10:42]  next_nonce (32 B)
     *   [42:234] passage_receipt (192 B)  <-- PRESENT IFF result==RES_OK; ABSENT on deny
     * Total length: 234 B on RES_OK; 42 B on any deny.
     * Bytes [0:42] are the legacy build_verdict output, byte-identical and unchanged;
     * next_nonce STAYS at [10:42] in BOTH cases.
     */
    fun parseAccessVerdict(bytes: ByteArray): AccessVerdict {
        require(bytes.size == 42 || bytes.size == 234) { "verdict must be 42 B (deny) or 234 B (OK)" }
        require(bytes[0] == 0x81.toByte()) { "verdict marker mismatch" }
        val buf = ByteBuffer.wrap(bytes, 1, 41).order(ByteOrder.LITTLE_ENDIAN)
        val result = buf.get()
        val readerTime = buf.long
        val nextNonce = ByteArray(32).also { buf.get(it) }
        // On RES_OK the 192-B passage_receipt body is appended at [42:234].
        val passageReceipt =
            if (bytes.size == 234 && result == 0x00.toByte()) bytes.copyOfRange(42, 234) else null
        return AccessVerdict(result, readerTime, nextNonce, passageReceipt)
    }

    fun buildAccessOperation(
        issuedKey: ByteArray,
        usedNonce: ByteArray,
        readerTimeEcho: Long,
        readerId: ByteArray,
        keyManager: KeyManager
    ): ByteArray {
        require(issuedKey.size == 151) { "issued_key must be 151 B" }
        require(usedNonce.size == 32) { "nonce must be 32 B" }
        require(readerId.size == 16) { "reader_id must be 16 B" }

        val keyId = computeKeyIdFromIssuedKey(issuedKey)
        val timeEcho = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array()
        val signingInput = readerId + usedNonce + timeEcho + keyId
        val signature = keyManager.sign(Domains.RSP + signingInput)

        val buf = ByteBuffer.allocate(256).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(issuedKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }

    // Variant that accepts a raw private key — used by tests.
    fun buildAccessOperationRaw(
        issuedKey: ByteArray,
        usedNonce: ByteArray,
        readerTimeEcho: Long,
        readerId: ByteArray,
        privKey: ByteArray
    ): ByteArray {
        require(issuedKey.size == 151)
        require(usedNonce.size == 32)
        require(readerId.size == 16)
        val keyId = computeKeyIdFromIssuedKey(issuedKey)
        val timeEcho = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array()
        val signingInput = readerId + usedNonce + timeEcho + keyId
        val signature = Ed25519.sign(privKey, Domains.RSP + signingInput)

        val buf = ByteBuffer.allocate(256).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(issuedKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }

    fun buildTimeSyncOperation(
        grantBytes: ByteArray,
        statementBytes: ByteArray
    ): ByteArray {
        require(grantBytes.size == 148) { "grant must be 148 B" }
        require(statementBytes.size == 140) { "statement must be 140 B" }
        val buf = ByteBuffer.allocate(289)
        buf.put(0x12.toByte())
        buf.put(grantBytes)
        buf.put(statementBytes)
        return buf.array()
    }

    fun buildTimeSyncStatement(
        readerId: ByteArray,
        authorityId: ByteArray,
        newTime: Long,
        usedNonce: ByteArray,
        kind: Byte,
        keyManager: KeyManager
    ): ByteArray {
        require(readerId.size == 16)
        require(authorityId.size == 16)
        require(usedNonce.size == 32)

        val body = ByteBuffer.allocate(76).order(ByteOrder.LITTLE_ENDIAN)
        body.put(0x01.toByte())
        body.put(readerId)
        body.put(authorityId)
        body.putLong(newTime)
        body.put(usedNonce)
        body.put(kind)
        body.put(0.toByte())
        body.put(0.toByte())
        val bodyBytes = body.array()

        val signature = keyManager.sign(Domains.TIM + bodyBytes)

        val full = ByteBuffer.allocate(140)
        full.put(bodyBytes)
        full.put(signature)
        return full.array()
    }

    fun buildRevokeKeyOperation(
        requesterKey: ByteArray,
        targetKey: ByteArray,
        usedNonce: ByteArray,
        readerTimeEcho: Long,
        readerIdBytes: ByteArray,
        signer: KeyManager
    ): ByteArray {
        require(requesterKey.size == 151)
        require(targetKey.size == 151)
        require(usedNonce.size == 32)
        require(readerIdBytes.size == 16)

        val requesterKeyId = computeKeyIdFromIssuedKey(requesterKey)
        val targetKeyId = computeKeyIdFromIssuedKey(targetKey)

        val timeEcho = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array()
        val signingInput = readerIdBytes + usedNonce + timeEcho + requesterKeyId + targetKeyId
        val signature = signer.sign(Domains.REV + signingInput)

        val buf = ByteBuffer.allocate(407).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x15.toByte())
        buf.put(requesterKey)
        buf.put(targetKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }

    fun buildRevokeKeyOperationRaw(
        requesterKey: ByteArray,
        targetKey: ByteArray,
        usedNonce: ByteArray,
        readerTimeEcho: Long,
        readerIdBytes: ByteArray,
        privKey: ByteArray
    ): ByteArray {
        require(requesterKey.size == 151)
        require(targetKey.size == 151)
        val requesterKeyId = computeKeyIdFromIssuedKey(requesterKey)
        val targetKeyId = computeKeyIdFromIssuedKey(targetKey)
        val timeEcho = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(readerTimeEcho).array()
        val signingInput = readerIdBytes + usedNonce + timeEcho + requesterKeyId + targetKeyId
        val signature = Ed25519.sign(privKey, Domains.REV + signingInput)

        val buf = ByteBuffer.allocate(407).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x15.toByte())
        buf.put(requesterKey)
        buf.put(targetKey)
        buf.put(usedNonce)
        buf.putLong(readerTimeEcho)
        buf.put(signature)
        return buf.array()
    }

    fun buildFilterUpdateOperation(
        courierIdHex: String,
        filterPackageBytes: ByteArray
    ): ByteArray {
        val courierId = hexToBytes(courierIdHex)
        require(courierId.size == 16) { "courier_id must be 16 B" }
        val buf = ByteBuffer.allocate(1 + 16 + filterPackageBytes.size)
        buf.put(0x13.toByte())
        buf.put(courierId)
        buf.put(filterPackageBytes)
        return buf.array()
    }

    // Nonce rotation: shared §9 — the ring on reader consumes nonce only for
    // signed ops (ACCESS/TIME_SYNC/REVOKE_KEY) and rotates fresh via
    // FILTER_UPDATE result as well. FDI (0x91) and BLK (0x94) embed next_nonce
    // on wire but we MUST NOT rotate phone-side from them — doing so would
    // desync with the reader's ring.
    fun extractNextNonce(resultBytes: ByteArray): ByteArray? {
        if (resultBytes.isEmpty()) return null
        return when (resultBytes[0]) {
            0x81.toByte() -> {
                if (resultBytes.size >= 42) resultBytes.copyOfRange(10, 42) else null
            }
            0x92.toByte(), 0x93.toByte(), 0x95.toByte() -> {
                if (resultBytes.size >= 32) resultBytes.copyOfRange(resultBytes.size - 32, resultBytes.size) else null
            }
            // NOTE: 0x91 FDI and 0x94 BLK carry a next_nonce on the wire but
            // phone must NOT consume it — return null to avoid desync with
            // reader's nonce ring (see shared §9 and android-spec §13).
            else -> null
        }
    }

    fun opResultSucceeded(resultBytes: ByteArray): Boolean {
        if (resultBytes.size < 3) return false
        val marker = resultBytes[0]
        // Generic OP_RESULT markers: 0x92/0x93/0x95. Result byte at offset 2.
        return when (marker) {
            0x92.toByte(), 0x93.toByte(), 0x95.toByte() -> resultBytes[2] == 0x00.toByte()
            0x81.toByte() -> resultBytes.size >= 2 && resultBytes[1] == 0x00.toByte()
            else -> false
        }
    }

    // OP_RESULT for FILTER_UPDATE: ext = delivery_receipt(112) || next_nonce(32).
    // Full layout: 13 B header + 144 B ext = 157 B. Receipt lives at offset 13..125.
    fun extractReceiptFromFilterResult(resultBytes: ByteArray): ByteArray {
        require(resultBytes.size >= 157) { "FILTER_UPDATE result too short" }
        require(resultBytes[0] == 0x93.toByte()) { "not a FILTER_UPDATE OP_RESULT" }
        return resultBytes.copyOfRange(13, 13 + 112)
    }

    fun extractReaderIdFromKey(issuedKey: ByteArray): ByteArray {
        require(issuedKey.size == 151)
        return issuedKey.copyOfRange(1, 17)
    }

    fun extractAuthorityIdFromGrant(grantBytes: ByteArray): ByteArray {
        require(grantBytes.size == 148)
        // authority_id @ offset 49, 16 B (§5.11)
        return grantBytes.copyOfRange(49, 65)
    }

    fun hexToBytes(hex: String): ByteArray {
        val clean = hex.replace("-", "").replace(" ", "")
        require(clean.length % 2 == 0) { "bad hex length" }
        return ByteArray(clean.length / 2) { i ->
            ((Character.digit(clean[i * 2], 16) shl 4) or Character.digit(clean[i * 2 + 1], 16)).toByte()
        }
    }
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
