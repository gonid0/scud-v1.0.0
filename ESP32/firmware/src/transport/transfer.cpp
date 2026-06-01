#include "transfer.h"
#include "apdu.h"
#include "op_sink.h"
#include "op_dispatch.h"
#include "../config.h"
#include "../utils/little_endian.h"
#include "../ops/ops.h"
#include "../state/authoritative.h"
#include "../hw/lock.h"
#include "../hw/led.h"
#include "../hw/rtc.h"
#include "../integration/access_hooks.h"
#include "../state/reader_state.h"
#include <Arduino.h>
#include <esp_system.h>
#include <esp_heap_caps.h>
#include <string.h>
#include <stdlib.h>

static uint8_t g_apdu_rx[APDU_RX_BUF];
static uint8_t g_apdu_tx[APDU_TX_BUF];

// Человеческое имя для result-кодов из ops.h. Используется во всех «что вернула
// операция» логах, чтобы не гадать «0x22 это какой?».
static const char* res_name(uint8_t code) {
    switch (code) {
        case RES_OK:                return "OK";
        case RES_BAD_SIGNATURE:     return "BAD_SIGNATURE";
        case RES_BAD_FORMAT:        return "BAD_FORMAT";
        case RES_BAD_NONCE:         return "BAD_NONCE";
        case RES_NO_SLOT:           return "NO_SLOT";
        case RES_NOT_AUTHORIZED:    return "NOT_AUTHORIZED";
        case RES_STALE:             return "STALE";
        case RES_WRONG_READER:      return "WRONG_READER";
        case RES_TIME_REGRESSION:   return "TIME_REGRESSION";
        case RES_UNKNOWN_OPCODE:    return "UNKNOWN_OPCODE";
        case RES_EXPIRED:           return "EXPIRED";
        case RES_REVOKED_BLACKLIST: return "REVOKED_BLACKLIST";
        case RES_REVOKED_FILTER:    return "REVOKED_FILTER";
        case RES_INTERNAL_ERROR:    return "INTERNAL_ERROR";
        default:                    return "UNKNOWN";
    }
}

// Достаёт код ответа из ext-результата операции. Layout зависит от opcode.
// Возвращает 0xFE если формат не распознан.
static uint8_t extract_result_code(uint8_t inner_opcode, const uint8_t* buf, size_t len) {
    if (len < 2) return RESULT_CODE_UNPARSED;
    switch (inner_opcode) {
        case INNER_ACCESS:
            // MARK_ACCESS_VERDICT(1) + result_code(1) + reader_time(8) + next_nonce(32)
            return buf[1];
        case INNER_FDI:
            // MARK_FDI(1) + reader_id(16) + filter_version(8) + delivered_at(8) + blob(104)
            //  + reader_time_now(8) + signature(64) + next_nonce(32)
            // Коды ответа FDI не имеют — это всегда успешный envelope.
            return RES_OK;
        case INNER_BLACKLIST:
            // Собственный envelope, нет кода.
            return RES_OK;
        case INNER_HANDOVER_ISSUE:
            // Success → 167-B handover_token (marker 0x99); reject → OP_RESULT.
            if (buf[0] == MARK_HANDOVER_TOKEN) return RES_OK;
            return len >= 3 ? buf[2] : RESULT_CODE_UNPARSED;
        case INNER_TIME_SYNC:
        case INNER_FILTER_UPDATE:
        case INNER_REVOKE_KEY:
        case INNER_HANDOVER_PRESENT:
            // Generic OP_RESULT: marker(1) + in_reply_to(1) + code(1) + ...
            return len >= 3 ? buf[2] : RESULT_CODE_UNPARSED;
        default:
            return RESULT_CODE_UNPARSED;
    }
}

static bool send_push_info_once() {
    uint8_t info[146];
    if (build_info_bytes(info) != 146) return false;

    g_apdu_tx[0] = 0x00; g_apdu_tx[1] = OPCODE_PUSH_INFO;
    g_apdu_tx[2] = 0x00; g_apdu_tx[3] = 0x00;
    g_apdu_tx[4] = 146;
    memcpy(g_apdu_tx + 5, info, 146);
    g_apdu_tx[5 + 146] = 0x00;

    size_t n = apdu_exchange(g_apdu_tx, 5 + 146 + 1, g_apdu_rx, sizeof(g_apdu_rx));
    return n >= 2 && g_apdu_rx[n - 2] == 0x90 && g_apdu_rx[n - 1] == 0x00;
}

