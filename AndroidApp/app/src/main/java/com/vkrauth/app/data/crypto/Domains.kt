package com.vkrauth.app.data.crypto

object Domains {
    val KEY = "RDR-KEY-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val INF = "RDR-INF-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val RSP = "RDR-RSP-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val FLT = "RDR-FLT-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val RCP = "RDR-RCP-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val BLK = "RDR-BLK-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val FDI = "RDR-FDI-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val TGR = "RDR-TGR-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val TIM = "RDR-TIM-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    val REV = "RDR-REV-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    // shared 15 - passage_receipt signed by reader
    val PSG = "RDR-PSG-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)
    // shared 17 - BLE session_token signed by reader (optional, v1.1)
    val BLE = "RDR-BLE-v1".padEnd(16, '\u0000').toByteArray(Charsets.US_ASCII)

    init {
        listOf(KEY, INF, RSP, FLT, RCP, BLK, FDI, TGR, TIM, REV, PSG, BLE).forEach {
            check(it.size == 16) { "domain tag must be 16 B" }
        }
    }
}
