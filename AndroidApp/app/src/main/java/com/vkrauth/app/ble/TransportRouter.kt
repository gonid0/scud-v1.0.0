package com.vkrauth.app.ble

/**
 * Транспорт, по которому имеет смысл нести операцию (план §2/§3.3, item X2).
 *
 * NFC — близость ~4 см. BLE — полнофункциональный канал в рамках явно открытой
 * per-reader сессии.
 */
enum class Transport { NFC, BLE }

/**
 * Чистая (без сайд-эффектов, юнит-тестируемая в изоляции) функция выбора
 * транспорта-ПО-УМОЛЧАНИЮ по типу пакета и заявленным caps ридера (item X2).
 *
 * H1 TRANSPORT UNIFICATION — согласованная модель: ВСЕ операции могут идти по
 * ОБОИМ транспортам (BLE и NFC). Relay/wormhole-риск ЯВНО ПРИНЯТ владельцем;
 * единственная BLE-специфичная мера — Android per-reader confirm-session
 * (UX-контроль, НЕ граница ридера, §16.8). Прошивка маршрутизирует
 * FDI/TIME_SYNC/REVOKE_KEY/ACCESS на их хендлеры по ОБОИМ транспортам.
 *
 * Эта функция — лишь выбор транспорта ПО УМОЛЧАНИЮ для авто-сценария tap:
 *   - bulk: FILTER_UPDATE(0x13), большой GET_BLACKLIST(0x14) → BLE, если ридер
 *     заявил BLE_CAP_BULK; иначе fallback на NFC;
 *   - всё остальное → NFC по умолчанию.
 * Но это НЕ граница возможностей: внутри ЯВНО открытой BLE per-reader сессии
 * (см. [bleSessionCanRun]) телефон гоняет FDI/TIME_SYNC/REVOKE_KEY/ACCESS прямо
 * по BLE тем же write→await циклом BleSession.runOperation.
 *
 * Неизвестные опкоды трактуем консервативно как NFC по умолчанию (fail-safe).
 */
object TransportRouter {

    // Inner opcodes (shared §3.1) — зеркало firmware ops.h / Serialization.
    const val OP_ACCESS: Byte         = 0x01
    const val OP_FDI: Byte            = 0x11
    const val OP_TIME_SYNC: Byte      = 0x12
    const val OP_FILTER_UPDATE: Byte  = 0x13
    const val OP_GET_BLACKLIST: Byte  = 0x14
    const val OP_REVOKE_KEY: Byte     = 0x15
    const val OP_PASSAGE: Byte        = 0x16
    // T3 NFC→BLE handover (shared §17.1). ISSUE is intrinsically NFC (minted from a
    // physical tap), PRESENT is intrinsically BLE (authorizes the BLE connection) —
    // their transport is fixed by the handover itself, not chosen by chooseTransport.
    const val OP_HANDOVER_ISSUE: Byte   = 0x17
    const val OP_HANDOVER_PRESENT: Byte = 0x18

    /** Bulk-операции, которые имеет смысл уносить с NFC-критического пути на BLE. */
    private val BULK_OPCODES = setOf(OP_FILTER_UPDATE, OP_GET_BLACKLIST)

    /**
     * H1: операции, которые ЯВНО открытая BLE per-reader сессия может выполнить
     * по BLE напрямую (тем же BleSession.runOperation write→await циклом, что и
     * ACCESS). Это НЕ авто-выбор транспорта — это полнофункциональность открытого
     * BLE-канала. HANDOVER_ISSUE(NFC-only)/HANDOVER_PRESENT(BLE-only) сюда НЕ
     * входят: их транспорт фиксирован самим handover'ом.
     */
    private val BLE_SESSION_OPCODES = setOf(OP_ACCESS, OP_FDI, OP_TIME_SYNC, OP_REVOKE_KEY)

    /**
     * Транспорт ПО УМОЛЧАНИЮ для авто-сценария tap (item X2).
     *
     * @param innerOpcode прикладной опкод операции (§3.1).
     * @param readerSupportsBulk ридер заявил BLE_CAP_BULK в advertisement (§16.2).
     * @return [Transport.BLE] для bulk-операций при наличии caps, иначе [Transport.NFC].
     */
    fun chooseTransport(innerOpcode: Byte, readerSupportsBulk: Boolean): Transport =
        if (innerOpcode in BULK_OPCODES && readerSupportsBulk) Transport.BLE else Transport.NFC

    /**
     * H1: можно ли выполнить операцию [innerOpcode] внутри явно открытой BLE
     * per-reader сессии. true для ACCESS/FDI/TIME_SYNC/REVOKE_KEY и любого
     * bulk-опкода — открытая BLE-сессия это полнофункциональный канал.
     */
    fun bleSessionCanRun(innerOpcode: Byte): Boolean =
        innerOpcode in BLE_SESSION_OPCODES || innerOpcode in BULK_OPCODES
}
