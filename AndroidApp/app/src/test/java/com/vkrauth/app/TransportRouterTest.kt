package com.vkrauth.app

import com.vkrauth.app.ble.Transport
import com.vkrauth.app.ble.TransportRouter
import com.vkrauth.app.ble.TransportRouter.chooseTransport
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for the pure transport-routing helper (item X2, plan §3.3).
 *
 * Policy: bulk ops (FILTER_UPDATE/GET_BLACKLIST) → BLE iff the reader advertised
 * BLE_CAP_BULK, else NFC; proximity ops (ACCESS/TIME_SYNC/REVOKE/FDI/PASSAGE) →
 * always NFC regardless of caps (proximity is a security property).
 */
class TransportRouterTest {

    @Test
    fun bulkFilterUpdate_goesToBle_whenReaderSupportsBulk() {
        assertEquals(
            Transport.BLE,
            chooseTransport(TransportRouter.OP_FILTER_UPDATE, readerSupportsBulk = true)
        )
    }

    @Test
    fun bulkGetBlacklist_goesToBle_whenReaderSupportsBulk() {
        assertEquals(
            Transport.BLE,
            chooseTransport(TransportRouter.OP_GET_BLACKLIST, readerSupportsBulk = true)
        )
    }

    @Test
    fun bulkFilterUpdate_fallsBackToNfc_whenReaderLacksBulk() {
        assertEquals(
            Transport.NFC,
            chooseTransport(TransportRouter.OP_FILTER_UPDATE, readerSupportsBulk = false)
        )
    }

    @Test
    fun access_alwaysNfc_evenWithBulkCaps() {
        assertEquals(
            Transport.NFC,
            chooseTransport(TransportRouter.OP_ACCESS, readerSupportsBulk = true)
        )
        assertEquals(
            Transport.NFC,
            chooseTransport(TransportRouter.OP_ACCESS, readerSupportsBulk = false)
        )
    }

    @Test
    fun proximityOps_alwaysNfc_regardlessOfCaps() {
        val proximity = listOf(
            TransportRouter.OP_TIME_SYNC,
            TransportRouter.OP_REVOKE_KEY,
            TransportRouter.OP_FDI,
            TransportRouter.OP_PASSAGE,
        )
        for (op in proximity) {
            assertEquals("op=0x%02X with caps".format(op), Transport.NFC, chooseTransport(op, true))
            assertEquals("op=0x%02X no caps".format(op), Transport.NFC, chooseTransport(op, false))
        }
    }

    @Test
    fun unknownOpcode_isConservativelyNfc() {
        assertEquals(Transport.NFC, chooseTransport(0x7F, readerSupportsBulk = true))
    }
}
