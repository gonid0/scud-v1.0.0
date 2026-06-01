#pragma once
#include <stdint.h>

void lock_init();

// Запускает открытие замка в фоне (FreeRTOS task): выставить сигнал,
// задержка duration_ms, снять сигнал. Возврат — немедленный.
void lock_trigger_open(uint16_t duration_ms);

bool lock_is_busy();
