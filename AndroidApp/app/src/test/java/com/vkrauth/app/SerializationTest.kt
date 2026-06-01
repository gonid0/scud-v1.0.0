package com.vkrauth.app

import com.vkrauth.app.data.crypto.Domains
import com.vkrauth.app.data.crypto.Ed25519
import com.vkrauth.app.data.crypto.Serialization
import com.vkrauth.app.data.crypto.computeKeyIdFromIssuedKey
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class SerializationTest {

    private fun makeInfo(
        readerId: ByteArray = ByteArray(16) { it.toByte() },
        readerTime: Long = 1_800_000_000L,
        filterVersion: Long = 42L,
        blacklistCount: Int = 3,
        freshNonce: ByteArray = ByteArray(32) { (it + 1).toByte() },
        sessionSeq: Int = 7,
        signature: ByteArray = ByteArray(64)
    ): ByteArray {
        val buf = ByteBuffer.allocate(146).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(readerId)
        buf.putLong(readerTime)
        buf.put(0x01.toByte())
        buf.putShort(256.toShort())
        buf.putLong(filterVersion)
        buf.putLong(readerTime - 100)
        buf.putShort(blacklistCount.toShort())
        buf.put(freshNonce)
        buf.putInt(sessionSeq)
        buf.put(signature)
        return buf.array()
    }

    private fun makeIssuedKey(
        readerId: ByteArray,
        phonePub: ByteArray,
        issuedAt: Long = 1_800_000_000L,
        expiresAt: Long = 1_900_000_000L,
        permitId: ByteArray = ByteArray(16) { (it + 0x40).toByte() },
        serial: Int = 5,
        signature: ByteArray = ByteArray(64)
    ): ByteArray {
        val buf = ByteBuffer.allocate(151).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x01.toByte())
        buf.put(readerId)
        buf.put(phonePub)
        buf.putLong(issuedAt)
        buf.putLong(expiresAt)
        buf.put(permitId)
        buf.putInt(serial)
        buf.put(byteArrayOf(0, 0))
        buf.put(signature)
        return buf.array()
    }

    @Test
    fun test_parse_info() {
        val readerId = ByteArray(16) { it.toByte() }
        val nonce = ByteArray(32) { (it + 100).toByte() }
        val bytes = makeInfo(readerId = readerId, freshNonce = nonce, filterVersion = 99, blacklistCount = 5)
        val info = Serialization.parseInfo(bytes)
        assertEquals(1, info.formatVersion)
        assertArrayEquals(readerId, info.readerId)
        assertEquals(1_800_000_000L, info.readerTime)
        assertEquals(256, info.maxApduSize)
        assertEquals(99L, info.filterVersion)
        assertEquals(5, info.blacklistCount)
        assertArrayEquals(nonce, info.freshNonce)
        assertEquals(7, info.sessionSeq)
        assertEquals(82, info.signedRange.size)
    }

    @Test
    fun test_build_access_operation() {
        val (priv, pub) = Ed25519.generateKeyPair()
        val readerId = ByteArray(16) { it.toByte() }
        val issued = makeIssuedKey(readerId, pub)
        val nonce = ByteArray(32) { 0x55 }
        val time = 1_800_000_050L

        val op = Serialization.buildAccessOperationRaw(issued, nonce, time, readerId, priv)
        assertEquals(256, op.size)
        assertEquals(0x01.toByte(), op[0])
        // issued_key placement
        assertArrayEquals(issued, op.copyOfRange(1, 152))
        // used_nonce
        assertArrayEquals(nonce, op.copyOfRange(152, 184))
        // reader_time_echo LE
        val te = ByteBuffer.wrap(op, 184, 8).order(ByteOrder.LITTLE_ENDIAN).long
        assertEquals(time, te)
        // signature verifies
        val keyId = computeKeyIdFromIssuedKey(issued)
        val teBytes = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(time).array()
        val signingInput = Domains.RSP + readerId + nonce + teBytes + keyId
        val sig = op.copyOfRange(192, 256)
        assertTrue(Ed25519.verify(pub, signingInput, sig))
    }

    @Test
    fun test_build_revoke_key_operation() {
        val (priv, pub) = Ed25519.generateKeyPair()
        val readerId = ByteArray(16) { it.toByte() }
        val requester = makeIssuedKey(readerId, pub, serial = 1)
        val target = makeIssuedKey(readerId, pub, serial = 2)
        val nonce = ByteArray(32) { 0x66 }
        val time = 1_800_000_060L

        val op = Serialization.buildRevokeKeyOperationRaw(requester, target, nonce, time, readerId, priv)
        assertEquals(407, op.size)
        assertEquals(0x15.toByte(), op[0])
        assertArrayEquals(requester, op.copyOfRange(1, 152))
        assertArrayEquals(target, op.copyOfRange(152, 303))
        assertArrayEquals(nonce, op.copyOfRange(303, 335))

        val reqId = computeKeyIdFromIssuedKey(requester)
        val tgtId = computeKeyIdFromIssuedKey(target)
        val teBytes = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(time).array()
        val signingInput = Domains.REV + readerId + nonce + teBytes + reqId + tgtId
        val sig = op.copyOfRange(343, 407)
        assertTrue(Ed25519.verify(pub, signingInput, sig))
    }

    @Test
    fun test_build_time_sync_operation() {
        val grant = ByteArray(148) { it.toByte() }
        val statement = ByteArray(140) { (it + 10).toByte() }
        val op = Serialization.buildTimeSyncOperation(grant, statement)
        assertEquals(289, op.size)
        assertEquals(0x12.toByte(), op[0])
        assertArrayEquals(grant, op.copyOfRange(1, 149))
        assertArrayEquals(statement, op.copyOfRange(149, 289))
    }

    @Test
    fun test_parse_access_verdict() {
        val nextNonce = ByteArray(32) { (it + 5).toByte() }
        val buf = ByteBuffer.allocate(42).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(0x81.toByte())
        buf.put(0x00.toByte())
        buf.putLong(1_800_000_100L)
        buf.put(nextNonce)
        val verdict = Serialization.parseAccessVerdict(buf.array())
        assertEquals(0x00.toByte(), verdict.result)
        assertEquals("OK", verdict.resultName)
        assertEquals(1_800_000_100L, verdict.readerTime)
        assertArrayEquals(nextNonce, verdict.nextNonce)
    }

    @Test
    fun test_extract_next_nonce_from_different_markers() {
        // ACCESS_VERDICT 0x81
        val n1 = ByteArray(32) { 0x11 }
        val v1 = ByteBuffer.allocate(42).order(ByteOrder.LITTLE_ENDIAN).apply {
            put(0x81.toByte()); put(0x00.toByte()); putLong(0L); put(n1)
        }.array()
        assertArrayEquals(n1, Serialization.extractNextNonce(v1))

        // OP_RESULT 0x95 — 45 B
        val n2 = ByteArray(32) { 0x22 }
        val v2 = ByteArray(45)
        v2[0] = 0x95.toByte()
        System.arraycopy(n2, 0, v2, 13, 32)
        assertArrayEquals(n2, Serialization.extractNextNonce(v2))

        // FDI 0x91 — should return null (not consumed for signed ops)
        val v3 = ByteArray(241)
        v3[0] = 0x91.toByte()
        assertEquals(null, Serialization.extractNextNonce(v3))

        // BLK 0x94 — should return null
        val v4 = ByteArray(207)
        v4[0] = 0x94.toByte()
        assertEquals(null, Serialization.extractNextNonce(v4))
    }
}
