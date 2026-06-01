#pragma once
#include <stdint.h>

// Загружает immutable-конфиг из NVS namespace "scud_imm" в g_state.
// Возвращает false если устройство не прошито (provisioned_flag != 1).
bool load_immutable_config();

// Пишет всё текущее immutable-состояние в g_state обратно в NVS и выставляет provisioned_flag=1.
bool commit_immutable_config();

// Полная очистка immutable (для RESET в provisioning-режиме).
bool wipe_immutable_config();
