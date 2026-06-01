#pragma once

// =========================================================================
// PN532 через UART (HSU, High Speed UART)
// ESP32 UART2: TX2=GPIO17 → PN532 RX, RX2=GPIO16 ← PN532 TX.
// На PN532-модуле перемычки SEL0=0, SEL1=0 (HSU).
// =========================================================================
#define PN532_UART_NUM     2
#define PN532_UART_RX_PIN  16
#define PN532_UART_TX_PIN  17
#define PN532_UART_BAUD    115200

// =========================================================================
// DS3231 через I2C (Wire)
// =========================================================================
#define I2C_SDA_PIN        21
#define I2C_SCL_PIN        22
#define I2C_FREQ           400000

// Lock control
#define LOCK_SIGNAL_PIN            26
#define LOCK_SIGNAL_ACTIVE_HIGH    true

// Встроенный светодиод на плате (ESP32 DevKit — синий на GPIO2).
// Один одноцветный → GRANTED/DENIED/idle различаем паттерном мигания.
#define LED_ONBOARD_PIN        2
#define LED_ONBOARD_ACTIVE_HIGH true

// Provisioning button
#define PROVISIONING_BUTTON_PIN   0

// Buffer sizes
#define TRANSFER_BUFFER_CAP        16384     // 16 KB
#define NONCE_RING_SIZE            8
#define NONCE_TTL_MS               10000
#define LOCAL_BLACKLIST_CAP        256

// BLE: разрываем соединение без активности дольше этого (§16.6 idle-watchdog).
#define BLE_IDLE_TIMEOUT_MS        30000

// NFC tap-сессия: жёсткий дедлайн, чтобы зависший/медленный телефон не держал
// единственный поток ридера бесконечно (голодают BLE-tick и cleanup'ы).
#define NFC_SESSION_DEADLINE_MS    8000

// Lock defaults (overridable via provisioning)
#define DEFAULT_LOCK_SIGNAL_DURATION_MS  3000
// Provisioning clamp for lock_duration_ms (SET-LOCK-DURATION). Floor stops a 0 ms
// no-op (strike never releases → lockout); ceiling stops the door being held open
// indefinitely. Enforced in serial_cmd.cpp (set) AND immutable.cpp (load).
#define LOCK_DURATION_MIN_MS             500
#define LOCK_DURATION_MAX_MS             10000

// Protocol
#define PROTOCOL_VERSION           1
// NB: max 240 — ограничение из-за uint8_t responseLength в PN532 HAL (255),
// + 12-байтовый заголовок chunked-ответа + 2 байта SW. 240 даёт запас и
// полностью укладывается в PN532 FSC=256 без обрезания.
#define MAX_APDU_DATA_SIZE         240

// Filter limits
#define MAX_FILTER_BYTES           (128 * 1024 - 1024)

// H4 Option B' — BLE big-FILTER_UPDATE loop-drained SPSC ring. onWrite (host-task)
// stages each reassembled PDU body into a fixed ring of small records; loopTask
// drains them into the inactive flash slot (single flash writer = B3 invariant).
// No full-file RAM copy → BLE filter rises from the old 24 KB cap to the flash
// ceiling (~120 KB real on the 256 KB A/B partition; see MAX_FILTER_BYTES note).
#define BLE_RING_REC_CAP      244     // >= negotiated maxPdu(mtu-3) - 2-byte header
#define BLE_RING_DEPTH        64      // records; ~15.9 KB static (< old 24 KB scratch)
#define DRAIN_PDUS_PER_TICK   8       // ring records drained per ble_loop_tick

// Clock skew tolerance (seconds)
#define CLOCK_SKEW_SECONDS         60

// NFC→BLE handover_token TTL (seconds, shared §17.1 / plan 08 §4.3). The token
// issued at the NFC tap is valid only for this window; the phone must present it
// over BLE within HANDOVER_TOKEN_TTL_S of issue (expires_at = issued_at + this).
#define HANDOVER_TOKEN_TTL_S       60

// SPIFFS file names for filter slots
#define FILTER_FILE_A              "/filter_A.bin"
#define FILTER_FILE_B              "/filter_B.bin"
#define FILTER_CURRENT_FILE        "/filter_current.txt"

