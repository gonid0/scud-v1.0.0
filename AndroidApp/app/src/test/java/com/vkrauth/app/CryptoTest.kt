package com.vkrauth.app

import com.vkrauth.app.data.crypto.Blake2s
import com.vkrauth.app.data.crypto.Ed25519
import com.vkrauth.app.data.crypto.computeKeyId
import com.vkrauth.app.data.crypto.computeKeyIdFromIssuedKey
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class CryptoTest {

    @Test
    fun `Ed25519 sign and verify roundtrip with known seed`() {
        val seed = ByteArray(32) { (it + 1).toByte() }
        val pub = Ed25519.publicKeyFromPrivate(seed)
        val msg = "hello scud".toByteArray()
        val sig = Ed25519.sign(seed, msg)
        assertEquals(64, sig.size)
        assertTrue(Ed25519.verify(pub, msg, sig))
        // Tampered message should fail.
        assertFalse(Ed25519.verify(pub, "hello scud!".toByteArray(), sig))
    }

    @Test
    fun `compute_key_id is deterministic blake2s-128 over little-endian header`() {
        val readerId = ByteArray(16) { it.toByte() }
        val phonePub = ByteArray(32) { (it + 32).toByte() }
        val issuedAt = 1_700_000_000L
        val serial = 7

        val direct = Blake2s.compute(
            readerId + phonePub +
                ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(issuedAt).array() +
                ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(serial).array(),
            128
        )
        val viaFn = computeKeyId(readerId, phonePub, issuedAt, serial)
        assertEquals(16, viaFn.size)
        assertTrue(direct.contentEquals(viaFn))
    }

    @Test
    fun `computeKeyIdFromIssuedKey extracts proper header fields`() {
        val readerId = ByteArray(16) { (it + 0x10).toByte() }
        val phonePub = ByteArray(32) { (it + 0x20).toByte() }
        val issuedAt = 1_800_000_000L
        val expiresAt = 1_900_000_000L
        val permitId = ByteArray(16) { (it + 0x40).toByte() }
        val serial = 42
        val payload = byteArrayOf(0, 0)
        val sig = ByteArray(64)

        val buf = ByteBuffer.allocate(151).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(readerId)
        buf.put(phonePub)
        buf.putLong(issuedAt)
        buf.putLong(expiresAt)
        buf.put(permitId)
        buf.putInt(serial)
        buf.put(payload)
        buf.put(sig)
        val issued = buf.array()

        val keyId = computeKeyIdFromIssuedKey(issued)
        val expected = computeKeyId(readerId, phonePub, issuedAt, serial)
        assertTrue(keyId.contentEquals(expected))
    }
}