// Retry wrapper: first PUSH_INFO after SELECT_AID часто ловит PN532 RF-protocol
// error (0x0B), пока Android HCE-сервис достраивает контекст. Короткая пауза +
// повтор обычно спасает.
static bool send_push_info() {
    for (uint32_t attempt = 0; attempt < g_state.cfg.push_retries; attempt++) {
        if (attempt > 0) {
            Serial.printf("[TAP] PUSH_INFO retry %u\n", (unsigned)attempt);
            delay(g_state.cfg.push_delay);
        }
        if (send_push_info_once()) return true;
    }
    return false;
}

// Send PUSH_CHUNK series for big result: returns true on success.
// PUSH_CHUNK APDU data-field layout (Lc байт): [msg_id 4][sent 4][total 4][flags 1]
// [to_send 2][chunk to_send] = 15-байтовый заголовок + чанк. Заголовок ОБЯЗАН быть
// учтён: Lc = 15 + to_send и должен влезть в согласованный max_apdu_size (=240).
#define PUSH_CHUNK_HEADER 15

static bool send_push_chunk_series(uint32_t msg_id, const uint8_t* bytes, uint32_t total) {
    // БАГ-ФИКС #3 (hardware-observed): PUSH_CHUNK с командным APDU 240/246 B падает
    // ДЕТЕРМИНИРОВАННО (PN532 inDataExchange status=0x01, target no-answer). ВАЖНО: это
    // НЕ универсальный потолок на размер reader→phone команды — в тех же сессиях FETCH
    // (0xC2) с инлайновым ACCESS-verdict (Lc=236 → APDU 242 B, см. PREV_INLINE_MAX и
    // ветку ниже на :672) проходит 100%, как и PUSH_INFO (0xC1, 152 B). Значит дефект
    // специфичен для PUSH_CHUNK-обмена (0xC4), а не «команда > N байт»; реальную границу
    // в (152, 240] на железе ещё не снимали (ветка compile-only). Поэтому консервативно
    // пиним размер PUSH_CHUNK к самому большому 100%-надёжному reader→phone APDU —
    // PUSH_INFO (поле данных 146 B → APDU 5+146+1 = 152 B).
    // TODO(hw-sweep): поднять, нащупав реальную границу PUSH_CHUNK на ESP32+телефоне.
    const uint16_t PROVEN_CMD_DATA = 146;   // == PUSH_INFO data field (работает каждую сессию)
    const uint16_t chunk_max = PROVEN_CMD_DATA - PUSH_CHUNK_HEADER;   // 146 - 15 = 131
    uint32_t sent = 0;
    while (sent < total) {
        uint16_t to_send = (total - sent) > chunk_max ? chunk_max : (uint16_t)(total - sent);
        uint8_t flags = (sent + to_send == total) ? 0x01 : 0x00;

        g_apdu_tx[0] = 0x00; g_apdu_tx[1] = OPCODE_PUSH_CHUNK;
        g_apdu_tx[2] = 0x00; g_apdu_tx[3] = 0x00;
        uint8_t lc = (uint8_t)(15 + to_send);
        g_apdu_tx[4] = lc;

        uint8_t* d = g_apdu_tx + 5;
        write_u32le(d + 0,  msg_id);
        write_u32le(d + 4,  sent);
        write_u32le(d + 8,  total);
        d[12] = flags;
        write_u16le(d + 13, to_send);
        memcpy(d + 15, bytes + sent, to_send);
        g_apdu_tx[5 + lc] = 0x00;

        // N3: тот же дешёвый per-chunk retry, что и в READ_CHUNK-приёме.
        // PUSH_CHUNK идемпотентен — кадр несёт абсолютные sent/total, повтор
        // переотправляет тот же чанк. Одиночный RF-glitch не валит весь series.
        bool chunk_ok = false;
        for (uint32_t attempt = 0; attempt < READ_CHUNK_RETRIES; attempt++) {
            if (attempt > 0) delay(PUSH_INFO_RETRY_DELAY_MS);
            size_t n = apdu_exchange(g_apdu_tx, 5 + lc + 1, g_apdu_rx, sizeof(g_apdu_rx));
            if (n >= 2 && g_apdu_rx[n - 2] == 0x90 && g_apdu_rx[n - 1] == 0x00) {
                chunk_ok = true;
                break;
            }
        }
        if (!chunk_ok) {
            Serial.printf("[TAP] PUSH_CHUNK chunk failed: off=%u/%u to_send=%u Lc=%u apdu=%u\n",
                          (unsigned)sent, (unsigned)total, (unsigned)to_send,
                          (unsigned)lc, (unsigned)(5 + lc + 1));
            return false;
        }

        sent += to_send;
    }
    return true;
}

