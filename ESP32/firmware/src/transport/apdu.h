#pragma once
#include <stdint.h>
#include <stddef.h>

bool apdu_init();

// Опрос PN532 на HCE-цель + SELECT AID. Возвращает true если активировано.
bool apdu_detect_target(uint32_t timeout_ms);

// Обмен APDU: возвращает длину ответа (0 при ошибке/разрыве).
// resp_max должен быть ≥ 260.
size_t apdu_exchange(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max);

// Сбросить RF-поле PN532 (off→on) — принуждает активный HCE-таргет
// деактивироваться. Вызывать при аварийном завершении tap-сессии, чтобы
// телефонный HCE-сервис получил onDeactivated и подготовился к новому тапу.
void apdu_reset_field();

// Просто выключить поле (без автоматического включения обратно). Использовать
// при штатном END + cooldown, чтобы телефон увидел деактивацию и не вёл
// повторную активацию до конца cooldown-окна. Поле поднимется на следующем
// apdu_detect_target автоматически (InListPassiveTarget auto-turns-on).
void apdu_field_off();

// Полная переинициализация PN532 (SAMConfig + таймауты). Вызывать, когда
// чип залип в bad state (каскад status=0x0B/0x13/0x01).
// Возвращает true, если PN532 отвечает; false — требуется power-cycle.
bool apdu_reinit_pn532();
