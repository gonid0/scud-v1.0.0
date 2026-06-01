// BLE channel — параллельный NFC-тапу транспорт операций (shared §16).
//
// Архитектура:
//   reader = BLE peripheral + GATT server, advertising постоянно.
//   phone  = BLE central, сканит, видит активные SCUD-устройства, кликает на
//            одно — устанавливает GATT-соединение, обменивается операциями
//            (ACCESS / TIME_SYNC / FILTER_UPDATE / …). The passage_receipt rides
//            the ACCESS_VERDICT tail (H3 fold-in), so there is no separate op.
//
// Semantics of the operations is **identical** to NFC: те же inner_opcodes,
// те же байтовые форматы, тот же crypto. Меняется только wire — куда они
// приезжают: APDU vs GATT chunked frames.
//
// Жизненный цикл:
//   ble_init()         — в setup(). Поднимает BLE-стек только если включено.
//   ble_loop_tick()    — в loop(). Сейчас no-op (NimBLE сам диспатчит коллбэки),
//                        зарезервирован для idle-timeout watchdog'а.
//   ble_shutdown()     — опционально, при переходе в провижининг-режим.
//
// Управляется флагом g_state.ble_enabled (NVS scud_imm:ble_en) и build-time
// макросом SCUD_BLE_ENABLED. Если макрос не определён — все функции — stubs,
// NimBLE даже не линкуется (экономим ~70 KB flash + ~25 KB heap).
#pragma once

#include <stdint.h>
#include <stddef.h>

// True, если BLE peripheral активен и принимает соединения.
bool ble_init();

// Per-loop tick. Кладёт в .bss идл-таймеры, ничего не блокирует.
void ble_loop_tick();

// Принудительный stop advertising + disconnect всех клиентов.
void ble_shutdown();

// Возвращает true, если build с поддержкой BLE (SCUD_BLE_ENABLED определён).
bool ble_compiled_in();
