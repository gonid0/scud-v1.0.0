#include "serial_cmd.h"
#include "../config.h"
#include "../state/reader_state.h"
#include "../state/immutable.h"
#include "../state/reader_config.h"
#include "../crypto/ed25519.h"
#include "../hw/rtc.h"
#include <Arduino.h>
#include <esp_system.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>

static char  line_buf[PROV_LINE_BUF_SIZE];
static size_t line_pos = 0;

static bool parse_hex(const char* s, uint8_t* out, size_t out_len) {
    for (size_t i = 0; i < out_len; i++) {
        char hi = s[i * 2], lo = s[i * 2 + 1];
        auto nib = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        int h = nib(hi), l = nib(lo);
        if (h < 0 || l < 0) return false;
        out[i] = (uint8_t)((h << 4) | l);
    }
    return true;
}

static void print_hex(const uint8_t* b, size_t n) {
    static const char* h = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        Serial.write(h[(b[i] >> 4) & 0xF]);
        Serial.write(h[b[i] & 0xF]);
    }
}

static void cmd_help() {
    Serial.println(F(
        "Commands:\n"
        "  HELP\n"
        "  GEN-KEYPAIR                      -- generate Ed25519 seed+pub into RAM\n"
        "  SHOW-PUBKEY\n"
        "  SET-READER-ID <hex16>\n"
        "  SET-GROUP-ID <hex16>\n"
        "  SET-SERVER-ED-PUB <hex32>\n"
        "  SET-SERVER-X-PUB  <hex32>\n"
        "  SET-LOCK-DURATION <ms>\n"
        "  SET-TIME <unix_seconds>\n"
        "  BLE-ENABLE | BLE-DISABLE         -- toggle BLE radio (needs COMMIT)\n"
        "  -- per-reader config (Phase 1, needs COMMIT; clamped to range):\n"
        "  SET-CLOCK-SKEW SET-NONCE-TTL SET-NFC-DEADLINE SET-BLE-IDLE\n"
        "  SET-TSYNC-DRIFT SET-TSYNC-BOOTSTRAP SET-PASSAGE-DIRECTION\n"
        "  SET-WHITELIST-MAX SET-BL-DELTA-MAX SET-COOLDOWN-END SET-COOLDOWN-GRANT\n"
        "  SET-PN532-RETRY SET-RF-ATR SET-RF-RETRY SET-FIELD-PAUSE\n"
        "  SET-PUSH-RETRIES SET-PUSH-DELAY SET-REINIT-THRESHOLD SET-NFC-DETECT\n"
        "  SET-BLE-MTU SET-BLE-INFO-DEFER\n"
        "  SET-BLACKLIST-CAP SET-NONCE-RING\n"
        "  STATUS\n"
        "  COMMIT\n"
        "  RESET"
    ));
}

static void cmd_gen_keypair() {
    for (int i = 0; i < 8; i++) {
        uint32_t r = esp_random();
        memcpy(g_state.reader_ed_priv + i * 4, &r, 4);
    }
    ed25519_derive_pub(g_state.reader_ed_priv, g_state.reader_ed_pub);
    Serial.println(F("OK keypair generated"));
}

static void cmd_show_pubkey() {
    Serial.print(F("pubkey="));
    print_hex(g_state.reader_ed_pub, 32);
    Serial.println();
}

static void cmd_status() {
    Serial.print(F("reader_id="));      print_hex(g_state.reader_id, 16); Serial.println();
    Serial.print(F("reader_group_id="));print_hex(g_state.reader_group_id, 16); Serial.println();
    Serial.print(F("reader_pub="));     print_hex(g_state.reader_ed_pub, 32); Serial.println();
    Serial.print(F("server_ed_pub=")); print_hex(g_state.server_ed_pub, 32); Serial.println();
    Serial.print(F("server_x_pub="));  print_hex(g_state.server_x_pub, 32); Serial.println();
    Serial.printf("lock_duration_ms=%u\n", g_state.lock_duration_ms);
    Serial.printf("ble_enabled=%d\n", g_state.ble_enabled ? 1 : 0);
    reader_config_status();   // Phase 1: per-reader operational params
}

