#include <Arduino.h>
#include <Wire.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include "config.h"
#include "state/reader_state.h"
#include "state/immutable.h"
#include "state/authoritative.h"
#include "state/local.h"
#include "hw/rtc.h"
#include "hw/led.h"
#include "hw/lock.h"
#include "transport/apdu.h"
#include "transport/transfer.h"
#include "provisioning/serial_cmd.h"
#include "ble/ble_channel.h"
#include "state/reader_config.h"

static void provisioning_loop() {
    while (true) {
        handle_provisioning_serial();
        led_provisioning_blink();
        delay(PROVISIONING_LOOP_DELAY_MS);
    }
}

static const char* reset_reason_str(esp_reset_reason_t r) {
    switch (r) {
        case ESP_RST_POWERON:   return "POWERON";
        case ESP_RST_EXT:       return "EXT_PIN";
        case ESP_RST_SW:        return "SW_RESET";
        case ESP_RST_PANIC:     return "PANIC";
        case ESP_RST_INT_WDT:   return "INT_WDT";
        case ESP_RST_TASK_WDT:  return "TASK_WDT";
        case ESP_RST_WDT:       return "OTHER_WDT";
        case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
        case ESP_RST_BROWNOUT:  return "BROWNOUT";
        case ESP_RST_SDIO:      return "SDIO";
        default:                return "UNKNOWN";
    }
}

void setup() {
    Serial.begin(SERIAL_CONSOLE_BAUD);
    delay(BOOT_SETTLE_DELAY_MS);

    Serial.printf("[BOOT] reset reason: %s\n", reset_reason_str(esp_reset_reason()));

    led_init();
    lock_init();
    pinMode(PROVISIONING_BUTTON_PIN, INPUT_PULLUP);

    // I2C for DS3231 only.
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ);

    // RTC up BEFORE provisioning_loop(): the SET-TIME CLI command calls rtc_set(),
    // which needs rtc_ok. Initialising the RTC only after provisioning (as before)
    // made SET-TIME during first-time provisioning ALWAYS return "ERR rtc" on a
    // fresh reader. DS3231 needs only I2C (begun above) — no other state.
    if (!rtc_init()) {
        Serial.println(F("[WARN] DS3231 init failed; awaiting TIME_SYNC"));
    }

    // B8: дефолт BLE-gate — вкл; load_immutable_config переопределит из NVS у
    // провижинённого ридера, а провижининг закоммитит это значение.
    g_state.ble_enabled = true;

    // Phase 1: заполняем per-reader config дефолтами из config.h. Это гарантирует
    // валидные значения даже на «чистом» ридере, у которого namespace scud_imm
    // ещё не существует (read-only Preferences.begin вернёт false). У провижинённого
    // ридера load_immutable_config переопределит их из NVS (ключ отсутствует → дефолт).
    reader_config_defaults();

    // Load immutable config; if missing — provisioning mode.
    if (!load_immutable_config()) {
        Serial.println(F("[PROVISIONING] device not provisioned. Use Serial CLI."));
        provisioning_loop();
    }
    // Manual provisioning override (hold boot button at power-on).
    if (digitalRead(PROVISIONING_BUTTON_PIN) == LOW) {
        Serial.println(F("[PROVISIONING] forced by button."));
        provisioning_loop();
    }

    load_authoritative_state();
    load_local_state();
    load_filter_from_flash();      // ok if absent (first boot)

    if (!apdu_init()) {
        Serial.println(F("[FATAL] PN532 init failed; check SEL0/SEL1, wiring, 5V"));
        led_fatal_blink();
        while (true) delay(FATAL_HALT_DELAY_MS);
    }

    g_state.session_seq = 0;

    if (ble_compiled_in() && g_state.ble_enabled) {
        if (ble_init()) {
            Serial.println(F("[BLE] channel up (shared §16)"));
        } else {
            Serial.println(F("[BLE] init failed — NFC-only mode"));
        }
    } else {
        Serial.println(F("[BLE] off (not compiled in, or ble_en=false)"));
    }

    // Task-watchdog backstop (FW-ARC-04). Subscribe the loop task ONLY here — after
    // the (interactive, possibly long) provisioning_loop — so provisioning is never
    // watchdogged. If loop() later wedges (stuck PN532 HSU exchange, deadlocked
    // host-task) past TASK_WDT_TIMEOUT_S with no esp_task_wdt_reset(), the chip
    // reboots instead of leaving the door dead until a manual reset. The 8 s NFC
    // session deadline only bounds the NORMAL flow; it is not a hang backstop.
#if ESP_IDF_VERSION_MAJOR >= 5
    esp_task_wdt_config_t wdt_cfg = {
        .timeout_ms     = TASK_WDT_TIMEOUT_S * 1000,
        .idle_core_mask = 0,
        .trigger_panic  = true,
    };
    esp_task_wdt_reconfigure(&wdt_cfg);   // TWDT already inited by Arduino-ESP32 v3
#else
    esp_task_wdt_init(TASK_WDT_TIMEOUT_S, true);
#endif
    esp_task_wdt_add(NULL);               // watch the loop task

    Serial.println(F("[READY]"));
}

void loop() {
    esp_task_wdt_reset();   // feed the watchdog each iteration

    if (!transfer_in_cooldown() && apdu_detect_target(g_state.cfg.nfc_detect)) {
        run_tap_session();
    }

    ble_loop_tick();
    led_tick();             // advance non-blocking access-feedback LED pattern
    cleanup_stale_nonces();
    expire_local_blacklist_entries();

    // NOTE for the future power profile (docs/13 §3.7): when delay() is replaced
    // by power_idle(), gate sleep on `!led_feedback_active() && !lock_is_busy()`
    // (alongside !transfer_in_cooldown()) so sleep cannot truncate actuation.
    delay(MAIN_LOOP_DELAY_MS);
}
