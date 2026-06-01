#pragma once
#include <stdint.h>

// 16 B ASCII + \x00 padding (shared protocol §2.3).
extern const uint8_t DOMAIN_KEY[16];
extern const uint8_t DOMAIN_INF[16];
extern const uint8_t DOMAIN_RSP[16];
extern const uint8_t DOMAIN_FLT[16];
extern const uint8_t DOMAIN_RCP[16];
extern const uint8_t DOMAIN_BLK[16];
extern const uint8_t DOMAIN_FDI[16];
extern const uint8_t DOMAIN_TGR[16];
extern const uint8_t DOMAIN_TIM[16];
extern const uint8_t DOMAIN_REV[16];
extern const uint8_t DOMAIN_PSG[16];   // passage_receipt (shared §15)
extern const uint8_t DOMAIN_BLE[16];   // BLE session_token (shared §17)
