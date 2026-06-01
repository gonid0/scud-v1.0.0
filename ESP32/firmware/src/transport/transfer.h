#pragma once
#include <stdint.h>
#include <stddef.h>

// Wire opcodes (shared §3.2).
#define OPCODE_SELECT_AID 0xA4
#define OPCODE_PUSH_INFO  0xC1
#define OPCODE_FETCH      0xC2
#define OPCODE_READ_CHUNK 0xC3
#define OPCODE_PUSH_CHUNK 0xC4
#define OPCODE_END        0xC5

// FETCH status values.
#define FETCH_STATUS_NO_OP       0x00
#define FETCH_STATUS_OP_SINGLE   0x01
#define FETCH_STATUS_OP_CHUNKED  0x02
#define FETCH_STATUS_ERROR       0x03

// FETCH error reasons (shared §4.5).
#define FETCH_ERR_BAD_PREV_RESULT 0x01
#define FETCH_ERR_SESSION_LOST    0x02
#define FETCH_ERR_INTERNAL        0x03

// Inner opcodes are in ops/ops.h to keep them accessible to both
// transport dispatch and handlers.

// Запуск tap-сессии после успешного SELECT AID.
// Блокирует loop(), возвращает при NO_OP или разрыве.
void run_tap_session();

// true, если мы в cooldown-окне после только что завершённой сессии.
// Main loop пропускает polling, пока true.
bool transfer_in_cooldown();

// L1-fix: единый post-dispatch «result actuation» для ACCESS-вердикта — замок /
// LED / cooldown. Раньше эта логика дублировалась в NFC (transfer.cpp) и BLE
// (ble_channel.cpp) путях и РАСХОДИЛАСЬ (BLE не ставил cooldown). Теперь оба
// транспорта зовут ЭТОТ хелпер, так что поведение байт-в-байт одинаково.
// Вызывать ТОЛЬКО с главного цикла (NFC run_tap_session напрямую; BLE — через
// on_op_complete в ble_loop_tick): трогает g_state + file-static cooldown,
// которые по B3-инварианту main-loop-only. На INNER_ACCESS && result_len>=2 &&
// result[0]==MARK_ACCESS_VERDICT: RES_OK -> on_access_granted (замок +
// led_feedback_granted) + grant-cooldown (только если ещё не в cooldown — иначе
// не пере-открываем замок); иначе -> on_access_denied (led_feedback_deny) +
// cd_end-cooldown.
void apply_access_result(const uint8_t* result, size_t result_len, uint8_t inner_opcode);
