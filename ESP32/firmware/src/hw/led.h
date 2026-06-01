#pragma once
#include <stdint.h>

void led_init();

// ============================================================================
// Access feedback — non-blocking, tick-driven
// ============================================================================
// One monochrome onboard LED; SUCCESS vs FAIL differ by PATTERN, not colour.
// Armed from the access hooks (integration/access_hooks.cpp), advanced by
// led_tick() from the main loop. Deliberately NOT a detached FreeRTOS task:
// the battery power profile deep-sleeps between taps (docs/13 §3.7) and
// esp_deep_sleep_start() resets the chip, which would kill a task mid-pattern
// and leave the LED stuck. The tick model instead exposes led_feedback_active()
// so power_idle() can gate sleep on it explicitly, and survives light-sleep
// because scheduling is millis()-based.

// Solid ON for window_ms (pass the lock-open duration), then auto-OFF. Decoupled
// from the lock actuator: replacing lock_trigger_open in the hook no longer
// silently kills the granted indication.
void led_feedback_granted(uint32_t window_ms);

// Self-terminating deny pattern (default 3× short blink; see led.cpp).
void led_feedback_deny();

// True while a granted/deny pattern is still animating. power_idle() must AND
// this into its sleep gate so sleep cannot truncate optical feedback.
bool led_feedback_active();

// Advance the feedback pattern. Call once per loop() iteration. millis()-gated,
// never blocks, no-op when idle.
void led_tick();

// ============================================================================
// Standalone indicator modes — mutually exclusive with the main loop
// ============================================================================
void led_provisioning_blink();    // Медленное мигание (вызывать в цикле provisioning).
void led_fatal_blink();           // Solid ON при фатальной ошибке инициализации.
