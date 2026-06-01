"""Phase 1 — reader parameterization tests.

Covers:
  - param_bounds seeded for all 26 params x 2 classes; defaults match catalog.
  - Resolver: no profile/override -> firmware defaults; profile applied; override
    on top; clamp of out-of-range override; security ceiling (skew > 300 clamped);
    cross-field (cd_grant auto-raised vs lock_dur); BLE suppression on battery
    class / ble_en=0; idempotency; direction-lock downgrade ignored + warning +
    audit.
  - API: create profile (valid + 422 on bad param/range); assign profile to a
    reader; GET config-script returns expected SET-* lines; override out-of-range
    -> 422.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import (
    AdminAuditLog,
    ParamBounds,
    Reader,
    ReaderParamOverride,
    ReaderProfile,
    ReaderProfileParam,
)
from scud.domain.reader_config_resolver import resolve_reader_params
from scud.domain.reader_param_catalog import (
    HARDWARE_CLASSES,
    PARAMS,
    PARAMS_BY_KEY,
    SEED_PROFILES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _seed_catalog(db: AsyncSession) -> None:
    """Seed param_bounds + 4 canonical profiles from the catalog module.

    Idempotent: skips rows that already exist (for session-scoped engine tests
    that share DB state across tests).
    """
    from scud.domain.reader_param_catalog import HARDWARE_CLASSES, PARAMS, SEED_PROFILES

    # Check if already seeded
    result = await db.execute(select(ParamBounds).limit(1))
    if result.scalar_one_or_none() is not None:
        return  # already seeded

    for hw in HARDWARE_CLASSES:
        for p in PARAMS:
            db.add(ParamBounds(
                param_key=p.key,
                hardware_class=hw,
                min_value=p.min_value,
                max_value=p.max_value,
                default_value=p.default,
                server_floor=p.server_floor,
                server_ceiling=p.server_ceiling,
            ))

    await db.flush()

    # Seed the 4 canonical profiles
    for profile_seed in SEED_PROFILES:
        profile = ReaderProfile(
            profile_id=uuid.uuid4(),
            name=profile_seed.name,
            hardware_class=profile_seed.hardware_class,
            description=profile_seed.description,
        )
        db.add(profile)
        await db.flush()
        for k, v in profile_seed.params.items():
            db.add(ReaderProfileParam(
                profile_id=profile.profile_id,
                param_key=k,
                value=v,
            ))

    await db.commit()


@pytest_asyncio.fixture
async def seeded_db(db_session: AsyncSession) -> AsyncSession:
    """Return db_session after seeding catalog data."""
    await _seed_catalog(db_session)
    return db_session


@pytest_asyncio.fixture
async def seeded_client(app, engine) -> AsyncClient:
    """HTTP client with catalog data pre-seeded."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await _seed_catalog(session)

    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_key_seeded(engine) -> str:
    """Create a fresh admin API key in a separate session (catalog already seeded)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scud.db.models import ApiKey
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    plaintext = "sk_admin_" + secrets.token_urlsafe(36)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    async with factory() as session:
        session.add(ApiKey(
            key_hash=key_hash,
            key_prefix=plaintext[:8],
            kind="admin",
            name="test_admin_phase1",
        ))
        await session.commit()
    return plaintext


async def _make_reader_group(db: AsyncSession) -> uuid.UUID:
    from scud.db.models import ReaderGroup
    g = ReaderGroup(
        group_id=uuid.uuid4(),
        name=f"test_group_{secrets.token_hex(4)}",
    )
    db.add(g)
    await db.flush()
    return g.group_id


async def _make_reader(
    db: AsyncSession,
    *,
    hardware_class: str = "esp32_mains_ble",
    profile_id: uuid.UUID | None = None,
) -> Reader:
    from nacl.public import PrivateKey
    from nacl.signing import SigningKey

    group_id = await _make_reader_group(db)
    rid = secrets.token_bytes(16)
    ed = SigningKey.generate()
    x = PrivateKey.generate()
    reader = Reader(
        reader_id=rid,
        reader_group_id=group_id,
        display_name="test reader",
        reader_pubkey=secrets.token_bytes(32),
        server_ed25519_privkey=bytes(ed),
        server_ed25519_pubkey=bytes(ed.verify_key),
        server_x25519_privkey=bytes(x),
        server_x25519_pubkey=bytes(x.public_key),
        hardware_class=hardware_class,
        profile_id=profile_id,
    )
    db.add(reader)
    await db.flush()
    return reader


# ---------------------------------------------------------------------------
# Section 1 — param_bounds seeding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_param_bounds_count(seeded_db: AsyncSession) -> None:
    """param_bounds must have 26 params x 2 classes = 52 rows."""
    result = await seeded_db.execute(select(ParamBounds))
    rows = result.scalars().all()
    assert len(rows) == len(PARAMS) * len(HARDWARE_CLASSES)


@pytest.mark.asyncio
async def test_param_bounds_defaults_match_catalog(seeded_db: AsyncSession) -> None:
    """default_value in param_bounds must match the catalog for every param + class."""
    for hw in HARDWARE_CLASSES:
        for p in PARAMS:
            result = await seeded_db.execute(
                select(ParamBounds).where(
                    ParamBounds.param_key == p.key,
                    ParamBounds.hardware_class == hw,
                )
            )
            row = result.scalar_one_or_none()
            assert row is not None, f"Missing bounds for ({p.key}, {hw})"
            assert row.default_value == p.default, (
                f"Default mismatch for {p.key}/{hw}: "
                f"bounds={row.default_value} catalog={p.default}"
            )


@pytest.mark.asyncio
async def test_param_bounds_security_ceiling(seeded_db: AsyncSession) -> None:
    """Security params must have server_ceiling set (skew=300, ts_drift=30)."""
    for hw in HARDWARE_CLASSES:
        r = await seeded_db.execute(
            select(ParamBounds).where(
                ParamBounds.param_key == "skew",
                ParamBounds.hardware_class == hw,
            )
        )
        skew = r.scalar_one()
        assert skew.server_ceiling == 300, f"skew server_ceiling should be 300, got {skew.server_ceiling}"

        r2 = await seeded_db.execute(
            select(ParamBounds).where(
                ParamBounds.param_key == "ts_drift",
                ParamBounds.hardware_class == hw,
            )
        )
        ts_drift = r2.scalar_one()
        assert ts_drift.server_ceiling == 30, f"ts_drift server_ceiling should be 30"


@pytest.mark.asyncio
async def test_seed_profiles_exist(seeded_db: AsyncSession) -> None:
    """All 4 canonical seed profiles must be present."""
    result = await seeded_db.execute(select(ReaderProfile))
    profiles = result.scalars().all()
    names = {p.name for p in profiles}
    expected = {sp.name for sp in SEED_PROFILES}
    assert expected.issubset(names), f"Missing profiles: {expected - names}"


# ---------------------------------------------------------------------------
# Section 2 — Resolver unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolver_no_profile_all_defaults(seeded_db: AsyncSession) -> None:
    """No profile / no override => all param values match firmware defaults."""
    reader = await _make_reader(seeded_db)
    resolved = await resolve_reader_params(seeded_db, reader)

    for p in PARAMS:
        assert resolved.params[p.key] == p.default, (
            f"param '{p.key}': expected default {p.default}, got {resolved.params[p.key]}"
        )


@pytest.mark.asyncio
async def test_resolver_profile_applied(seeded_db: AsyncSession) -> None:
    """Profile params override defaults."""
    # Find the 'entrance' profile
    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "entrance")
    )
    profile = result.scalar_one()

    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)
    resolved = await resolve_reader_params(seeded_db, reader)

    # psg_dir should be 1 (from profile)
    assert resolved.params["psg_dir"] == 1
    # wl_max should be 1024 (from profile, overriding default 256)
    assert resolved.params["wl_max"] == 1024
    # ble_en should be 1 (from profile)
    assert resolved.params["ble_en"] == 1
    # ble_idle should be 15000 (from profile, overriding default 30000)
    assert resolved.params["ble_idle"] == 15000


@pytest.mark.asyncio
async def test_resolver_override_wins(seeded_db: AsyncSession) -> None:
    """Per-reader override applied on top of profile."""
    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "entrance")
    )
    profile = result.scalar_one()
    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)

    # Override wl_max to 512
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="wl_max",
        value=512,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    assert resolved.params["wl_max"] == 512


@pytest.mark.asyncio
async def test_resolver_clamp_out_of_range_override(seeded_db: AsyncSession) -> None:
    """An override above firmware max is clamped down."""
    reader = await _make_reader(seeded_db)

    # wl_max max = 2048; try to set 9999
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="wl_max",
        value=9999,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    assert resolved.params["wl_max"] == 2048  # clamped to max
    assert any("wl_max" in w for w in resolved.warnings)


@pytest.mark.asyncio
async def test_resolver_security_ceiling_skew(seeded_db: AsyncSession) -> None:
    """skew > server_ceiling (300) is clamped to 300 even if firmware allows up to 600."""
    reader = await _make_reader(seeded_db)

    # Set skew to 500 (above server_ceiling=300 but below firmware max=600)
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="skew",
        value=500,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    assert resolved.params["skew"] == 300
    assert any("skew" in w for w in resolved.warnings)


@pytest.mark.asyncio
async def test_resolver_cross_field_cd_grant_raised(seeded_db: AsyncSession) -> None:
    """cd_grant is auto-raised if cd_grant < lock_dur + 1500."""
    reader = await _make_reader(seeded_db)

    # lock_dur = 5000, so cd_grant must be >= 6500
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="lock_dur",
        value=5000,
    ))
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="cd_grant",
        value=1000,  # too low
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    assert resolved.params["cd_grant"] >= resolved.params["lock_dur"] + 1500
    assert any("cd_grant" in w for w in resolved.warnings)


@pytest.mark.asyncio
async def test_resolver_ble_suppression_battery_class(seeded_db: AsyncSession) -> None:
    """BLE-only params excluded from script when hardware_class=esp32_battery_nfc."""
    reader = await _make_reader(seeded_db, hardware_class="esp32_battery_nfc")
    resolved = await resolve_reader_params(seeded_db, reader)

    script_commands = {cmd for cmd, _ in resolved.script}
    assert "SET-BLE-MTU" not in script_commands
    assert "SET-BLE-IDLE" not in script_commands
    assert "SET-BLE-INFO-DEFER" not in script_commands
    # ble_en=0 for battery profile -> BLE-DISABLE emitted
    assert "BLE-DISABLE" in script_commands


@pytest.mark.asyncio
async def test_resolver_ble_suppression_ble_en_zero(seeded_db: AsyncSession) -> None:
    """BLE-only params excluded from script when ble_en resolved to 0."""
    reader = await _make_reader(seeded_db)

    # Force ble_en = 0
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="ble_en",
        value=0,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    script_commands = {cmd for cmd, _ in resolved.script}
    assert "SET-BLE-MTU" not in script_commands
    assert "SET-BLE-IDLE" not in script_commands
    assert "SET-BLE-INFO-DEFER" not in script_commands
    assert "BLE-DISABLE" in script_commands


@pytest.mark.asyncio
async def test_resolver_idempotency(seeded_db: AsyncSession) -> None:
    """Resolving twice yields the identical script (idempotent)."""
    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "entrance")
    )
    profile = result.scalar_one()
    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)

    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="nfc_det",
        value=200,
    ))
    await seeded_db.flush()

    resolved1 = await resolve_reader_params(seeded_db, reader)
    resolved2 = await resolve_reader_params(seeded_db, reader)

    assert resolved1.script == resolved2.script
    assert resolved1.params == resolved2.params


@pytest.mark.asyncio
async def test_resolver_direction_lock_ho_req(seeded_db: AsyncSession) -> None:
    """Override trying to set ho_req=0 on sensitive_room profile is ignored + warned."""
    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "sensitive_room")
    )
    profile = result.scalar_one()
    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)

    # Try to downgrade ho_req from 1 to 0
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="ho_req",
        value=0,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    # ho_req must stay 1 (downgrade ignored)
    assert resolved.params["ho_req"] == 1
    assert any("ho_req" in w for w in resolved.warnings)


@pytest.mark.asyncio
async def test_resolver_direction_lock_ble_en(seeded_db: AsyncSession) -> None:
    """Override trying to re-enable BLE on sensitive_room (ble_en=0) is ignored."""
    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "sensitive_room")
    )
    profile = result.scalar_one()
    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)

    # Try to set ble_en=1 (re-enable BLE, which is a security downgrade)
    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="ble_en",
        value=1,
    ))
    await seeded_db.flush()

    resolved = await resolve_reader_params(seeded_db, reader)
    # ble_en must stay 0
    assert resolved.params["ble_en"] == 0
    assert any("ble_en" in w for w in resolved.warnings)


@pytest.mark.asyncio
async def test_resolver_direction_lock_audit_log(seeded_db: AsyncSession) -> None:
    """Security downgrade writes AdminAuditLog when api_key provided."""
    from scud.db.models import ApiKey
    api_key = ApiKey(
        api_key_id=uuid.uuid4(),
        key_hash="test_hash_" + secrets.token_hex(8),
        key_prefix="sk_admin_",
        kind="admin",
        name="audit_test_key",
    )
    seeded_db.add(api_key)
    await seeded_db.flush()

    result = await seeded_db.execute(
        select(ReaderProfile).where(ReaderProfile.name == "sensitive_room")
    )
    profile = result.scalar_one()
    reader = await _make_reader(seeded_db, profile_id=profile.profile_id)

    seeded_db.add(ReaderParamOverride(
        reader_id=reader.reader_id,
        param_key="ho_req",
        value=0,
    ))
    await seeded_db.flush()

    # Resolve with api_key => should write audit log
    resolved = await resolve_reader_params(seeded_db, reader, api_key=api_key)
    assert resolved.params["ho_req"] == 1  # downgrade ignored

    # Check audit log
    audit_result = await seeded_db.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.action == "security_downgrade_ignored",
            AdminAuditLog.actor_id == api_key.api_key_id,
        )
    )
    audit_rows = audit_result.scalars().all()
    assert len(audit_rows) >= 1


@pytest.mark.asyncio
async def test_resolver_script_order_catalog(seeded_db: AsyncSession) -> None:
    """Script is emitted in catalog order (deterministic)."""
    reader = await _make_reader(seeded_db)
    resolved = await resolve_reader_params(seeded_db, reader)

    # Build expected order for mains_ble (all BLE params present)
    from scud.domain.reader_param_catalog import BLE_ONLY_PARAMS, PARAMS
    expected_commands = []
    for p in PARAMS:
        if p.key == "ble_en":
            expected_commands.append("BLE-ENABLE")  # default ble_en=1
        elif p.set_command is not None:
            expected_commands.append(p.set_command)

    actual_commands = [cmd for cmd, _ in resolved.script]
    assert actual_commands == expected_commands


# ---------------------------------------------------------------------------
# Section 3 — API tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_create_profile_valid(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """POST /admin/reader-profiles with valid params returns 201."""
    resp = await seeded_client.post(
        "/api/v1/admin/reader-profiles",
        json={
            "name": "test_profile_valid",
            "hardware_class": "esp32_mains_ble",
            "description": "test",
            "params": {"psg_dir": 2, "nfc_det": 200},
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "test_profile_valid"
    assert data["params"]["psg_dir"] == 2
    assert data["params"]["nfc_det"] == 200


@pytest.mark.asyncio
async def test_api_create_profile_bad_param_key(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """POST /admin/reader-profiles with unknown param_key returns 422."""
    resp = await seeded_client.post(
        "/api/v1/admin/reader-profiles",
        json={
            "name": "bad_param_profile",
            "hardware_class": "esp32_mains_ble",
            "params": {"nonexistent_param": 42},
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_api_create_profile_out_of_range(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """POST /admin/reader-profiles with value out of param bounds returns 422."""
    resp = await seeded_client.post(
        "/api/v1/admin/reader-profiles",
        json={
            "name": "out_of_range_profile",
            "hardware_class": "esp32_mains_ble",
            "params": {"skew": 9999},  # max is 300 (server ceiling) or 600 (firmware)
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_api_list_profiles(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """GET /admin/reader-profiles returns list including seed profiles."""
    resp = await seeded_client.get(
        "/api/v1/admin/reader-profiles",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = {p["name"] for p in data["items"]}
    assert "entrance" in names
    assert "sensitive_room" in names


@pytest.mark.asyncio
async def test_api_get_profile(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """GET /admin/reader-profiles/{id} returns profile details."""
    # First list to get an id
    list_resp = await seeded_client.get(
        "/api/v1/admin/reader-profiles",
        headers={"X-Api-Key": admin_key_seeded},
    )
    entrance = next(p for p in list_resp.json()["items"] if p["name"] == "entrance")
    profile_id = entrance["profile_id"]

    resp = await seeded_client.get(
        f"/api/v1/admin/reader-profiles/{profile_id}",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "entrance"
    assert "psg_dir" in data["params"]


@pytest.mark.asyncio
async def test_api_patch_profile(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """PATCH /admin/reader-profiles/{id} updates the profile."""
    # Create a fresh profile to patch
    create_resp = await seeded_client.post(
        "/api/v1/admin/reader-profiles",
        json={
            "name": "patchable_profile",
            "hardware_class": "esp32_mains_ble",
            "params": {"psg_dir": 1},
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert create_resp.status_code == 201
    profile_id = create_resp.json()["profile_id"]

    patch_resp = await seeded_client.patch(
        f"/api/v1/admin/reader-profiles/{profile_id}",
        json={"params": {"psg_dir": 2, "nfc_det": 150}},
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    data = patch_resp.json()
    assert data["params"]["psg_dir"] == 2
    assert data["params"]["nfc_det"] == 150


@pytest.mark.asyncio
async def test_api_delete_profile(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
) -> None:
    """DELETE /admin/reader-profiles/{id} removes the profile."""
    create_resp = await seeded_client.post(
        "/api/v1/admin/reader-profiles",
        json={
            "name": "deletable_profile",
            "hardware_class": "esp32_mains_ble",
            "params": {},
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert create_resp.status_code == 201
    profile_id = create_resp.json()["profile_id"]

    del_resp = await seeded_client.delete(
        f"/api/v1/admin/reader-profiles/{profile_id}",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert del_resp.status_code == 200, del_resp.text

    get_resp = await seeded_client.get(
        f"/api/v1/admin/reader-profiles/{profile_id}",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_api_enroll_reader_with_profile(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
    engine,
) -> None:
    """Enrolling a reader with profile_id sets the profile."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scud.db.models import ReaderGroup

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    # Create a group
    async with factory() as session:
        group = ReaderGroup(group_id=uuid.uuid4(), name=f"grp_{secrets.token_hex(4)}")
        session.add(group)
        await session.commit()
        group_id = str(group.group_id)

    # Get entrance profile id
    list_resp = await seeded_client.get(
        "/api/v1/admin/reader-profiles",
        headers={"X-Api-Key": admin_key_seeded},
    )
    entrance = next(p for p in list_resp.json()["items"] if p["name"] == "entrance")
    profile_id = entrance["profile_id"]

    reader_id_hex = secrets.token_hex(16)
    pubkey_b64 = base64.b64encode(secrets.token_bytes(32)).decode()

    enroll_resp = await seeded_client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id_hex,
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "test reader with profile",
            "profile_id": profile_id,
            "hardware_class": "esp32_mains_ble",
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert enroll_resp.status_code == 200, enroll_resp.text

    # GET config-script
    script_resp = await seeded_client.get(
        f"/api/v1/admin/readers/{reader_id_hex}/config-script",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert script_resp.status_code == 200, script_resp.text
    data = script_resp.json()
    assert data["profile_id"] == profile_id
    # psg_dir=1 from entrance profile -> SET-PASSAGE-DIRECTION 1
    commands = {item["command"]: item["arg"] for item in data["script"]}
    assert commands.get("SET-PASSAGE-DIRECTION") == "1"
    assert commands.get("SET-WHITELIST-MAX") == "1024"


@pytest.mark.asyncio
async def test_api_patch_reader_assign_profile(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
    engine,
) -> None:
    """PATCH /admin/readers/{id} with profile_id updates the assignment."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scud.db.models import ReaderGroup

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        group = ReaderGroup(group_id=uuid.uuid4(), name=f"grp_{secrets.token_hex(4)}")
        session.add(group)
        await session.commit()
        group_id = str(group.group_id)

    reader_id_hex = secrets.token_hex(16)
    pubkey_b64 = base64.b64encode(secrets.token_bytes(32)).decode()

    # Enroll without profile
    enroll_resp = await seeded_client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id_hex,
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "reader for patch test",
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert enroll_resp.status_code == 200

    # Get interior_room profile
    list_resp = await seeded_client.get(
        "/api/v1/admin/reader-profiles",
        headers={"X-Api-Key": admin_key_seeded},
    )
    interior = next(p for p in list_resp.json()["items"] if p["name"] == "interior_room")
    profile_id = interior["profile_id"]

    # Patch to assign profile
    patch_resp = await seeded_client.patch(
        f"/api/v1/admin/readers/{reader_id_hex}",
        json={"profile_id": profile_id},
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    # Config script should now reflect interior_room params
    script_resp = await seeded_client.get(
        f"/api/v1/admin/readers/{reader_id_hex}/config-script",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert script_resp.status_code == 200
    data = script_resp.json()
    commands = {item["command"]: item["arg"] for item in data["script"]}
    # interior_room has psg_dir=2
    assert commands.get("SET-PASSAGE-DIRECTION") == "2"


@pytest.mark.asyncio
async def test_api_override_out_of_range_returns_422(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
    engine,
) -> None:
    """PATCH /admin/readers/{id} with out-of-range override returns 422."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scud.db.models import ReaderGroup

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        group = ReaderGroup(group_id=uuid.uuid4(), name=f"grp_{secrets.token_hex(4)}")
        session.add(group)
        await session.commit()
        group_id = str(group.group_id)

    reader_id_hex = secrets.token_hex(16)
    pubkey_b64 = base64.b64encode(secrets.token_bytes(32)).decode()

    enroll_resp = await seeded_client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id_hex,
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "override range test",
        },
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert enroll_resp.status_code == 200

    # wl_max max = 2048; try 99999
    patch_resp = await seeded_client.patch(
        f"/api/v1/admin/readers/{reader_id_hex}",
        json={"overrides": {"wl_max": 99999}},
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert patch_resp.status_code == 422, patch_resp.text


@pytest.mark.asyncio
async def test_api_config_script_ble_suppression(
    seeded_client: AsyncClient,
    admin_key_seeded: str,
    engine,
) -> None:
    """Config script for battery_nfc_only profile reader suppresses BLE params."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scud.db.models import ReaderGroup

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        group = ReaderGroup(group_id=uuid.uuid4(), name=f"grp_{secrets.token_hex(4)}")
        session.add(group)
        await session.commit()
        group_id = str(group.group_id)

    list_resp = await seeded_client.get(
        "/api/v1/admin/reader-profiles",
        headers={"X-Api-Key": admin_key_seeded},
    )
    battery_profile = next(
        p for p in list_resp.json()["items"] if p["name"] == "battery_nfc_only"
    )
    profile_id = battery_profile["profile_id"]

    reader_id_hex = secrets.token_hex(16)
    pubkey_b64 = base64.b64encode(secrets.token_bytes(32)).decode()

    await seeded_client.post(
        "/api/v1/admin/readers/enroll",
        json={
            "reader_id": reader_id_hex,
            "reader_pubkey": pubkey_b64,
            "reader_group_id": group_id,
            "display_name": "battery reader",
            "hardware_class": "esp32_battery_nfc",
            "profile_id": profile_id,
        },
        headers={"X-Api-Key": admin_key_seeded},
    )

    script_resp = await seeded_client.get(
        f"/api/v1/admin/readers/{reader_id_hex}/config-script",
        headers={"X-Api-Key": admin_key_seeded},
    )
    assert script_resp.status_code == 200
    data = script_resp.json()
    commands = {item["command"] for item in data["script"]}
    # BLE-only params suppressed
    assert "SET-BLE-MTU" not in commands
    assert "SET-BLE-IDLE" not in commands
    assert "SET-BLE-INFO-DEFER" not in commands
    # BLE-DISABLE emitted
    assert "BLE-DISABLE" in commands