// Encode prev_result (empty / inline / reference) into data buffer, return length.
static size_t encode_prev_result(bool have_reference, uint32_t ref_msg_id,
                                 const uint8_t* inline_bytes, size_t inline_len,
                                 uint8_t* out) {
    if (have_reference) {
        out[0] = 0xFF; out[1] = 0xFF;
        write_u32le(out + 2, ref_msg_id);
        return 6;
    }
    if (inline_len == 0) {
        out[0] = 0x00; out[1] = 0x00;
        return 2;
    }
    write_u16le(out, (uint16_t)inline_len);
    memcpy(out + 2, inline_bytes, inline_len);
    return 2 + inline_len;
}

// Issue FETCH, parse response.
static bool send_fetch(const uint8_t* enc_prev, size_t enc_prev_len,
                       uint8_t* status_out,
                       uint8_t* inner_opcode_out,
                       uint32_t* msg_id_out,
                       uint32_t* total_len_out,
                       uint8_t*  first_chunk_out,
                       size_t*   first_chunk_len_out) {
    g_apdu_tx[0] = 0x00; g_apdu_tx[1] = OPCODE_FETCH;
    g_apdu_tx[2] = 0x00; g_apdu_tx[3] = 0x00;
    g_apdu_tx[4] = (uint8_t)enc_prev_len;
    memcpy(g_apdu_tx + 5, enc_prev, enc_prev_len);
    g_apdu_tx[5 + enc_prev_len] = 0x00;

    size_t n = apdu_exchange(g_apdu_tx, 5 + enc_prev_len + 1, g_apdu_rx, sizeof(g_apdu_rx));
    if (n < 3) return false;
    if (g_apdu_rx[n - 2] != 0x90 || g_apdu_rx[n - 1] != 0x00) return false;

    size_t payload = n - 2;
    uint8_t status = g_apdu_rx[0];
    *status_out = status;

    switch (status) {
        case FETCH_STATUS_NO_OP:
            return true;
        case FETCH_STATUS_OP_SINGLE: {
            if (payload < 4) return false;
            *inner_opcode_out = g_apdu_rx[1];
            uint16_t op_len = read_u16le(g_apdu_rx + 2);
            if (payload < (size_t)(4 + op_len)) return false;
            *total_len_out = op_len;
            *first_chunk_len_out = op_len;
            memcpy(first_chunk_out, g_apdu_rx + 4, op_len);
            return true;
        }
        case FETCH_STATUS_OP_CHUNKED: {
            if (payload < 12) return false;
            *inner_opcode_out = g_apdu_rx[1];
            *msg_id_out       = read_u32le(g_apdu_rx + 2);
            *total_len_out    = read_u32le(g_apdu_rx + 6);
            uint16_t fcl      = read_u16le(g_apdu_rx + 10);
            if (payload < (size_t)(12 + fcl)) return false;
            *first_chunk_len_out = fcl;
            memcpy(first_chunk_out, g_apdu_rx + 12, fcl);
            return true;
        }
        case FETCH_STATUS_ERROR: {
            // Shared §4.5: payload = [reason 1B] after status byte.
            if (payload >= 2) {
                first_chunk_out[0] = g_apdu_rx[1];
                *first_chunk_len_out = 1;
            } else {
                *first_chunk_len_out = 0;
            }
            return true; // allow caller to see error reason
        }
        default:
            return true;
    }
}

static bool send_read_chunk(uint32_t msg_id, uint32_t offset, uint16_t max_chunk,
                            uint8_t* out_chunk, uint16_t out_cap,
                            uint16_t* out_len, bool* out_last) {
    g_apdu_tx[0] = 0x00; g_apdu_tx[1] = OPCODE_READ_CHUNK;
    g_apdu_tx[2] = 0x00; g_apdu_tx[3] = 0x00;
    g_apdu_tx[4] = 10;
    write_u32le(g_apdu_tx + 5,  msg_id);
    write_u32le(g_apdu_tx + 9,  offset);
    write_u16le(g_apdu_tx + 13, max_chunk);
    g_apdu_tx[15] = 0x00;

    size_t n = apdu_exchange(g_apdu_tx, 16, g_apdu_rx, sizeof(g_apdu_rx));
    if (n < 5) return false;
    if (g_apdu_rx[n - 2] != 0x90 || g_apdu_rx[n - 1] != 0x00) return false;

    uint16_t chunk_len = read_u16le(g_apdu_rx + 0);
    uint8_t  flags     = g_apdu_rx[2];
    if (n < (size_t)(3 + chunk_len + 2)) return false;

    // FW-ARC-01: chunk_len приходит от телефона. Без этой проверки memcpy пишет в
    // op_buf+offset на chunk_len байт, и злонамеренный/битый ответ переполняет кучу
    // за границей выделенного op_buf. Отбрасываем чанк, не влезающий в остаток ёмкости.
    if (chunk_len > out_cap) return false;

    memcpy(out_chunk, g_apdu_rx + 3, chunk_len);
    *out_len  = chunk_len;
    *out_last = (flags & 0x01) != 0;
    return true;
}

