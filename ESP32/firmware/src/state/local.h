#pragma once
#include <stdint.h>

// Local blacklist + nonce ring helpers.

bool load_local_state();
bool save_local_blacklist_to_nvs();

// Append a new key_id to local_blacklist (idempotent). Returns false if full.
bool blacklist_append(const uint8_t key_id[16], uint64_t revoked_at, uint64_t expires_at, uint8_t reason);

// Returns index if found, else -1.
int  blacklist_find(const uint8_t key_id[16]);

// Remove by key_id if present (used when filter_delta absorbs them).
void blacklist_remove(const uint8_t key_id[16]);

// Drop entries whose expires_at < now_sec. Re-saves NVS if anything dropped.
void expire_local_blacklist_entries();

// Nonce ring.
void issue_nonce(const uint8_t nonce[32]);
bool consume_nonce(const uint8_t nonce[32]);
void cleanup_stale_nonces();