static void process_line(char* line) {
    // Strip trailing CR/LF and spaces.
    char* end = line + strlen(line);
    while (end > line && (end[-1] == '\r' || end[-1] == '\n' || end[-1] == ' ')) *--end = 0;
    if (!*line) return;

    char* space = strchr(line, ' ');
    const char* cmd = line;
    const char* arg = nullptr;
    if (space) { *space = 0; arg = space + 1; }

    if (!strcasecmp(cmd, "HELP"))               cmd_help();
    else if (!strcasecmp(cmd, "GEN-KEYPAIR"))   cmd_gen_keypair();
    else if (!strcasecmp(cmd, "SHOW-PUBKEY"))   cmd_show_pubkey();
    else if (!strcasecmp(cmd, "STATUS"))        cmd_status();
    else if (!strcasecmp(cmd, "SET-READER-ID") && arg && strlen(arg) == 32) {
        if (parse_hex(arg, g_state.reader_id, 16)) Serial.println(F("OK"));
        else Serial.println(F("ERR bad hex"));
    }
    else if (!strcasecmp(cmd, "SET-GROUP-ID") && arg && strlen(arg) == 32) {
        if (parse_hex(arg, g_state.reader_group_id, 16)) Serial.println(F("OK"));
        else Serial.println(F("ERR bad hex"));
    }
    else if (!strcasecmp(cmd, "SET-SERVER-ED-PUB") && arg && strlen(arg) == 64) {
        if (parse_hex(arg, g_state.server_ed_pub, 32)) Serial.println(F("OK"));
        else Serial.println(F("ERR bad hex"));
    }
    else if (!strcasecmp(cmd, "SET-SERVER-X-PUB") && arg && strlen(arg) == 64) {
        if (parse_hex(arg, g_state.server_x_pub, 32)) Serial.println(F("OK"));
        else Serial.println(F("ERR bad hex"));
    }
    else if (!strcasecmp(cmd, "SET-LOCK-DURATION") && arg) {
        // Clamp to [LOCK_DURATION_MIN_MS .. LOCK_DURATION_MAX_MS]: 0 = strike never
        // releases (lockout), a huge value = door held open. strtol handles overflow
        // inputs cleanly (atoi is UB past INT_MAX).
        long ld = strtol(arg, nullptr, 10);
        if (ld < LOCK_DURATION_MIN_MS) ld = LOCK_DURATION_MIN_MS;
        if (ld > LOCK_DURATION_MAX_MS) ld = LOCK_DURATION_MAX_MS;
        g_state.lock_duration_ms = (uint16_t)ld;
        Serial.printf("OK lock_duration_ms=%u\n", (unsigned)g_state.lock_duration_ms);
    }
    else if (!strcasecmp(cmd, "SET-TIME") && arg) {
        uint64_t t = strtoull(arg, nullptr, 10);
        if (rtc_set(t)) Serial.println(F("OK"));
        else            Serial.println(F("ERR rtc"));
    }
    else if (!strcasecmp(cmd, "BLE-ENABLE")) {
        g_state.ble_enabled = true;
        Serial.println(F("OK ble enabled (run COMMIT to persist)"));
    }
    else if (!strcasecmp(cmd, "BLE-DISABLE")) {
        g_state.ble_enabled = false;
        Serial.println(F("OK ble disabled (run COMMIT to persist)"));
    }
    else if (!strcasecmp(cmd, "COMMIT")) {
        if (commit_immutable_config()) Serial.println(F("OK committed"));
        else                           Serial.println(F("ERR nvs"));
    }
    else if (!strcasecmp(cmd, "RESET")) {
        if (wipe_immutable_config()) Serial.println(F("OK wiped"));
        else                         Serial.println(F("ERR nvs"));
    }
    // Phase 1: table-driven per-reader config SET-* commands. Tried before the
    // unknown-command fallthrough; returns true (and prints OK) on a match.
    else if (reader_config_set(cmd, arg)) {
        // handled
    }
    else {
        Serial.println(F("ERR unknown command (HELP)"));
    }
}

void handle_provisioning_serial() {
    while (Serial.available()) {
        int c = Serial.read();
        if (c < 0) break;
        if (c == '\n' || c == '\r') {
            if (line_pos > 0) {
                line_buf[line_pos] = 0;
                process_line(line_buf);
                line_pos = 0;
            }
        } else if (line_pos + 1 < sizeof(line_buf)) {
            line_buf[line_pos++] = (char)c;
        } else {
            line_pos = 0;   // overflow → drop
            Serial.println(F("ERR line too long"));
        }
    }
}