static void send_end() {
    uint8_t end_apdu[6] = {0x00, OPCODE_END, 0x00, 0x00, 0x00, 0x00};
    uint8_t tmp[4];
    apdu_exchange(end_apdu, 6, tmp, sizeof(tmp));
}

// X1 (partial): per-op dispatch + transport policy now live in the shared
// transport/op_dispatch.{h,cpp} (single source of truth, byte-identical to the
// former static dispatch_op here and the BLE inline switch). The NFC call site
// below invokes it with OP_TRANSPORT_NFC. The large-FILTER_UPDATE flash route
// (N2/B6, earlier in this file) stays as-is — dispatch_op is the small/in-RAM
// path only.

static int      g_consecutive_failures = 0;
static uint32_t g_cooldown_until_ms    = 0;

// Cooldown после удачного END или ACCESS GRANTED — чтобы телефон не вёл
// бесконечный цикл перетапов, пока его не убрали от антенны.
// Значения cd_end / cd_grant — per-reader config (g_state.cfg, дефолты из config.h).

bool transfer_in_cooldown() {
    return (int32_t)(g_cooldown_until_ms - millis()) > 0;
}

// L1-fix: единственный источник истины для «что сделать после ACCESS-вердикта».
// Раньше этот блок дублировался дословно в NFC (run_tap_session) и BLE
// (ble_channel.cpp::on_op_complete) путях, и они РАСХОДИЛИСЬ — NFC ставил
// cooldown, BLE нет (finding L1). Теперь оба зовут этот хелпер. result-байты НЕ
// трогаются — меняется только локальное actuation-решение.
//
// РАЗДЕЛЕНИЕ ОТВЕТСТВЕННОСТИ (см. integration/access_hooks.h):
//   PLATFORM (здесь): валидация уже сделана в op_access; эта функция гарантирует
//   вызов хука РОВНО один раз на вердикт, с главного цикла (B3-safe), под
//   cooldown-гейтом (телефон в поле не перевзводит грант). Тайминг anti-retap
//   cooldown (cd_grant/cd_end) — тоже здесь, это свойство платформы.
//   INTEGRATOR (access_hooks.cpp): ЧТО физически происходит (замок/турникет/
//   барьер/лог) — в on_access_granted / on_access_denied. Дефолт — demo-kit.
//
// B3-safe: g_state и file-static g_cooldown_until_ms трогаются только с главного
// цикла — NFC зовёт это прямо в run_tap_session(); BLE — через on_op_complete,
// который крутится на главном цикле из ble_loop_tick (НЕ в host-task коллбэке).
void apply_access_result(const uint8_t* result, size_t result_len, uint8_t inner_opcode) {
    if (inner_opcode != INNER_ACCESS || result_len < 2 || result[0] != MARK_ACCESS_VERDICT) {
        return;
    }
    if (result[1] == RES_OK) {
        // GRANTED: вызываем integrator-хук + ставим длинный cooldown, чтобы не было
        // auto-перетапов, пока актуатор отрабатывает. L1: хук и cooldown — ТОЛЬКО
        // если ещё не в cooldown (подключённый телефон не перевзводит грант в тихое
        // окно; NFC и так не тапается в cooldown, gated в main.cpp; здесь то же явно
        // для BLE-пути).
        if (!transfer_in_cooldown()) {
            Serial.printf("[ACCESS] GRANTED → on_access_granted(%ums)\n",
                          g_state.lock_duration_ms);
            on_access_granted(g_state.lock_duration_ms);
            uint32_t cd = g_state.cfg.cd_grant;
            if (g_state.lock_duration_ms + COOLDOWN_GRANT_MARGIN_MS > cd) cd = g_state.lock_duration_ms + COOLDOWN_GRANT_MARGIN_MS;
            g_cooldown_until_ms = millis() + cd;
        } else {
            Serial.println("[ACCESS] GRANTED во время cooldown — хук не вызываю");
        }
    } else {
        Serial.printf("[ACCESS] DENIED: %s (0x%02X)\n",
                      res_name(result[1]), result[1]);
        on_access_denied(result[1]);
        g_cooldown_until_ms = millis() + g_state.cfg.cd_end;
    }
}

