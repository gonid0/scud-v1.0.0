#pragma once
#include <stdint.h>

bool rtc_init();
uint64_t rtc_now();           // unix seconds, 0 if RTC not available
bool rtc_set(uint64_t epoch); // seconds since 1970
