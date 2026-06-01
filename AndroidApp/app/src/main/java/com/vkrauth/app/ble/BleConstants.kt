package com.vkrauth.app.ble

import java.util.UUID

/**
 * UUIDs and constants for the SCUD BLE channel (shared §16).
 *
 * Если меняется UUID — обязательно правится одновременно firmware/src/ble/ble_channel.cpp
 * (это часть единого контракта shared/00).
 */
object BleConstants {
    val SERVICE_UUID: UUID            = UUID.fromString("5c0da001-5c0d-4d11-8001-000000000000")
    val CHR_INFO_NOTIFY: UUID         = UUID.fromString("5c0da001-5c0d-4d11-8001-000000000001")
    val CHR_OP_WRITE: UUID            = UUID.fromString("5c0da001-5c0d-4d11-8001-000000000002")
    val CHR_RESULT_NOTIFY: UUID       = UUID.fromString("5c0da001-5c0d-4d11-8001-000000000003")
    val CHR_CONTROL: UUID             = UUID.fromString("5c0da001-5c0d-4d11-8001-000000000004")

    // Стандартный CCC дескриптор (Client Characteristic Configuration).
    val CCC_DESCRIPTOR: UUID          = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val MANUFACTURER_ID: Int    = 0xC0DE

    // Advertisement capability flags (manuf-data byte[8], §16.2, X2). Mirror of
    // firmware config.h BLE_CAP_*. Unsigned hint for transport routing only.
    const val BLE_CAP_BULK: Int       = 0x01   // bit0: reader accepts bulk ops over BLE
    const val BLE_CAP_HANDOVER: Int   = 0x02   // bit1: reader supports NFC→BLE handover (reserved)

    // Framing flags (shared §16.5).
    const val FLAG_LAST: Byte         = 0x01
    const val FLAG_HAS_TOTAL: Byte    = 0x02

    // CONTROL sub-commands.
    const val CTL_RESET: Byte         = 0x01
    const val CTL_END: Byte           = 0x02

    // Negotiated MTU target. iOS/Android обычно дают 247.
    const val MTU_REQUEST: Int        = 247

    // Сессия на ридере живёт ~30 сек без активности — здесь UI-таймер чуть короче.
    const val SESSION_TIMEOUT_MS: Long = 25_000
}
