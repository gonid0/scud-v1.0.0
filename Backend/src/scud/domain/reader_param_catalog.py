"""Canonical catalog of reader configuration parameters for Phase 1.

This module is the SINGLE SOURCE OF TRUTH for all reader parameter metadata.
It is imported by:
  - The Alembic migration (0007_reader_profiles) to seed param_bounds and profiles.
  - The resolver (reader_config_resolver.py) for bound checks and script generation.
  - API validators.

IMPORTANT: This module must remain import-safe from an Alembic migration context —
it must not import from any scud.* app or DB modules. Pure data only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Hardware classes
# ---------------------------------------------------------------------------

HARDWARE_CLASSES: list[str] = ["esp32_mains_ble", "esp32_battery_nfc"]

# BLE-only params: suppressed in the provisioning script when the hardware class
# is battery/NFC-only OR when the resolved ble_en == 0. They are still stored in
# param_bounds for completeness but omitted from SET-* output.
BLE_ONLY_PARAMS: frozenset[str] = frozenset({"ble_mtu", "ble_idle", "ble_defer"})

# Security-sensitive parameters — subject to extra server-side ceiling checks.
SECURITY_PARAMS: frozenset[str] = frozenset({"skew", "nonce_ttl", "ts_drift", "ts_boot"})


# ---------------------------------------------------------------------------
# Parameter spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """Metadata for a single reader configuration parameter.

    Attributes
    ----------
    key:
        Short snake_case identifier used everywhere internally.
    set_command:
        The SET-* command token sent to the firmware over serial.
        None for ble_en (which emits BLE-ENABLE / BLE-DISABLE instead).
    default:
        Firmware default value.
    min_value:
        Absolute minimum accepted by firmware.
    max_value:
        Absolute maximum accepted by firmware.
    value_kind:
        'uint' | 'bool' | 'enum'
    server_floor:
        Optional server-imposed lower bound (stricter than firmware min).
    server_ceiling:
        Optional server-imposed upper bound (stricter than firmware max).
        Primarily used for security parameters to prevent unsafe relaxation.
    """

    key: str
    set_command: Optional[str]          # None => ble_en special case
    default: int
    min_value: int
    max_value: int
    value_kind: str = "uint"            # 'uint' | 'bool' | 'enum'
    server_floor: Optional[int] = None
    server_ceiling: Optional[int] = None


# ---------------------------------------------------------------------------
# Canonical parameter list (26 rows, catalog order)
# ---------------------------------------------------------------------------
# Verified against ESP32/firmware/src/config.h and state/reader_config.cpp CFG[].
# Security server ceilings (policy knobs):
#   skew:      firmware max=600 → server_ceiling=300
#   ts_drift:  firmware max=60  → server_ceiling=30
#   nonce_ttl: no server ceiling (= firmware max 60000)
#   ts_boot:   no server ceiling (= firmware max 604800)

PARAMS: list[ParamSpec] = [
    # key              set_command                  default  min    max    kind    s_floor  s_ceil
    ParamSpec("skew",       "SET-CLOCK-SKEW",           60,    5,    600,  "uint",   None,   300),
    ParamSpec("nonce_ttl",  "SET-NONCE-TTL",         10000, 2000,  60000,  "uint",   None,  None),
    ParamSpec("nfc_dl",     "SET-NFC-DEADLINE",       8000, 3000,  30000,  "uint",   None,  None),
    ParamSpec("ble_idle",   "SET-BLE-IDLE",          30000, 5000, 300000,  "uint",   None,  None),
    ParamSpec("ts_drift",   "SET-TSYNC-DRIFT",          10,    1,     60,  "uint",   None,    30),
    ParamSpec("ts_boot",    "SET-TSYNC-BOOTSTRAP",   86400, 3600, 604800,  "uint",   None,  None),
    ParamSpec("psg_dir",    "SET-PASSAGE-DIRECTION",     1,    1,      2,  "enum",   None,  None),
    ParamSpec("wl_max",     "SET-WHITELIST-MAX",       256,   32,   2048,  "uint",   None,  None),
    ParamSpec("bld_max",    "SET-BL-DELTA-MAX",        256,   32,   2048,  "uint",   None,  None),
    ParamSpec("cd_end",     "SET-COOLDOWN-END",       3000,  500,  10000,  "uint",   None,  None),
    ParamSpec("cd_grant",   "SET-COOLDOWN-GRANT",     4500, 1000,  15000,  "uint",   None,  None),
    ParamSpec("pn_retry",   "SET-PN532-RETRY",          16,    1,    255,  "uint",   None,  None),
    ParamSpec("rf_atr",     "SET-RF-ATR",               15,   10,     15,  "uint",   None,  None),
    ParamSpec("rf_retry",   "SET-RF-RETRY",             15,   10,     15,  "uint",   None,  None),
    ParamSpec("fld_pause",  "SET-FIELD-PAUSE",          80,   50,    500,  "uint",   None,  None),
    ParamSpec("pi_retry",   "SET-PUSH-RETRIES",          3,    1,     10,  "uint",   None,  None),
    ParamSpec("pi_delay",   "SET-PUSH-DELAY",           40,   10,    500,  "uint",   None,  None),
    ParamSpec("reinit",     "SET-REINIT-THRESHOLD",      3,    1,     20,  "uint",   None,  None),
    ParamSpec("nfc_det",    "SET-NFC-DETECT",          100,   20,    500,  "uint",   None,  None),
    ParamSpec("ble_mtu",    "SET-BLE-MTU",             247,  243,    517,  "uint",   None,  None),
    ParamSpec("ble_defer",  "SET-BLE-INFO-DEFER",       50,   10,    500,  "uint",   None,  None),
    ParamSpec("bl_cap",     "SET-BLACKLIST-CAP",       256,   16,    256,  "uint",   None,  None),
    ParamSpec("nonce_ring", "SET-NONCE-RING",            8,    2,      8,  "uint",   None,  None),
    ParamSpec("ho_req",     "SET-HANDOVER-REQUIRED",     0,    0,      1,  "bool",   None,  None),
    ParamSpec("lock_dur",   "SET-LOCK-DURATION",      3000,  500,  10000,  "uint",   None,  None),
    # ble_en: no SET-* command; emits BLE-ENABLE or BLE-DISABLE
    ParamSpec("ble_en",     None,                        1,    0,      1,  "bool",   None,  None),
]

# Index for fast lookup by key
PARAMS_BY_KEY: dict[str, ParamSpec] = {p.key: p for p in PARAMS}

# Ordered keys list (catalog order)
PARAM_KEYS: list[str] = [p.key for p in PARAMS]


# ---------------------------------------------------------------------------
# Seed profiles
# ---------------------------------------------------------------------------

@dataclass
class ProfileSeed:
    name: str
    hardware_class: str
    description: str
    params: dict[str, int] = field(default_factory=dict)


SEED_PROFILES: list[ProfileSeed] = [
    ProfileSeed(
        name="entrance",
        hardware_class="esp32_mains_ble",
        description="Main entrance reader — entry direction, full whitelist, BLE enabled.",
        params={
            "psg_dir":    1,
            "bl_cap":     256,
            "nonce_ring": 8,
            "wl_max":     1024,
            "bld_max":    1024,
            "ble_en":     1,
            "ble_idle":   15000,
        },
    ),
    ProfileSeed(
        name="interior_room",
        hardware_class="esp32_mains_ble",
        description="Interior room reader — exit direction, smaller whitelist, shorter cooldowns.",
        params={
            "psg_dir":  2,
            "bl_cap":   128,
            "nonce_ring": 4,
            "wl_max":   256,
            # Short interior lock + snappy cooldowns. lock_dur is set so the
            # cd_grant >= lock_dur + 1500 invariant holds without auto-raise
            # (2500 >= 1000 + 1500), keeping the "snappy" intent intact.
            "lock_dur": 1000,
            "cd_end":   1500,
            "cd_grant": 2500,
        },
    ),
    ProfileSeed(
        name="sensitive_room",
        hardware_class="esp32_mains_ble",
        description=(
            "Sensitive room — NFC handover required, BLE disabled, "
            "tightened clock-skew and nonce TTL."
        ),
        params={
            "ho_req":   1,
            "ble_en":   0,
            "skew":     15,
            "nonce_ttl": 4000,
        },
    ),
    ProfileSeed(
        name="battery_nfc_only",
        hardware_class="esp32_battery_nfc",
        description="Battery-powered NFC-only reader — BLE suppressed, longer NFC detect.",
        params={
            "ble_en":   0,
            "nfc_det":  300,
            "fld_pause": 200,
        },
    ),
]
