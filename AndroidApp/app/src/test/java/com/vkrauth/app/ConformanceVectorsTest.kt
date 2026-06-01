package com.vkrauth.app

import com.vkrauth.app.ble.BleFraming
import com.vkrauth.app.ble.ReassemblyBuf
import com.vkrauth.app.data.crypto.Blake2s
import com.vkrauth.app.data.crypto.Domains
import com.vkrauth.app.data.crypto.Ed25519
import com.vkrauth.app.data.crypto.computeKeyId
import com.vkrauth.app.data.crypto.computeKeyIdFromIssuedKey
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Cross-implementation conformance: the Android crypto MUST reproduce the same
 * golden vectors as the Backend reference (docs/test_vectors/protocol_v1.json).
 * One bit of divergence breaks Ed25519 verify against a real reader. Android is
 * a data-mule, so it implements domains/key_id/blake2s/ed25519 (not bloom/murmur).
 */
class ConformanceVectorsTest {

    private val vectors by lazy {
        // Canonical corpus lives at repo docs/test_vectors/protocol_v1.json.
        // Walk up from the test working dir to find it (single source, no copy).
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        var found: File? = null
        while (dir != null) {
            val cand = File(dir, "docs/test_vectors/protocol_v1.json")
            if (cand.exists()) { found = cand; break }
            dir = dir.parentFile
        }
        requireNotNull(found) {
            "protocol_v1.json not found from ${System.getProperty("user.dir")}"
        }
        Json.parseToJsonElement(found.readText()).jsonObject
    }

    private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
    private fun String.unhex(): ByteArray = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private val domainByLabel: Map<String, ByteArray> = mapOf(
        "RDR-KEY-v1" to Domains.KEY, "RDR-INF-v1" to Domains.INF, "RDR-RSP-v1" to Domains.RSP,
        "RDR-FLT-v1" to Domains.FLT, "RDR-RCP-v1" to Domains.RCP, "RDR-BLK-v1" to Domains.BLK,
        "RDR-FDI-v1" to Domains.FDI, "RDR-TGR-v1" to Domains.TGR, "RDR-TIM-v1" to Domains.TIM,
        "RDR-REV-v1" to Domains.REV, "RDR-PSG-v1" to Domains.PSG, "RDR-BLE-v1" to Domains.BLE,
    )

    @Test
    fun domains() {
        val expected = vectors["domains"]!!.jsonObject
        assertEquals(expected.keys, domainByLabel.keys)
        for ((label, tag) in domainByLabel) {
            assertEquals("domain $label", expected[label]!!.jsonPrimitive.content, tag.hex())
        }
    }

    @Test
    fun keyId() {
        for (v in vectors["key_id"]!!.jsonArray) {
            val o = v.jsonObject
            val got = computeKeyId(
                o["reader_id_hex"]!!.jsonPrimitive.content.unhex(),
                o["phone_pubkey_hex"]!!.jsonPrimitive.content.unhex(),
                o["issued_at"]!!.jsonPrimitive.long,
                o["serial"]!!.jsonPrimitive.int,
            ).hex()
            assertEquals(o["expected_hex"]!!.jsonPrimitive.content, got)
        }
    }

    @Test
    fun blake2s128() {
        for (v in vectors["blake2s_128"]!!.jsonArray) {
            val o = v.jsonObject
            val got = Blake2s.compute(o["input_hex"]!!.jsonPrimitive.content.unhex(), 128).hex()
            assertEquals(o["expected_hex"]!!.jsonPrimitive.content, got)
        }
    }

    @Test
    fun ed25519DomainSeparated() {
        val domains = vectors["domains"]!!.jsonObject
        for (v in vectors["ed25519"]!!.jsonArray) {
            val o = v.jsonObject
            val seed = o["seed_hex"]!!.jsonPrimitive.content.unhex()
            val pub = Ed25519.publicKeyFromPrivate(seed)
            assertEquals(o["pubkey_hex"]!!.jsonPrimitive.content, pub.hex())
            val tag = domains[o["domain"]!!.jsonPrimitive.content]!!.jsonPrimitive.content.unhex()
            val payload = o["payload_hex"]!!.jsonPrimitive.content.unhex()
            val sig = Ed25519.sign(seed, tag + payload)
            assertEquals(o["signature_hex"]!!.jsonPrimitive.content, sig.hex())
            assertTrue(Ed25519.verify(pub, tag + payload, sig))
        }
    }

