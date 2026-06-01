#include "apdu.h"
#include "../config.h"
#include "../state/reader_state.h"
#include <Arduino.h>
#include <PN532.h>
#include <PN532_HSU.h>

// Используется UART2 на ESP32 как HSU-линк к PN532.
static HardwareSerial pn532_serial(PN532_UART_NUM);
static PN532_HSU pn532_hsu(pn532_serial);
static PN532 nfc(pn532_hsu);

bool apdu_init() {
    pn532_serial.begin(PN532_UART_BAUD, SERIAL_8N1, PN532_UART_RX_PIN, PN532_UART_TX_PIN);
    nfc.begin();

    uint32_t v = nfc.getFirmwareVersion();
    if (!v) {
        Serial.println("[PN532] firmware not found — check SEL0/SEL1=0 (HSU), wiring, 5V power");
        return false;
    }
    Serial.printf("[PN532] PN5%02X firmware %u.%u\n",
                  (uint8_t)((v >> 24) & 0xFF),
                  (uint8_t)((v >> 16) & 0xFF),
                  (uint8_t)((v >> 8)  & 0xFF));

    nfc.SAMConfig();
    nfc.setPassiveActivationRetries((uint8_t)g_state.cfg.pn532_retries);
    // Поднять таймаут ответа target'а (Non-DEP/T=CL). Дефолт fRetry=0x0A (~102ms)
    // слишком короток для Android HCE, который отвечает после DB-queries + crypto.
    // 0x0F = 3.28s — с большим запасом, не влияет на скорость при нормальных тапах.
    bool tok = nfc.setRfTimings(/*fATR_RES=*/(uint8_t)g_state.cfg.rf_atr, /*fRetry=*/(uint8_t)g_state.cfg.rf_retry);
    Serial.printf("[PN532] setRfTimings: %s\n", tok ? "OK" : "FAIL");
    return true;
}

bool apdu_detect_target(uint32_t timeout_ms) {
    uint8_t uid[10];
    uint8_t uid_len = 0;

    bool found = nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uid_len, (uint16_t)timeout_ms);
    if (!found) return false;

    static const uint8_t SELECT_AID[] = {
        0x00, 0xA4, 0x04, 0x00,
        AID_LEN,
        0xF0, 0x53, 0x43, 0x55, 0x44, 0x01,
        0x00
    };
    uint8_t response[SELECT_AID_RESP_BUF];
    uint8_t resp_len = sizeof(response);

    if (!nfc.inDataExchange((uint8_t*)SELECT_AID, sizeof(SELECT_AID), response, &resp_len)) return false;
    if (resp_len < 2) return false;
    return response[resp_len - 2] == 0x90 && response[resp_len - 1] == 0x00;
}

size_t apdu_exchange(const uint8_t* req, size_t req_len, uint8_t* resp, size_t resp_max) {
    uint8_t rl = resp_max < 255 ? (uint8_t)resp_max : 255;
    bool ok = nfc.inDataExchange((uint8_t*)req, (uint8_t)req_len, resp, &rl);
    return ok ? (size_t)rl : 0;
}

void apdu_reset_field() {
    // Полный сброс: выключить поле → пауза → включить обратно.
    // Пауза >=50 ms обязательна, иначе телефонный модем не регистрирует деактивацию.
    nfc.setRFField(0x00, 0x00);
    delay(g_state.cfg.field_pause);
    nfc.setRFField(0x02, 0x01);
    // После включения SAMConfig не требуется — он сохраняется в PN532.
}

void apdu_field_off() {
    nfc.setRFField(0x00, 0x00);
}

bool apdu_reinit_pn532() {
    Serial.println("[PN532] reinit requested");
    // Мягкий путь: sleep-wakeup + полная переконфигурация.
    nfc.begin();
    uint32_t v = nfc.getFirmwareVersion();
    if (!v) {
        Serial.println("[PN532] reinit failed — no firmware response");
        return false;
    }
    nfc.SAMConfig();
    nfc.setPassiveActivationRetries((uint8_t)g_state.cfg.pn532_retries);
    nfc.setRfTimings((uint8_t)g_state.cfg.rf_atr, (uint8_t)g_state.cfg.rf_retry);
    Serial.println("[PN532] reinit OK");
    return true;
}
