#include "led.h"
#include "../config.h"
#include <Arduino.h>

static inline void led_write(bool on) {
    bool level = LED_ONBOARD_ACTIVE_HIGH ? on : !on;
    digitalWrite(LED_ONBOARD_PIN, level ? HIGH : LOW);
}

void led_init() {
    pinMode(LED_ONBOARD_PIN, OUTPUT);
    led_write(false);
}

// ---------------------------------------------------------------------------
// Access-feedback state machine (non-blocking, ticked from loop())
// ---------------------------------------------------------------------------
// Replaces the old led_set_granted/led_set_idle (lit as a side effect inside
// lock_task) and led_deny_flash_async (a detached vTaskDelay task). Both edges
// of every pattern now live here, in the LED layer — see led.h for the why.

namespace {
enum class Fb : uint8_t { None, Granted, Deny };

Fb       s_fb      = Fb::None;
uint32_t s_started = 0;   // millis() when the pattern was armed
uint32_t s_window  = 0;   // granted hold duration (ms)

// Deny pattern: LED_DENY_BLINKS pulses of LED_DENY_HALF_MS on / LED_DENY_HALF_MS
// off (default 3×120/120 ≈ 720 ms — byte-identical to the previous behaviour).
//
// BATTERY note (docs/13 §3.5): a multi-blink pattern cannot complete across a
// deep-sleep boundary in the future battery_nfc profile (the boundary is a chip
// reset). When that profile lands, set LED_DENY_BLINKS = 1 (single pulse) under
// the profile flag so deny feedback is a single edge that fits before sleep.
constexpr uint8_t  LED_DENY_BLINKS  = 3;
constexpr uint32_t LED_DENY_HALF_MS = 120;
constexpr uint32_t LED_DENY_TOTAL_MS = (uint32_t)LED_DENY_BLINKS * 2u * LED_DENY_HALF_MS;
}  // namespace

void led_feedback_granted(uint32_t window_ms) {
    s_fb      = Fb::Granted;
    s_started = millis();
    s_window  = window_ms;
    led_write(true);
}

void led_feedback_deny() {
    s_fb      = Fb::Deny;
    s_started = millis();
    led_write(true);
}

bool led_feedback_active() {
    return s_fb != Fb::None;
}

void led_tick() {
    if (s_fb == Fb::None) return;

    const uint32_t elapsed = millis() - s_started;  // rollover-safe (unsigned)

    if (s_fb == Fb::Granted) {
        if (elapsed >= s_window) {
            led_write(false);
            s_fb = Fb::None;
        }
        return;
    }

    // Deny: level is a pure function of elapsed time — even half-period = ON,
    // odd = OFF; the LED goes OFF and the pattern ends after LED_DENY_TOTAL_MS.
    if (elapsed >= LED_DENY_TOTAL_MS) {
        led_write(false);
        s_fb = Fb::None;
        return;
    }
    const uint32_t phase = elapsed / LED_DENY_HALF_MS;  // 0 .. 2*BLINKS-1
    led_write((phase % 2u) == 0u);
}

// ---------------------------------------------------------------------------
// Standalone indicator modes (provisioning / fatal) — direct, not ticked
// ---------------------------------------------------------------------------
void led_provisioning_blink() {
    static uint32_t last = 0;
    static bool on = false;
    if (millis() - last > 500) {
        last = millis();
        on = !on;
        led_write(on);
    }
}

void led_fatal_blink() {
    led_write(true);
}
