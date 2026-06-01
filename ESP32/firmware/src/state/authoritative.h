#pragma once
#include <stdint.h>
#include <stddef.h>
#include "../transport/op_sink.h"

// Authoritative state (scud_auth NVS + SPIFFS filter slots).
bool load_authoritative_state();
bool save_time_sync_state_to_nvs();
bool save_filter_meta_to_nvs();
bool save_delivery_record_to_nvs();

// Grab bloom_bytes + whitelist from active flash slot into g_state RAM.
bool load_filter_from_flash();

// Write a full filter_package body (after header+body layout) into inactive slot,
// flip current_slot, persist meta. Assumes signature is already verified.
bool apply_new_filter(const uint8_t* filter_package, size_t total_len,
                      uint64_t filter_version,
                      uint32_t m_bits, uint8_t k_hashes, uint32_t hash_seed,
                      uint64_t generated_at,
                      uint32_t filter_bytes_len,
                      uint16_t whitelist_count,
                      const uint8_t* courier_id,
                      uint64_t received_at);

// ---------------------------------------------------------------------------
// N2/B6 — large-packet receive straight to flash (lift the 16 KB RAM cap).
//
// flash_slot_sink: an op_sink that streams the assembled FILTER_UPDATE package
// directly into the INACTIVE A/B slot file (no 16 KB cap, no second RAM copy).
// The transport layer (NFC transfer.cpp / BLE ble_channel.cpp) feeds chunks via
// the op_sink interface; the handler then back-reads through the same sink to
// verify and parse. The active filter is untouched until the atomic swap, so a
// failed/forged update can never corrupt the live filter.
//
// open : create+truncate the inactive slot, ready for write(). false on FS error.
// close: flush the file handle (call once writing is done; reads still work via
//        a reopened handle inside the sink).
bool flash_slot_sink_open(op_sink* s);
void flash_slot_sink_close(op_sink* s);

// commit_filter_from_flash: the from-flash counterpart of apply_new_filter.
// The package has already been streamed into the inactive slot via the sink.
// Parses the header by reading the slot, validates it (caller passes the
// already-parsed/validated header fields), then:
//   1. verifies the server Ed25519 signature by reading the slot back
//      (verify_filter_sink_sig). On invalid → returns false WITHOUT swapping.
//   2. on valid → applies the blacklist_delta (read from slot), flips the
//      current slot to the freshly written one, persists meta, reloads bloom.
// Returns true only on a valid signature + successful swap+reload.
bool commit_filter_from_flash(op_sink* s,
                              uint64_t filter_version,
                              uint32_t m_bits, uint8_t k_hashes, uint32_t hash_seed,
                              uint64_t generated_at,
                              uint32_t filter_bytes_len,
                              uint16_t whitelist_count,
                              uint16_t bl_delta_count,
                              const uint8_t* courier_id,
                              uint64_t received_at);
