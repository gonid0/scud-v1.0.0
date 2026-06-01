"""Phase 1 resolver: compute the final provisioning parameters for a reader.

resolve_reader_params(session, reader) -> ResolvedConfig

Algorithm (exact, per spec):
  1. base = param_bounds.default_value for reader.hardware_class.
  2. overlay reader_profile_param for reader.profile_id (if set).
  3. overlay reader_param_override for the reader.
  4. Clamp to [server_floor ?? min_value .. server_ceiling ?? max_value].
  5. Direction-Lock (security downgrade): if the profile is a security profile
     (ho_req==1 or ble_en==0 in the profile's own params), and an override
     tries to downgrade a security gate (ho_req 1->0, ble_en 0->1), IGNORE
     the downgrade and append a warning. Does not raise.
  6. Cross-field invariants:
     - cd_grant >= lock_dur + 1500  (raise cd_grant if needed, clamp to max, warn)
     - ble_mtu >= 243               (clamp up, warn)
     (lock_dur is bounded to firmware [500, 10000] by the Step-4 bounds clamp.)
  7. BLE-row suppression: if hardware_class=='esp32_battery_nfc' OR ble_en resolved
     to 0, drop ble_mtu / ble_idle / ble_defer from .script. For ble_en itself,
     always emit BLE-DISABLE in those cases (regardless of the stored value).
  8. Emit .script in catalog order; ble_en emits BLE-ENABLE / BLE-DISABLE;
     all others emit (set_command, str(value)).

The resolver is the ONLY place security policy lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import (
    AdminAuditLog,
    ParamBounds,
    Reader,
    ReaderParamOverride,
    ReaderProfileParam,
)
from scud.domain.reader_param_catalog import (
    BLE_ONLY_PARAMS,
    HARDWARE_CLASSES,
    PARAMS,
    PARAMS_BY_KEY,
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ResolvedConfig:
    """Result of resolve_reader_params.

    Attributes
    ----------
    params:
        Ordered dict param_key -> final clamped integer value (all 26 params).
    script:
        Ordered list of (command, arg | None) tuples ready for serial playback.
        ble_en emits ("BLE-ENABLE", None) or ("BLE-DISABLE", None).
        All others emit (set_command, str(value)).
    warnings:
        Human-readable audit notes (security downgrades ignored, auto-adjustments).
    """
    params: dict[str, int] = field(default_factory=dict)
    script: list[tuple[str, str | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

async def resolve_reader_params(
    session: AsyncSession,
    reader: Reader,
    api_key=None,  # Optional; if provided, security-downgrade warnings are audit-logged
) -> ResolvedConfig:
    """Compute the final provisioning parameters for *reader*.

    Parameters
    ----------
    session:
        Active async DB session (used for read-only lookups).
    reader:
        ORM Reader instance.  reader.hardware_class and reader.profile_id are
        read; the session is used to load profile params and per-reader overrides.
    api_key:
        If supplied, security-downgrade audit events are written to AdminAuditLog
        (flushed but not committed — caller commits).
    """
    hw_class = reader.hardware_class

    # ------------------------------------------------------------------
    # Step 1 — load param_bounds for the hardware class
    # ------------------------------------------------------------------
    result = await session.execute(
        select(ParamBounds).where(ParamBounds.hardware_class == hw_class)
    )
    bounds_rows: dict[str, ParamBounds] = {r.param_key: r for r in result.scalars().all()}

    # Build base values from defaults
    params: dict[str, int] = {}
    for p in PARAMS:
        if p.key in bounds_rows:
            params[p.key] = bounds_rows[p.key].default_value
        else:
            # Fallback to catalog default if bounds row missing (shouldn't happen)
            params[p.key] = p.default

    # ------------------------------------------------------------------
    # Step 2 — overlay profile params (if profile assigned)
    # ------------------------------------------------------------------
    profile_params: dict[str, int] = {}
    if reader.profile_id is not None:
        result = await session.execute(
            select(ReaderProfileParam).where(
                ReaderProfileParam.profile_id == reader.profile_id
            )
        )
        for row in result.scalars().all():
            profile_params[row.param_key] = row.value
            params[row.param_key] = row.value

    # ------------------------------------------------------------------
    # Step 3 — overlay per-reader overrides
    # ------------------------------------------------------------------
    result = await session.execute(
        select(ReaderParamOverride).where(
            ReaderParamOverride.reader_id == reader.reader_id
        )
    )
    override_rows: dict[str, int] = {r.param_key: r.value for r in result.scalars().all()}

    # Determine if this is a security profile
    is_security_profile = (
        profile_params.get("ho_req", 0) == 1
        or profile_params.get("ble_en", params.get("ble_en", 1)) == 0
    )

    warnings: list[str] = []
    audit_entries: list[dict] = []

    for k, override_val in override_rows.items():
        # Step 5 — Direction-Lock check
        if is_security_profile:
            current = params.get(k)
            if k == "ho_req" and current == 1 and override_val == 0:
                msg = (
                    f"Security downgrade ignored: override tries to set ho_req=0 "
                    f"but profile requires ho_req=1 (reader={reader.reader_id.hex()})"
                )
                warnings.append(msg)
                audit_entries.append(
                    {"param": k, "profile_value": current, "override_value": override_val}
                )
                continue  # skip the downgrade
            if k == "ble_en" and profile_params.get("ble_en", None) == 0 and override_val != 0:
                msg = (
                    f"Security downgrade ignored: override tries to set ble_en={override_val} "
                    f"but profile requires ble_en=0 (reader={reader.reader_id.hex()})"
                )
                warnings.append(msg)
                audit_entries.append(
                    {"param": k, "profile_value": 0, "override_value": override_val}
                )
                continue  # skip the downgrade

        params[k] = override_val

    # ------------------------------------------------------------------
    # Step 4 — Clamp to server/firmware bounds
    # ------------------------------------------------------------------
    for p in PARAMS:
        k = p.key
        if k not in params:
            continue
        if k in bounds_rows:
            b = bounds_rows[k]
            lo = b.server_floor if b.server_floor is not None else b.min_value
            hi = b.server_ceiling if b.server_ceiling is not None else b.max_value
        else:
            lo = p.server_floor if p.server_floor is not None else p.min_value
            hi = p.server_ceiling if p.server_ceiling is not None else p.max_value

        raw = params[k]
        clamped = max(lo, min(hi, raw))
        if clamped != raw:
            warnings.append(
                f"Parameter '{k}' clamped from {raw} to {clamped} "
                f"(bounds [{lo}, {hi}])"
            )
        params[k] = clamped

    # ------------------------------------------------------------------
    # Step 6 — Cross-field invariants
    # ------------------------------------------------------------------

    # lock_dur is bounded to firmware [500, 10000] by the Step-4 bounds clamp
    # (param_bounds, seeded from the catalog) — no separate hard clamp here, which
    # previously used wrong literals [100, 60000] and could emit values the firmware
    # rejects. The cross-field invariants below assume lock_dur is already in range.

    # cd_grant >= lock_dur + 1500
    cd_grant = params.get("cd_grant", PARAMS_BY_KEY["cd_grant"].default)
    lock_dur = params.get("lock_dur", PARAMS_BY_KEY["lock_dur"].default)
    required_cd_grant = lock_dur + 1500
    if cd_grant < required_cd_grant:
        cd_grant_max = bounds_rows["cd_grant"].max_value if "cd_grant" in bounds_rows else PARAMS_BY_KEY["cd_grant"].max_value
        adjusted = min(required_cd_grant, cd_grant_max)
        warnings.append(
            f"cd_grant raised from {cd_grant} to {adjusted} "
            f"to satisfy cd_grant >= lock_dur({lock_dur}) + 1500"
        )
        params["cd_grant"] = adjusted

    # ble_mtu >= 243
    ble_mtu = params.get("ble_mtu", PARAMS_BY_KEY["ble_mtu"].default)
    if ble_mtu < 243:
        warnings.append(f"ble_mtu raised from {ble_mtu} to 243 (minimum 243)")
        params["ble_mtu"] = 243

    # ------------------------------------------------------------------
    # Audit-log security downgrade events if api_key provided
    # ------------------------------------------------------------------
    if audit_entries and api_key is not None:
        for entry in audit_entries:
            session.add(AdminAuditLog(
                actor_type="api_key",
                actor_id=api_key.api_key_id,
                action="security_downgrade_ignored",
                target_type="reader",
                target_id=reader.reader_id.hex(),
                details=entry,
            ))
        await session.flush()

    # ------------------------------------------------------------------
    # Step 7 — Determine BLE suppression
    # ------------------------------------------------------------------
    # BLE-only rows (ble_mtu, ble_idle, ble_defer) are dropped from the script
    # when the hardware class is battery/NFC-only OR when ble_en resolved to 0.
    # For the ble_en row itself, in suppress mode we always emit BLE-DISABLE.
    suppress_ble = (
        hw_class == "esp32_battery_nfc"
        or params.get("ble_en", 1) == 0
    )

    # ------------------------------------------------------------------
    # Step 8 — Build .script in catalog order
    # ------------------------------------------------------------------
    script: list[tuple[str, str | None]] = []
    for p in PARAMS:
        k = p.key
        val = params.get(k)
        if val is None:
            continue

        # BLE suppression: drop BLE-only rows from script (but not ble_en itself)
        if k in BLE_ONLY_PARAMS and suppress_ble:
            continue

        if k == "ble_en":
            # When BLE suppression is active (battery class or ble_en=0),
            # always emit BLE-DISABLE regardless of stored value.
            if suppress_ble:
                script.append(("BLE-DISABLE", None))
            else:
                script.append(("BLE-ENABLE" if val else "BLE-DISABLE", None))
        else:
            if p.set_command is not None:
                script.append((p.set_command, str(val)))

    return ResolvedConfig(params=params, script=script, warnings=warnings)