void run_tap_session() {
    g_state.session_seq++;
    // H3 FOLD-IN: no passage cache to reset — the receipt now rides the
    // ACCESS_VERDICT tail (built inline per-ACCESS), there is no per-session
    // staging to clear at the start of a new tap.
    uint32_t t_session_start = millis();
    uint32_t heap_start = esp_get_free_heap_size();
    uint64_t rtc_at_start = rtc_now();
    Serial.printf("[TAP] === session #%u start === reader=%02X%02X%02X%02X… "
                  "rtc=%llu filter_v=%llu bl_cnt=%u wl_cnt=%u heap=%u\n",
                  (unsigned)g_state.session_seq,
                  g_state.reader_id[0], g_state.reader_id[1],
                  g_state.reader_id[2], g_state.reader_id[3],
                  (unsigned long long)rtc_at_start,
                  (unsigned long long)g_state.filter_version,
                  (unsigned)g_state.blacklist_count,
                  (unsigned)g_state.whitelist_count,
                  (unsigned)heap_start);

    if (!send_push_info()) {
        Serial.println("[TAP] PUSH_INFO failed");
        if ((uint32_t)(++g_consecutive_failures) >= g_state.cfg.reinit_thr) {
            Serial.println("[TAP] 3 consecutive failures — trying PN532 reinit");
            if (apdu_reinit_pn532()) g_consecutive_failures = 0;
        }
        apdu_reset_field();
        return;
    }
    Serial.printf("[TAP] PUSH_INFO sent in %lums\n", (unsigned long)(millis() - t_session_start));
    g_consecutive_failures = 0;

    uint8_t   prev_buf[PREV_INLINE_BUF];   // inline up to ~256
    size_t    prev_len   = 0;
    bool      prev_is_ref = false;
    uint32_t  prev_msg_id = 0;

    // Dynamic buffer for big op payloads.
    uint8_t*  op_buf     = nullptr;
    size_t    op_buf_cap = 0;

    // Result buffer large enough for worst-case BLK response
    // (29 + 82 + 32*256 + 64 + 32 = 8399 B, see shared §5.9).
    // RESULT_BUF_CAP defined in config.h.
    uint8_t*  result_buf = (uint8_t*)malloc(RESULT_BUF_CAP);
    if (!result_buf) {
        Serial.println("[TAP] result_buf alloc failed");
        return;
    }

    while (true) {
        // N5: жёсткий дедлайн сессии — медленный/зависший телефон не держит
        // единственный поток ридера бесконечно (голодают BLE-tick и cleanup'ы).
        if ((uint32_t)(millis() - t_session_start) > g_state.cfg.nfc_deadline_ms) {
            Serial.printf("[TAP] session deadline %ums exceeded — abort\n",
                          (unsigned)g_state.cfg.nfc_deadline_ms);
            break;
        }
        // Encode prev_result
        uint8_t enc[APDU_RX_BUF];
        size_t  enc_len = encode_prev_result(prev_is_ref, prev_msg_id,
                                             prev_buf, prev_is_ref ? 0 : prev_len,
                                             enc);

        uint8_t status = 0, inner_opcode = 0;
        uint32_t msg_id = 0, total_len = 0;
        uint8_t first_chunk[APDU_RX_BUF];
        size_t first_chunk_len = 0;

        if (!send_fetch(enc, enc_len, &status, &inner_opcode, &msg_id, &total_len,
                        first_chunk, &first_chunk_len)) {
            Serial.println("[TAP] FETCH failed (link?)");
            break;
        }

        if (status == FETCH_STATUS_NO_OP) {
            Serial.printf("[TAP] FETCH → NO_OP, session END after %lums\n",
                          (unsigned long)(millis() - t_session_start));
            send_end();
            // Штатное завершение — cooldown, если ещё не установлен более длинным
            // grant-значением (в этом случае lock_duration определяет окно).
            if (!transfer_in_cooldown()) {
                g_cooldown_until_ms = millis() + g_state.cfg.cd_end;
            }
            Serial.printf("[TAP] cooldown %lums\n",
                          (unsigned long)(g_cooldown_until_ms - millis()));
            break;
        }
        if (status == FETCH_STATUS_ERROR) {
            uint8_t reason = first_chunk_len ? first_chunk[0] : 0;
            const char* rname =
                reason == FETCH_ERR_BAD_PREV_RESULT ? "BAD_PREV_RESULT" :
                reason == FETCH_ERR_SESSION_LOST   ? "SESSION_LOST"    :
                reason == FETCH_ERR_INTERNAL       ? "INTERNAL"        : "UNKNOWN";
            Serial.printf("[TAP] phone ERROR 0x%02X (%s)\n", reason, rname);
            if (reason == FETCH_ERR_SESSION_LOST) {
                // Shared §4.9: reader re-sends PUSH_INFO, resets prev_result.
                if (!send_push_info()) break;
                prev_len    = 0;
                prev_is_ref = false;
                continue;
            }
            break;
        }

        // Get full op payload (in RAM for non-streaming ops; filter_update streams differently but
        // в MVP также буферизируем, см. spec §9.5).
        const uint8_t* payload = nullptr;
        size_t payload_len     = total_len;

        // Человекочитаемое имя опкода для логов.
        const char* opc_name =
            inner_opcode == INNER_ACCESS        ? "ACCESS"        :
            inner_opcode == INNER_FDI           ? "FDI"           :
            inner_opcode == INNER_TIME_SYNC     ? "TIME_SYNC"     :
            inner_opcode == INNER_FILTER_UPDATE ? "FILTER_UPDATE" :
            inner_opcode == INNER_BLACKLIST     ? "BLACKLIST"     :
            inner_opcode == INNER_REVOKE_KEY    ? "REVOKE_KEY"    :
            inner_opcode == INNER_HANDOVER_ISSUE   ? "HANDOVER_ISSUE"   :
            inner_opcode == INNER_HANDOVER_PRESENT ? "HANDOVER_PRESENT" : "UNKNOWN";

        // N2/B6: a big FILTER_UPDATE is dispatched straight from the flash slot
        // (no 16 KB op_buf). The flag tells the shared result-handling block to
        // skip dispatch_op (the handler already ran). result_buf/result_len are
        // filled by the flash branch below.
        bool   flash_dispatched = false;
        size_t flash_result_len = 0;

        if (status == FETCH_STATUS_OP_SINGLE) {
            Serial.printf("[TAP] FETCH → %s (single, %uB)\n", opc_name, (unsigned)first_chunk_len);
            payload = first_chunk;
            payload_len = first_chunk_len;
        } else { // OP_CHUNKED
            Serial.printf("[TAP] FETCH → %s (chunked, total=%uB, first=%uB)\n",
                          opc_name, (unsigned)total_len, (unsigned)first_chunk_len);

            // N2/B6 — large FILTER_UPDATE: stream straight into the inactive A/B
            // slot via op_sink instead of malloc'ing op_buf. The 16 KB cap is
            // lifted for THIS path only (capped at MAX_FILTER_BYTES); all other
            // ops (and small filters) keep the RAM path below. The op payload is
            // [inner_opcode 1][courier_id 16][filter_package …]; we strip the
            // 17-B prefix and stream only the package into the sink (so the slot
            // file == the signed package, exactly what verify-from-flash reads).
            if (inner_opcode == INNER_FILTER_UPDATE && total_len > TRANSFER_BUFFER_CAP) {
                const uint32_t PREFIX = 1u + 16u;                 // inner_opcode + courier_id
                if (total_len < PREFIX + 56u + 64u ||
                    (uint64_t)(total_len - PREFIX) > (uint64_t)MAX_FILTER_BYTES) {
                    Serial.printf("[TAP] FILTER_UPDATE size %u out of range — abort\n",
                                  (unsigned)total_len);
                    break;
                }
                if (first_chunk_len < PREFIX + 56u) {
                    // Need the prefix + full package header in the first chunk to
                    // route + validate (MAX_APDU_DATA_SIZE 240 ≥ 73, always true).
                    Serial.println("[TAP] FILTER_UPDATE first chunk too small — abort");
                    break;
                }
                uint8_t courier_id[16];
                memcpy(courier_id, first_chunk + 1, 16);
                uint8_t pkg_header[56];
                memcpy(pkg_header, first_chunk + PREFIX, 56);

                op_sink sink;
                if (!flash_slot_sink_open(&sink)) {
                    Serial.println("[TAP] flash_slot_sink_open failed — abort");
                    break;
                }
                // First chunk: write the package portion (everything after the prefix).
                if (!sink.write(&sink, first_chunk + PREFIX, (uint32_t)(first_chunk_len - PREFIX))) {
                    Serial.println("[TAP] flash sink write (first) failed — abort");
                    flash_slot_sink_close(&sink);
                    break;
                }
                uint32_t offset = first_chunk_len;
                bool done = false, sink_err = false;
                while (!done && offset < total_len) {
                    if ((uint32_t)(millis() - t_session_start) > g_state.cfg.nfc_deadline_ms) {
                        Serial.println("[TAP] deadline during flash READ_CHUNK — abort");
                        sink_err = true; break;
                    }
                    uint16_t got = 0; bool last = false;
                    uint32_t remaining = total_len - offset;
                    uint16_t cap  = remaining > 0xFFFFu ? 0xFFFFu : (uint16_t)remaining;
                    uint16_t want = cap < MAX_APDU_DATA_SIZE ? cap : MAX_APDU_DATA_SIZE;
                    uint8_t  rxbuf[MAX_APDU_DATA_SIZE];
                    bool chunk_ok = false;
                    for (uint32_t attempt = 0; attempt < READ_CHUNK_RETRIES; attempt++) {
                        if (attempt > 0) {
                            Serial.printf("[TAP] flash READ_CHUNK retry %u at %u/%u\n",
                                          (unsigned)attempt, (unsigned)offset, (unsigned)total_len);
                            delay(PUSH_INFO_RETRY_DELAY_MS);
                        }
                        if (send_read_chunk(msg_id, offset, want, rxbuf, sizeof(rxbuf), &got, &last)) {
                            chunk_ok = true; break;
                        }
                    }
                    if (!chunk_ok) {
                        Serial.printf("[TAP] flash READ_CHUNK failed at %u/%u\n",
                                      (unsigned)offset, (unsigned)total_len);
                        sink_err = true; break;
                    }
                    if (!sink.write(&sink, rxbuf, got)) {
                        Serial.println("[TAP] flash sink write failed (SPIFFS full?) — abort");
                        sink_err = true; break;
                    }
                    offset += got;
                    if (last) done = true;
                }
                if (sink_err) {
                    flash_slot_sink_close(&sink);
                    goto abort_session;
                }
                Serial.printf("[TAP] FILTER_UPDATE streamed to flash slot (%uB pkg)\n",
                              (unsigned)sink.len(&sink));
                flash_result_len = op_filter_update_from_flash(courier_id, pkg_header, 56,
                                                               &sink, result_buf, RESULT_BUF_CAP);
                flash_slot_sink_close(&sink);
                flash_dispatched = true;
            } else {
            if (total_len > TRANSFER_BUFFER_CAP) {
                Serial.printf("[TAP] op too big (%u B > cap %u), abort\n",
                              (unsigned)total_len, (unsigned)TRANSFER_BUFFER_CAP);
                break;
            }
            if (op_buf_cap < total_len) {
                if (op_buf) free(op_buf);
                op_buf = (uint8_t*)malloc(total_len);
                op_buf_cap = op_buf ? total_len : 0;
                if (!op_buf) {
                    Serial.printf("[TAP] op_buf malloc(%u) failed\n", (unsigned)total_len);
                    break;
                }
            }
            memcpy(op_buf, first_chunk, first_chunk_len);
            uint32_t offset = first_chunk_len;
            bool done = false;
            while (!done && offset < total_len) {
                // N5: дедлайн действует и внутри READ_CHUNK-цикла (поток мелких
                // чанков без прогресса тоже ограничен по времени).
                if ((uint32_t)(millis() - t_session_start) > g_state.cfg.nfc_deadline_ms) {
                    Serial.println("[TAP] deadline during READ_CHUNK — abort");
                    goto abort_session;
                }
                uint16_t got  = 0;
                bool last = false;
                // FW-ARC-01: остаток ёмкости op_buf = total_len - offset (буфер выделен
                // под total_len). Запрашиваем и принимаем не больше остатка.
                uint32_t remaining = total_len - offset;
                uint16_t cap  = remaining > 0xFFFFu ? 0xFFFFu : (uint16_t)remaining;
                uint16_t want = cap < MAX_APDU_DATA_SIZE ? cap : MAX_APDU_DATA_SIZE;
                // N3: дешёвый per-chunk retry. READ_CHUNK идемпотентен (читает по
                // абсолютному offset), поэтому одиночный RF-glitch не валит всю
                // передачу — повторяем тот же offset до READ_CHUNK_RETRIES раз.
                // offset/advance не трогаем: re-request — это тот же offset.
                // Дедлайн сессии (проверяется выше в цикле) по-прежнему ограничивает
                // суммарное время, так что ретраи не могут зависнуть.
                bool chunk_ok = false;
                for (uint32_t attempt = 0; attempt < READ_CHUNK_RETRIES; attempt++) {
                    if (attempt > 0) {
                        Serial.printf("[TAP] READ_CHUNK retry %u at offset=%u/%u\n",
                                      (unsigned)attempt, (unsigned)offset, (unsigned)total_len);
                        delay(PUSH_INFO_RETRY_DELAY_MS);
                    }
                    if (send_read_chunk(msg_id, offset, want, op_buf + offset, cap, &got, &last)) {
                        chunk_ok = true;
                        break;
                    }
                }
                if (!chunk_ok) {
                    Serial.printf("[TAP] READ_CHUNK failed at offset=%u/%u after %u attempts\n",
                                  (unsigned)offset, (unsigned)total_len, (unsigned)READ_CHUNK_RETRIES);
                    goto abort_session;
                }
                offset += got;
                if (last) done = true;
            }
            Serial.printf("[TAP] %s chunked fully received (%uB)\n", opc_name, (unsigned)offset);
            payload = op_buf;
            payload_len = offset;
            } // end RAM-path branch (N2/B6 else)
        }

        {
            uint32_t t_dispatch = millis();
            // N2/B6: the large-FILTER_UPDATE flash branch already ran the handler
            // (op_filter_update_from_flash) — reuse its result instead of calling
            // dispatch_op on a non-existent op_buf payload.
            size_t result_len = flash_dispatched
                ? flash_result_len
                : dispatch_op(inner_opcode, payload, payload_len,
                              result_buf, RESULT_BUF_CAP, OP_TRANSPORT_NFC);
            if (result_len == 0) {
                Serial.printf("[TAP] %s produced no result (payload %uB)\n",
                              opc_name, (unsigned)payload_len);
                prev_len = 0;
                prev_is_ref = false;
                continue;
            }
            uint8_t rcode = extract_result_code(inner_opcode, result_buf, result_len);
            Serial.printf("[TAP] %s → result %uB in %lums (code=0x%02X %s)\n",
                          opc_name, (unsigned)result_len,
                          (unsigned long)(millis() - t_dispatch),
                          rcode, res_name(rcode));

            // Per-opcode дополнительная информация, если интересна.
            if (inner_opcode == INNER_TIME_SYNC && rcode == RES_OK) {
                int64_t delta_sec = (int64_t)rtc_now() - (int64_t)rtc_at_start;
                Serial.printf("[TAP] TIME_SYNC applied: rtc %llu → %llu (Δ%+lld с)\n",
                              (unsigned long long)rtc_at_start,
                              (unsigned long long)rtc_now(),
                              (long long)delta_sec);
            } else if (inner_opcode == INNER_FILTER_UPDATE && rcode == RES_OK) {
                Serial.printf("[TAP] FILTER_UPDATE applied: new version=%llu m_bits=%u k=%u wl=%u\n",
                              (unsigned long long)g_state.filter_version,
                              (unsigned)g_state.filter_m_bits,
                              (unsigned)g_state.filter_k_hashes,
                              (unsigned)g_state.whitelist_count);
            } else if (inner_opcode == INNER_FDI && result_len >= 33) {
                uint64_t fv = read_u64le(result_buf + 17);
                uint64_t dla = read_u64le(result_buf + 25);
                Serial.printf("[TAP] FDI envelope: filter_v=%llu delivered_at=%llu\n",
                              (unsigned long long)fv, (unsigned long long)dla);
            } else if (inner_opcode == INNER_BLACKLIST && result_len >= 29) {
                uint16_t num = read_u16le(result_buf + 25);
                uint16_t blob = read_u16le(result_buf + 27);
                Serial.printf("[TAP] BLK envelope: entries=%u blob=%uB\n",
                              (unsigned)num, (unsigned)blob);
            } else if (inner_opcode == INNER_REVOKE_KEY && rcode == RES_OK) {
                Serial.printf("[TAP] REVOKE_KEY OK (blacklist now %u entries)\n",
                              (unsigned)g_state.blacklist_count);
            }

            // ACCESS VERDICT: trigger lock async AFTER we queue result for send.
            // L1: единый actuation-хелпер (общий с BLE-путём); result-байты не трогает.
            apply_access_result(result_buf, result_len, inner_opcode);

            if (result_len > PREV_INLINE_MAX) {
                uint32_t new_msg_id = esp_random();
                if (!send_push_chunk_series(new_msg_id, result_buf, (uint32_t)result_len)) {
                    Serial.println("[TAP] PUSH_CHUNK series failed");
                    break;
                }
                prev_is_ref = true;
                prev_msg_id = new_msg_id;
                prev_len    = 0;
            } else {
                memcpy(prev_buf, result_buf, result_len);
                prev_len    = result_len;
                prev_is_ref = false;
            }
        }
    }

abort_session:
    if (op_buf) free(op_buf);
    free(result_buf);
    bool cooldown = transfer_in_cooldown();
    uint32_t heap_end = esp_get_free_heap_size();
    int32_t heap_delta = (int32_t)heap_end - (int32_t)heap_start;
    Serial.printf("[TAP] === session #%u end (%lums, %s, heap=%u Δ%+ld) ===\n",
                  (unsigned)g_state.session_seq,
                  (unsigned long)(millis() - t_session_start),
                  cooldown ? "cooldown+field_off" : "abort+field_cycle",
                  (unsigned)heap_end, (long)heap_delta);
    // Штатное завершение (cooldown уже установлен): просто выключаем поле
    // и оставляем выключенным на время cooldown — это разрывает цикл
    // автоматических перетапов.
    // Аварийное завершение: off→on cycle, чтобы HCE телефона сбросилось
    // и при следующем приближении можно было тапнуть снова.
    if (cooldown) {
        apdu_field_off();
    } else {
        apdu_reset_field();
    }
}
