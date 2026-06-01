#pragma once
#include <Preferences.h>

// Phase 1: table-driven load/commit/SET/STATUS for the ~21 per-reader
// operational parameters in g_state.cfg (ReaderConfig, reader_state.h).
//
// Single source of truth is the CFG[] table in reader_config.cpp: each row maps
// an NVS key (≤15 chars) + a SET-* command + a uint32_t ReaderConfig member to
// its config.h default and clamp range. Defaults equal the config.h consts, so
// an unprovisioned / already-flashed reader (NVS key absent) is byte-identical.

// Fill every cfg field with its config.h default (no NVS access). Call once at
// boot so g_state.cfg is valid even on a brand-new reader whose scud_imm
// namespace does not exist yet (read-only Preferences.begin would fail).
void reader_config_defaults();

// Load every cfg field from NVS, defaulting to its config.h const when absent.
// Same Preferences instance / namespace (scud_imm) as lock_dur / ble_en.
void reader_config_load(Preferences& p);

// Persist every cfg field to NVS (called from commit_immutable_config path).
void reader_config_commit(Preferences& p);

// If cmd matches a SET-* command: parse uint32 from arg, clamp to [min,max],
// store into the field, print "OK", return true. Returns false if no match
// (caller falls through to its own dispatch / unknown-command handling).
bool reader_config_set(const char* cmd, const char* arg);

// Print every key=value (used by STATUS).
void reader_config_status();
