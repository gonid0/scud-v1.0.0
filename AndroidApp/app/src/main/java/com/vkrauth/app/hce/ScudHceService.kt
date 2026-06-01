package com.vkrauth.app.hce

import android.nfc.cardemulation.HostApduService
import android.os.Bundle
import android.util.Log
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class ScudHceService : HostApduService() {

    @Inject
    lateinit var tapController: TapController

    override fun processCommandApdu(apdu: ByteArray?, extras: Bundle?): ByteArray? {
        if (apdu == null) return SW_WRONG_LENGTH
        // N4: вся работа (DAO + AndroidKeyStore) уходит с NFC binder-потока в
        // корутину TapController. Возвращаем null — ответ придёт через
        // sendResponseApdu, когда обработка завершится (HostApduService API).
        tapController.handleApdu(apdu) { resp ->
            try {
                sendResponseApdu(resp)
            } catch (e: Exception) {
                Log.e(TAG, "sendResponseApdu failed", e)
            }
        }
        return null
    }

    override fun onDeactivated(reason: Int) {
        tapController.onDeactivated(reason)
    }

    companion object {
        private const val TAG = "ScudHce"

        val SW_OK = byteArrayOf(0x90.toByte(), 0x00)
        val SW_WRONG_LENGTH = byteArrayOf(0x67.toByte(), 0x00)
        val SW_FILE_NOT_FOUND = byteArrayOf(0x6A.toByte(), 0x82.toByte())
        val SW_REF_NOT_FOUND = byteArrayOf(0x6A.toByte(), 0x88.toByte())
        val SW_UNKNOWN = byteArrayOf(0x6F.toByte(), 0x00)
    }
}
