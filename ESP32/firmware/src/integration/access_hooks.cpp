// ============================================================================
// ACCESS actuation hooks — DEFAULT / DEMO KIT
// ============================================================================
//
// This file is meant to be EDITED per deployment. It is the integrator's seam —
// see access_hooks.h for the full contract (when these fire, what is guaranteed,
// and the platform/integrator responsibility split).
//
// The defaults below are a demonstration kit for the reference hardware
// (config.h pin map): on grant, pulse the lock signal (GPIO LOCK_SIGNAL_PIN →
// relay/electric strike) for the configured duration AND give the configured-
// duration "granted" LED feedback; on deny, blink the LED. The two side effects
// are now INDEPENDENT (the LED no longer rides the lock task), so you can swap
// the actuator — turnstile, barrier, buzzer, site-local event — WITHOUT silently
// losing the optical feedback, and WITHOUT touching the platform code that
// verifies ACCESS and calls these.
// ============================================================================

#include "access_hooks.h"
#include "../hw/lock.h"
#include "../hw/led.h"

void on_access_granted(uint16_t lock_duration_ms) {
    // INTEGRATOR: the actuator. Replace lock_trigger_open with your strike /
    // turnstile / barrier / relay. Returns at once (timed in a background task).
    lock_trigger_open(lock_duration_ms);
    // DEFAULT optical feedback — decoupled from the actuator above. Same duration
    // as the lock window ⇒ visually identical to the previous behaviour, but it
    // survives an actuator swap. Non-blocking (ticked from loop via led_tick()).
    led_feedback_granted(lock_duration_ms);
}

void on_access_denied(uint8_t result_code) {
    // DEFAULT KIT: short blink. `result_code` (RES_* in ops.h) is available if your
    // install wants to differentiate (e.g. a louder alarm on a revoked vs a
    // malformed credential) — the demo kit treats all denials the same.
    (void)result_code;
    led_feedback_deny();
}
