#include "rtc.h"
#include "../config.h"
#include <Arduino.h>
#include <Wire.h>
#include <RTClib.h>

static RTC_DS3231 rtc;
static bool rtc_ok = false;

bool rtc_init() {
    if (!rtc.begin(&Wire)) {
        rtc_ok = false;
        return false;
    }
    if (rtc.lostPower()) {
        // Don't silently overwrite — leave time as-is and rely on time_sync operation.
        Serial.println("[RTC] DS3231 reports power loss; awaiting TIME_SYNC");
    }
    rtc_ok = true;
    return true;
}

uint64_t rtc_now() {
    if (!rtc_ok) return 0;
    DateTime n = rtc.now();
    return (uint64_t)n.unixtime();
}

bool rtc_set(uint64_t epoch) {
    if (!rtc_ok) return false;
    rtc.adjust(DateTime((uint32_t)epoch));
    return true;
}
