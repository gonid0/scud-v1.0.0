package com.vkrauth.app.data.crypto

import org.bouncycastle.crypto.digests.Blake2sDigest
import java.nio.ByteBuffer
import java.nio.ByteOrder

object Blake2s {
    fun compute(data: ByteArray, digestBits: Int = 128): ByteArray {
        val digest = Blake2sDigest(digestBits)
        digest.update(data, 0, data.size)
        val out = ByteArray(digestBits / 8)
        digest.doFinal(out, 0)
        return out
    }
}

fun computeKeyId(
    readerId: ByteArray,
    phonePubkey: ByteArray,
    issuedAtSec: Long,
    serial: Int
): ByteArray {
    require(readerId.size == 16) { "reader_id must be 16 B" }
    require(phonePubkey.size == 32) { "phone_pubkey must be 32 B" }
    val buf = ByteBuffer.allocate(16 + 32 + 8 + 4).order(ByteOrder.LITTLE_ENDIAN)
    buf.put(readerId)
    buf.put(phonePubkey)
    buf.putLong(issuedAtSec)
    buf.putInt(serial)
    return Blake2s.compute(buf.array(), 128)
}

// Extract header fields from a 151 B issued_key and compute key_id.
// Layout (§5.1): reader_id@1..17, phone_pubkey@17..49, issued_at@49..57, serial@81..85.
fun computeKeyIdFromIssuedKey(issuedKey: ByteArray): ByteArray {
    require(issuedKey.size == 151) { "issued_key must be 151 B" }
    val readerId = issuedKey.copyOfRange(1, 17)
    val phonePubkey = issuedKey.copyOfRange(17, 49)
    val issuedAt = ByteBuffer.wrap(issuedKey, 49, 8).order(ByteOrder.LITTLE_ENDIAN).long
    val serial = ByteBuffer.wrap(issuedKey, 81, 4).order(ByteOrder.LITTLE_ENDIAN).int
    return computeKeyId(readerId, phonePubkey, issuedAt, serial)
}
