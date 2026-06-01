package com.vkrauth.app.ble

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * BLE chunked framing (shared §16.5) — pure produce side, extracted from
 * [BleSession.writeChunked] so it is unit-testable against the golden vectors
 * (docs/test_vectors/protocol_v1.json -> "ble_framing"). Mirrors the firmware
 * `ble_frame_message` byte-for-byte.
 *
 * PDU = [seq 1B] [flags 1B] [total_len 4B LE on the first PDU] [chunk].
 *   flags bit0 = LAST, bit1 = HAS_TOTAL (only seq==0 carries total_len).
 */
object BleFraming {
    /** Split [data] into PDUs of at most [maxPdu] bytes. Empty data -> no PDUs. */
    fun frame(data: ByteArray, maxPdu: Int): List<ByteArray> {
        val frames = ArrayList<ByteArray>()
        var sent = 0
        var seq = 0
        while (sent < data.size) {
            val isFirst = seq == 0
            val header = if (isFirst) 6 else 2
            val canSend = (maxPdu - header).coerceAtLeast(1)
            val thisChunk = (data.size - sent).coerceAtMost(canSend)
            val isLast = sent + thisChunk == data.size

            var flags = 0
            if (isLast) flags = flags or BleConstants.FLAG_LAST.toInt()
            if (isFirst) flags = flags or BleConstants.FLAG_HAS_TOTAL.toInt()

            val pdu = ByteArray(header + thisChunk)
            pdu[0] = (seq and 0xFF).toByte()
            pdu[1] = flags.toByte()
            if (isFirst) {
                ByteBuffer.wrap(pdu, 2, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(data.size)
            }
            System.arraycopy(data, sent, pdu, header, thisChunk)

            frames.add(pdu)
            sent += thisChunk
            seq++
        }
        return frames
    }
}
