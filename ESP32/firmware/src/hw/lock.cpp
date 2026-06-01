#include "lock.h"
#include "../config.h"
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

static volatile bool s_busy = false;

// Pure strike actuator: drive LOCK_SIGNAL_PIN for duration_ms. The granted LED
// is NO LONGER lit here — optical feedback lives in the LED layer (led_feedback_
// granted), armed from on_access_granted, so swapping this actuator out cannot
// silently kill the indication. lock_is_busy() mirrors led_feedback_active() as
// a sleep-gate predicate for the future power_idle() (docs/13 §3.7).
static void lock_task(void* arg) {
    uint16_t duration_ms = (uint16_t)(uintptr_t)arg;

    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? HIGH : LOW);
    vTaskDelay(duration_ms / portTICK_PERIOD_MS);
    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? LOW : HIGH);

    s_busy = false;
    vTaskDelete(NULL);
}

void lock_init() {
    pinMode(LOCK_SIGNAL_PIN, OUTPUT);
    digitalWrite(LOCK_SIGNAL_PIN, LOCK_SIGNAL_ACTIVE_HIGH ? LOW : HIGH);
}

void lock_trigger_open(uint16_t duration_ms) {
    if (s_busy) return;
    s_busy = true;
    xTaskCreate(lock_task, "lock", 2048, (void*)(uintptr_t)duration_ms, 1, nullptr);
}

bool lock_is_busy() { return s_busy; }