// AID префикс + "SCUD" + version
#define AID_BYTES                  { 0xF0, 0x53, 0x43, 0x55, 0x44, 0x01 }
#define AID_LEN                    6

// =========================================================================
// Phase 2 parameterization: named constants for previously-inline literals.
// Values are behaviour-preserving (verified against the source line cited).
// =========================================================================

// --- Loop / boot timing (main.cpp) ---
#define SERIAL_CONSOLE_BAUD        115200   // Serial.begin()
#define BOOT_SETTLE_DELAY_MS       200      // post-Serial.begin settle
#define FATAL_HALT_DELAY_MS        1000     // spin delay in fatal-halt loop
#define PROVISIONING_LOOP_DELAY_MS 10       // provisioning_loop() tick
#define MAIN_LOOP_DELAY_MS         10       // loop() tick
// Task-WDT backstop: reboot if loop() (NFC session / BLE drain) wedges past this.
// Must exceed the worst legitimate single-iteration block (8 s NFC_SESSION_DEADLINE
// + flash/op time) with margin, so it fires ONLY on a genuine hang, not a long tap.
#define TASK_WDT_TIMEOUT_S         15       // esp_task_wdt timeout (s)
#define NFC_DETECT_TIMEOUT_MS      100      // apdu_detect_target() poll timeout

// --- Provisioning serial CLI (provisioning/serial_cmd.cpp) ---
#define PROV_LINE_BUF_SIZE         256      // line_buf[]

// --- NVS namespaces / storage (state/*.cpp) ---
#define NVS_NS_IMMUTABLE           "scud_imm"
#define NVS_NS_AUTH                "scud_auth"
#define NVS_NS_LOCAL               "scud_local"
#define DEFAULT_BLE_ENABLED        1        // getUChar("ble_en", <default>) — B8: on

// --- Filter SPIFFS partition + slots (state/authoritative.cpp) ---
#define FILTER_PARTITION_LABEL     "filter" // partitions.csv label
#define FILTER_FS_BASE_PATH        "/filter"
#define FILTER_FS_MAX_OPEN_FILES   10
#define FILTER_SLOT_A              'A'
#define FILTER_SLOT_B              'B'
#define DEFAULT_FILTER_SLOT        "A"      // getString("cur_slot", <default>)

// --- Local blacklist NVS key naming (state/local.cpp) ---
#define NVS_KEYPREFIX_BL_KEY       "k_%u"   // per-entry key_id key
#define NVS_KEYPREFIX_BL_META      "m_%u"   // per-entry meta key
#define NVS_BL_KEYNAME_BUF         12       // snprintf buffer for the above

// --- TIME_SYNC soft-policy windows (ops/time_sync.cpp) — security sensitive ---
#define RTC_DEAD_SENTINEL              0      // rtc_now() == 0 means DS3231 dead/unset
#define TIME_SYNC_BOOTSTRAP_WINDOW_S   86400  // 24h window for the first SOFT sync
#define TIME_SYNC_SECONDS_PER_DAY      86400  // elapsed→days divisor (named to avoid RTClib's SECONDS_PER_DAY clash)
#define TIME_SYNC_MIN_WINDOW_DAYS      1      // floor for elapsed-days
#define TIME_SYNC_DRIFT_S_PER_DAY      10     // allowed drift seconds per day

// --- passage_receipt reader-policy defaults (ops/passage.cpp) ---
#define PASSAGE_DEFAULT_DIRECTION   0x01     // receipt[57] direction = entry
#define PASSAGE_VERDICT_OK          0x00     // receipt[58] verdict OK
#define PASSAGE_FLAG_REAL_PASS      0x01     // receipt[63] flags bit0 = real pass

// --- REVOKE_KEY blacklist reason (ops/revoke_key.cpp) ---
#define BL_REASON_BY_USER           0x01     // blacklist_append() reason

// --- FILTER_UPDATE limits (ops/filter_update.cpp) ---
#define FLT_RESP_MIN                160      // minimum resp_max to proceed
#define MAX_WHITELIST_COUNT         256      // whitelist_count upper bound
#define MAX_BL_DELTA_COUNT          256      // bl_delta_count upper bound