    @Test
    fun issuedKeySerialization() {
        for (v in vectors["issued_key"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(151, blob.size)
            // key_id parsed from the header offsets (reader@1, phone@17, issued@49, serial@81)
            assertEquals(o["key_id_hex"]!!.jsonPrimitive.content, computeKeyIdFromIssuedKey(blob).hex())
            // scalar fields at exact offsets
            val expires = ByteBuffer.wrap(blob, 57, 8).order(ByteOrder.LITTLE_ENDIAN).long
            assertEquals(o["expires_at"]!!.jsonPrimitive.long, expires)
            assertEquals(o["permit_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(65, 81).hex())
            // server signature over DOMAIN_KEY || header(87)
            val pub = o["server_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.KEY + blob.copyOfRange(0, 87), blob.copyOfRange(87, 151)))
        }
    }

    @Test
    fun timeGrantSerialization() {
        for (v in vectors["time_grant"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(148, blob.size)
            assertEquals(o["authority_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(49, 65).hex())
            assertEquals(o["kind"]!!.jsonPrimitive.int, blob[81].toInt() and 0xFF)
            // server signature over DOMAIN_TGR || header(84)
            val pub = o["server_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.TGR + blob.copyOfRange(0, 84), blob.copyOfRange(84, 148)))
        }
    }

    @Test
    fun passageReceiptSerialization() {
        for (v in vectors["passage_receipt"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(192, blob.size)
            assertEquals(0x01.toByte(), blob[0])
            assertEquals(o["reader_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(1, 17).hex())
            assertEquals(o["receipt_nonce_hex"]!!.jsonPrimitive.content, blob.copyOfRange(17, 33).hex())
            assertEquals(o["key_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(33, 49).hex())
            assertEquals(o["passed_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 49, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["direction"]!!.jsonPrimitive.int, blob[57].toInt() and 0xFF)
            assertEquals(o["verdict"]!!.jsonPrimitive.int, blob[58].toInt() and 0xFF)
            assertEquals(o["session_seq"]!!.jsonPrimitive.int, ByteBuffer.wrap(blob, 59, 4).order(ByteOrder.LITTLE_ENDIAN).int)
            assertEquals(o["flags"]!!.jsonPrimitive.int, blob[63].toInt() and 0xFF)
            assertEquals(o["phone_pubkey_hex"]!!.jsonPrimitive.content, blob.copyOfRange(64, 96).hex())
            assertEquals(o["permit_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(96, 112).hex())
            assertEquals("00000000", blob.copyOfRange(124, 128).hex())  // padding zeroed
            // reader signature over DOMAIN_PSG || bytes[0:128]
            val pub = o["reader_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.PSG + blob.copyOfRange(0, 128), blob.copyOfRange(128, 192)))
        }
    }

    @Test
    fun infoSerialization() {
        for (v in vectors["info"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(146, blob.size)
            assertEquals(0x01.toByte(), blob[0])
            assertEquals(o["reader_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(1, 17).hex())
            assertEquals(o["reader_time"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 17, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["protocol_version"]!!.jsonPrimitive.int, blob[25].toInt() and 0xFF)
            assertEquals(o["max_apdu_size"]!!.jsonPrimitive.int, ByteBuffer.wrap(blob, 26, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF)
            assertEquals(o["filter_version"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 28, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["filter_delivered_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 36, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["blacklist_count"]!!.jsonPrimitive.int, ByteBuffer.wrap(blob, 44, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF)
            assertEquals(o["fresh_nonce_hex"]!!.jsonPrimitive.content, blob.copyOfRange(46, 78).hex())
            assertEquals(o["session_seq"]!!.jsonPrimitive.int, ByteBuffer.wrap(blob, 78, 4).order(ByteOrder.LITTLE_ENDIAN).int)
            // reader signature over DOMAIN_INF || header(82)
            val pub = o["reader_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.INF + blob.copyOfRange(0, 82), blob.copyOfRange(82, 146)))
        }
    }

    @Test
    fun deliveryReceiptSerialization() {
        for (v in vectors["delivery_receipt"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(112, blob.size)
            assertEquals(o["reader_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(0, 16).hex())
            assertEquals(o["applied_filter_version"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 16, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["applied_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 24, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["courier_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(32, 48).hex())
            // reader signature over DOMAIN_RCP || bytes[0:48]
            val pub = o["reader_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.RCP + blob.copyOfRange(0, 48), blob.copyOfRange(48, 112)))
        }
    }

    @Test
    fun fdiSerialization() {
        for (v in vectors["fdi"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(241, blob.size)
            assertEquals(0x91.toByte(), blob[0])
            assertEquals(o["reader_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(1, 17).hex())
            assertEquals(o["filter_version"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 17, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["filter_delivered_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 25, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["encrypted_courier_blob_hex"]!!.jsonPrimitive.content, blob.copyOfRange(33, 137).hex())
            assertEquals(o["reader_time_now"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 137, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["next_nonce_hex"]!!.jsonPrimitive.content, blob.copyOfRange(209, 241).hex())
            // reader signature over DOMAIN_FDI || bytes[0:145]; next_nonce@209 is NOT signed
            val pub = o["reader_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.FDI + blob.copyOfRange(0, 145), blob.copyOfRange(145, 209)))
        }
    }

    @Test
    fun handoverTokenSerialization() {
        // NFC→BLE handover_token (shared §17.1, plan 08 §4.3). Android both builds
        // (HandoverToken.build) and parses/verifies (HandoverToken.parse). Here we
        // check the parse offsets + reader signature over DOMAIN_BLE || bytes[0:103].
        for (v in vectors["handover_token"]!!.jsonArray) {
            val o = v.jsonObject
            val blob = o["expected_hex"]!!.jsonPrimitive.content.unhex()
            assertEquals(167, blob.size)
            assertEquals(0x99.toByte(), blob[0])
            assertEquals(o["reader_id_hex"]!!.jsonPrimitive.content, blob.copyOfRange(1, 17).hex())
            assertEquals(o["issued_to_phone_pubkey_hex"]!!.jsonPrimitive.content, blob.copyOfRange(17, 49).hex())
            assertEquals(o["reader_ble_addr_hex"]!!.jsonPrimitive.content, blob.copyOfRange(49, 55).hex())
            assertEquals(o["tap_nonce_hex"]!!.jsonPrimitive.content, blob.copyOfRange(55, 87).hex())
            assertEquals(o["issued_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 87, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            assertEquals(o["expires_at"]!!.jsonPrimitive.long, ByteBuffer.wrap(blob, 95, 8).order(ByteOrder.LITTLE_ENDIAN).long)
            // reader signature over DOMAIN_BLE || bytes[0:103]
            val pub = o["reader_pubkey_hex"]!!.jsonPrimitive.content.unhex()
            assertTrue(Ed25519.verify(pub, Domains.BLE + blob.copyOfRange(0, 103), blob.copyOfRange(103, 167)))
        }
    }

    @Test
    fun bleFramingProduce() {
        // The real BleFraming.frame (used by BleSession.writeChunked) must reproduce
        // the exact golden PDU sequence — same chunk boundaries + headers as firmware.
        for (v in vectors["ble_framing"]!!.jsonArray) {
            val o = v.jsonObject
            val payload = o["payload_hex"]!!.jsonPrimitive.content.unhex()
            val maxPdu = o["max_pdu"]!!.jsonPrimitive.int
            val expected = o["frames_hex"]!!.jsonArray.map { it.jsonPrimitive.content }
            val got = BleFraming.frame(payload, maxPdu).map { it.hex() }
            assertEquals("frames for ${o["label"]!!.jsonPrimitive.content}", expected, got)
        }
    }

    @Test
    fun bleFramingReassemble() {
        // The real ReassemblyBuf (used by BleSession.handleNotify) must reassemble
        // the golden frames back to the payload, and stay incomplete until LAST.
        for (v in vectors["ble_framing"]!!.jsonArray) {
            val o = v.jsonObject
            val frames = o["frames_hex"]!!.jsonArray.map { it.jsonPrimitive.content.unhex() }
            val buf = ReassemblyBuf()
            var completed: ByteArray? = null
            for ((i, f) in frames.withIndex()) {
                val r = buf.feed(f)
                if (i < frames.size - 1) {
                    assertEquals("partial frame #$i must not complete", null, r)
                } else {
                    completed = r
                }
            }
            assertEquals(o["payload_hex"]!!.jsonPrimitive.content, completed?.hex())
        }
    }

    @Test
    fun apduFraming() {
        // NFC/APDU transport framing (§3.2, §4): the data bodies firmware
        // transfer.cpp and Android TapController exchange, at exact LE offsets.
        // Reconciled byte-identical with firmware; same golden corpus both check.
        val af = vectors["apdu_framing"]!!.jsonObject
        fun obj(k: String) = af[k]!!.jsonObject
        fun JsonObject.i(k: String) = this[k]!!.jsonPrimitive.int
        fun JsonObject.h(k: String) = this[k]!!.jsonPrimitive.content
        fun ByteArray.u16(off: Int) = ByteBuffer.wrap(this, off, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
        fun ByteArray.u32(off: Int) = ByteBuffer.wrap(this, off, 4).order(ByteOrder.LITTLE_ENDIAN).int

        obj("op_chunked").let { v ->                 // [0x02][inner][msg_id4][total4][fcl2][chunk]
            val b = v.h("data_hex").unhex()
            assertEquals(0x02, b[0].toInt() and 0xFF)
            assertEquals(v.i("inner_opcode"), b[1].toInt() and 0xFF)
            assertEquals(v.i("msg_id"), b.u32(2))
            assertEquals(v.i("total_len"), b.u32(6))
            assertEquals(v.i("first_chunk_len"), b.u16(10))
            assertEquals(v.h("first_chunk_hex"), b.copyOfRange(12, b.size).hex())
        }
        obj("op_single").let { v ->                  // [0x01][inner][op_len2][op_bytes]
            val b = v.h("data_hex").unhex()
            assertEquals(0x01, b[0].toInt() and 0xFF)
            assertEquals(v.i("inner_opcode"), b[1].toInt() and 0xFF)
            assertEquals(v.i("op_len"), b.u16(2))
            assertEquals(v.h("op_bytes_hex"), b.copyOfRange(4, b.size).hex())
        }
        obj("read_chunk_cmd").let { v ->             // [msg_id4][offset4][max_chunk2] (+ APDU wrapper)
            val b = v.h("data_hex").unhex()
            assertEquals(v.i("msg_id"), b.u32(0))
            assertEquals(v.i("offset"), b.u32(4))
            assertEquals(v.i("max_chunk_len"), b.u16(8))
            val ap = v.h("apdu_hex").unhex()
            assertEquals("00c300000a", ap.copyOfRange(0, 5).hex())
            assertEquals(b.hex(), ap.copyOfRange(5, 15).hex())
            assertEquals(0x00, ap[15].toInt() and 0xFF)
        }
        obj("read_chunk_resp").let { v ->            // [chunk_len2][flags1][chunk]
            val b = v.h("data_hex").unhex()
            assertEquals(v.i("chunk_len"), b.u16(0))
            assertEquals(v.i("flags"), b[2].toInt() and 0xFF)
            assertEquals(1, b[2].toInt() and 0x01)   // LAST
            assertEquals(v.h("chunk_hex"), b.copyOfRange(3, b.size).hex())
        }
        obj("push_chunk_cmd").let { v ->             // [msg_id4][offset4][total4][flags1][chunk_len2][chunk]
            val b = v.h("data_hex").unhex()
            assertEquals(v.i("msg_id"), b.u32(0))
            assertEquals(v.i("offset"), b.u32(4))
            assertEquals(v.i("total"), b.u32(8))
            assertEquals(v.i("flags"), b[12].toInt() and 0xFF)
            assertEquals(v.i("chunk_len"), b.u16(13))
            assertEquals(v.h("chunk_hex"), b.copyOfRange(15, b.size).hex())
            val ap = v.h("apdu_hex").unhex()
            assertEquals("00c40000", ap.copyOfRange(0, 4).hex())
            assertEquals(b.size, ap[4].toInt() and 0xFF)
        }
        obj("reference").let { v ->                  // [FF FF][msg_id4]
            val b = v.h("data_hex").unhex()
            assertEquals(0xFF, b[0].toInt() and 0xFF)
            assertEquals(0xFF, b[1].toInt() and 0xFF)
            assertEquals(v.i("msg_id"), b.u32(2))
        }
    }
}
