#pragma once
#include <stdint.h>

// ============================================================================
// ACCESS actuation — INTEGRATOR ADAPTATION POINT
// ============================================================================
//
// These two functions are the single place where "what physically happens on an
// access decision" lives. They are the supported extension seam for anyone
// deploying a SCUD reader into their own installation (electric strike, mag-lock,
// turnstile, barrier, buzzer, external relay board, site-specific logging, …).
//
// RESPONSIBILITY SPLIT
// --------------------
//   PLATFORM (we own — do NOT change to adapt a deployment):
//     - Verifying the ACCESS operation: all 7 cryptographic/policy checks in
//       op_access (nonce/anti-replay, reader_id, time, blacklist, bloom+whitelist,
//       server signature, phone signature). A hook is reached ONLY after a verdict
//       is produced.
//     - Calling these hooks correctly: exactly once per verdict, from the reader
//       MAIN LOOP (never from an ISR or a NimBLE host-task callback), race-free
//       w.r.t. g_state (invariant B3), and cooldown-gated so a phone left in the
//       field cannot re-trigger a grant while a previous one is still cooling down.
//     - Anti-retap cooldown timing (cd_grant / cd_end, derived from per-reader
//       config + lock_duration_ms). Stays in apply_access_result, NOT here.
//
//   INTEGRATOR (you own — edit access_hooks.cpp for your install):
//     - What the side-effect actually is: which GPIO/relay/strike to drive, for
//       how long, what to blink, what to log, etc.
//
// In short: the platform guarantees these hooks fire at the right moment with a
// trustworthy decision; the deployment decides what they do.
//
// CONTRACT / GUARANTEES
// ---------------------
//   on_access_granted(lock_duration_ms):
//     - Reached only when ACCESS passed ALL checks: valid, signed by both the
//       issuing server and the presenting phone, not expired, not revoked
//       (blacklist + bloom/whitelist), with a fresh single-use nonce. It is safe
//       to open the door here — the platform has already authorized it.
//     - `lock_duration_ms` is the per-reader-configured strike duration
//       (DEFAULT_LOCK_SIGNAL_DURATION_MS, clamped [LOCK_DURATION_MIN_MS ..
//       LOCK_DURATION_MAX_MS], set via provisioning SET-LOCK-DURATION). Honour it
//       if your actuator has a duration; ignore it if not.
//     - Called on the main loop. MUST return promptly — do NOT block for the whole
//       actuation. Spawn a background FreeRTOS task for anything timed (the default
//       kit does exactly this via lock_trigger_open).
//
//   on_access_denied(result_code):
//     - Reached on every non-OK verdict. `result_code` is the RES_* reason
//       (see ops.h: RES_EXPIRED, RES_REVOKED_BLACKLIST, RES_BAD_SIGNATURE, …) so a
//       deployment can react differently per reason (e.g. distinct alarm on a
//       revoked vs malformed credential). Default kit ignores it (one red blink).
//     - Same timing rules: main loop, return promptly.
//
// NOTE ON COOLDOWN: the grant cooldown is derived from lock_duration_ms. If your
// actuation runs LONGER than lock_duration_ms (e.g. a slow barrier), raise
// lock_duration_ms (or cd_grant) via provisioning so the anti-retap window covers
// the full motion — the platform won't re-fire on_access_granted while cooling down.
//
// The bodies in access_hooks.cpp are the DEFAULT / DEMO KIT (open the electric
// lock + blink the onboard LED). Replace them for a real installation.
// ============================================================================

// Fired once when an ACCESS verdict is GRANTED. `lock_duration_ms` is the
// configured strike duration. See contract above.
void on_access_granted(uint16_t lock_duration_ms);

// Fired once when an ACCESS verdict is DENIED. `result_code` is the RES_* reason
// (ops.h). See contract above.
void on_access_denied(uint8_t result_code);