// --- PN532 / NFC interop tuning (transport/apdu.cpp) ---
#define PN532_PASSIVE_RETRIES       0x10     // setPassiveActivationRetries()
#define PN532_RF_TIMING_ATR         0x0F     // setRfTimings fATR_RES (~3.28s)
#define PN532_RF_TIMING_RETRY       0x0F     // setRfTimings fRetry
#define FIELD_RESET_PAUSE_MS        80       // RF-field off→on pause (>=50ms req.)
#define SELECT_AID_RESP_BUF         32       // response[] for SELECT-AID exchange

// --- NFC transfer buffers / tuning (transport/transfer.cpp) ---
#define APDU_RX_BUF                 260      // g_apdu_rx[]
#define APDU_TX_BUF                 300      // g_apdu_tx[]
#define RESULT_CODE_UNPARSED        0xFE     // extract_result_code() sentinel
#define PUSH_INFO_RETRIES           3        // send_push_info() attempts
#define PUSH_INFO_RETRY_DELAY_MS    40       // pause between PUSH_INFO retries
#define READ_CHUNK_RETRIES          3        // N3: re-request same offset on RF glitch before abort
#define PN532_FAIL_REINIT_THRESHOLD 3        // consecutive failures → reinit
#define RESULT_BUF_CAP              8704      // worst-case BLK result buffer
#define PREV_INLINE_BUF             (TRANSFER_BUFFER_CAP / 64)  // prev_buf[] inline cap (~256)
#define PREV_INLINE_MAX             252       // result_len threshold: inline vs PUSH_CHUNK
#define COOLDOWN_AFTER_END_MS       3000      // cooldown after clean END
#define COOLDOWN_AFTER_GRANT_MS     4500      // cooldown after ACCESS GRANTED
#define COOLDOWN_GRANT_MARGIN_MS    1500      // margin added over lock_duration

// --- BLE channel (ble/ble_channel.cpp, ble/ble_frame.cpp) ---
#define BLE_REQUESTED_MTU           247      // NimBLEDevice::setMTU()
#define BLE_MAX_PDU                 240      // max on-wire PDU payload
#define BLE_INFO_DEFER_MS           50       // defer INFO push after connect
#define BLE_CONN_HANDLE_NONE        0xFFFF   // "no active central" sentinel
#define BLE_DEVICE_NAME_PREFIX      "SCUD-"  // advertised device-name prefix
#define BLE_DEVICE_NAME_BUF         16       // device_name[]
#define BLE_PDU_STAGE_CAP           256      // ble_frame.cpp pdu[] staging buffer

// --- BLE advertising interval (power: intermittent / AirTag-style) ---
// Units of 0.625 ms (BLE spec). Larger interval => lower average radio power at
// the cost of slower discovery by the phone scanner. ~1000-1500 ms is a
// battery-friendly idle cadence vs the NimBLE fast default (~20-40 ms): it cuts
// idle adv-events ~30-50x. Trade-off: the phone takes up to ~1.5 s to find the
// reader. For a mains-powered fast-discovery reader, set both small (e.g. 48 =
// 30 ms). TODO(power): make per-reader (loop_delay_ms-class knob, docs/10 §2.3).
#define BLE_ADV_MIN_INTERVAL_UNITS  1600     // 1600 * 0.625ms = 1000 ms
#define BLE_ADV_MAX_INTERVAL_UNITS  2400     // 2400 * 0.625ms = 1500 ms

// --- BLE advertisement capability flags (X2, §16.2) ---
// Published in the advertisement manufacturer-data (NOT signed, NOT in the
// conformance corpus). The phone reads these from the scan record to route bulk
// ops to BLE while keeping proximity ops on NFC (see TransportRouter / §16.2).
#define BLE_CAP_BULK                0x01     // bit0: reader accepts bulk ops over BLE (BLE channel up)
#define BLE_CAP_HANDOVER            0x02     // bit1: reader supports NFC→BLE handover (T3, plan 08 §4.3)

// --- T3 handover gate default (reader_config.cpp CFG[] table) ---
// 0 = no gate (default; existing BLE-filter path unchanged). 1 = require a prior
// verified NFC→BLE handover for INNER_FILTER_UPDATE over BLE (fail-closed).
#define HANDOVER_REQUIRED_DEFAULT   0
