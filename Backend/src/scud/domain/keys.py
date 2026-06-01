"""Key issuance and revocation domain logic (§5.3)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from scud.config import settings
from scud.crypto.bloom import bloom_contains
from scud.crypto.key_id import compute_key_id
from scud.crypto.serialization import pack_issued_key
from scud.db.models import FilterPackage, IssuedKey, TimeGrant
from scud.db.repositories import devices as dev_repo
from scud.db.repositories import filters as filter_repo
from scud.db.repositories import grants as grant_repo
from scud.db.repositories import keys as key_repo
from scud.db.repositories import permits as permit_repo
from scud.db.repositories import readers as reader_repo
from scud.db.repositories import tasks as task_repo
from scud.domain.grants import create_time_grant

logger = logging.getLogger(__name__)


def _fp_in_filter(fp: Optional[FilterPackage], key_id: bytes) -> bool:
    """Проверить, попадает ли key_id в текущий bloom и НЕ нейтрализуется ли whitelist'ом.

    Layout filter_package.package_bytes (см. crypto/serialization.py::pack_filter_package):
        [0:56]                                     header
        [56 : 56+filter_bytes_len]                 bloom_bytes
        [wl_off : wl_off + wl_count*24]            whitelist (key_id + expires_at)
        [bl_off : bl_off + bl_count*16]            blacklist_delta
        [last 64]                                  signature

    Returns True если key_id даст RES_REVOKED_FILTER на ридере при текущем фильтре.
    """
    if fp is None:
        return False
    pkg = fp.package_bytes
    bloom_off = 56
    bloom = pkg[bloom_off : bloom_off + fp.filter_bytes_len]
    if not bloom_contains(bloom, key_id, fp.m_bits, fp.k_hashes, fp.hash_seed):
        return False
    # Попали в bloom — whitelist может нейтрализовать. whitelist_count у filter
    # package — реальное количество записей.
    wl_off = bloom_off + fp.filter_bytes_len
    for i in range(fp.whitelist_count):
        entry_off = wl_off + i * 24
        if pkg[entry_off : entry_off + 16] == key_id:
            return False
    return True


async def issue_key(
    session: AsyncSession,
    permit_id: uuid.UUID,
    user_id: int,
    validity_seconds: int,
    request_grant: bool,
    issued_at_ts: Optional[int] = None,
) -> tuple[IssuedKey, Optional[TimeGrant]]:
    """Issue a new key for the given permit. Returns (key, grant_or_none).

    issued_at_ts — опциональный future-start (unix-секунды). Если None → now.
    Используется для выпуска ключа «на будущее» (UI выбирает start/end
    через date-picker).
    """
    if validity_seconds <= 0:
        raise ValueError("ttl_invalid")

    # Lock permit
    permit = await permit_repo.get_permit_for_update(session, permit_id)
    if permit is None or permit.user_id != user_id:
        raise PermissionError("not_owner")
    if permit.revoked_at is not None:
        raise ValueError("permit_revoked")

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    if permit.valid_from > now:
        raise ValueError("permit_not_started")
    if permit.valid_until <= now:
        raise ValueError("permit_expired")
    if validity_seconds > permit.max_token_ttl_seconds:
        raise ValueError(f"ttl_too_long:{permit.max_token_ttl_seconds}")

    active_count = await permit_repo.count_active_keys(session, permit_id)
    if active_count >= permit.n_parallel:
        raise ValueError(f"n_parallel_exceeded:{active_count}:{permit.n_parallel}")

    # Защита bloom-фильтра отзыва от раздувания: если задан max_total_issued —
    # считаем ключи, ещё не истёкшие (expires_at > now, любой статус), и
    # отказываем при достижении квоты. Истёкшие ключи ридер отбрасывает по сроку,
    # в фильтр они не попадают и поэтому не учитываются — место в квоте
    # освобождается само, как только ключ выходит из окна валидности.
    if permit.max_total_issued is not None:
        unexpired = await permit_repo.count_unexpired_keys(session, permit_id, now)
        if unexpired >= permit.max_total_issued:
            raise ValueError(
                f"unexpired_cap_exceeded:{unexpired}:{permit.max_total_issued}"
            )

    device = await dev_repo.get_active_device_for_user(session, user_id)
    if device is None:
        raise ValueError("no_active_device")

    reader = await reader_repo.get_reader_by_id(session, permit.reader_id)
    if reader is None or not reader.is_active:
        raise LookupError("reader_not_found")

    base_serial = await key_repo.get_next_serial(session, permit_id)

    # issued_at берём из аргумента (future-start) или now. Валидация:
    #   * не раньше now - 15s (clock-skew защита),
    #   * не позже permit.valid_until.
    # Если клиент дал issued_at_ts в прошлое больше чем на 15 секунд — откатываем на now.
    CLOCK_SKEW_SEC = 15
    effective_issued_ts = issued_at_ts if issued_at_ts is not None else now_ts
    if effective_issued_ts < now_ts - CLOCK_SKEW_SEC:
        effective_issued_ts = now_ts
    permit_until_ts = int(permit.valid_until.timestamp())
    if effective_issued_ts >= permit_until_ts:
        raise ValueError("permit_expired")

    issued_at_final_ts = effective_issued_ts
    # expires_at = issued_at + validity, но не позже permit.valid_until.
    expires_dt = min(
        datetime.fromtimestamp(issued_at_final_ts, tz=timezone.utc)
        + timedelta(seconds=validity_seconds),
        permit.valid_until,
    )
    expires_at_ts = int(expires_dt.timestamp())
    # Переименование для минимального diff в остальном коде — сохраняем старое имя.
    issued_at_ts = issued_at_final_ts

    permit_id_bytes = permit.permit_id.bytes
    phone_pubkey = device.phone_pubkey
    reader_id = permit.reader_id

    # A. Retry подбор serial, чтобы key_id не попал в FP **последнего** bloom-фильтра
    # (max filter_version в БД), а не того, что реально применён на ридере. Даже если
    # ридер ещё не применил актуальный фильтр — мы выпускаем ключ, совместимый с тем
    # набором, который к нему доставится в ближайший же courier-раунд. Так мы не
    # тратим попытки на «устаревшую» картину и не получаем recovered-ключей, которые
    # после apply_new_filter на ридере окажутся снова в FP.
    # C. Fallback: если все попытки провалились — выпускаем ключ как есть, и тогда
    # (и ТОЛЬКО тогда) запрашиваем генерацию нового фильтра, чтобы ключ попал в
    # whitelist при следующем bloom-апдейте.
    latest_filter = await filter_repo.get_latest_filter(session, reader_id)
    serial = base_serial
    key_id = compute_key_id(reader_id, phone_pubkey, issued_at_ts, serial)
    needs_filter_regen = False
    if latest_filter is not None:
        max_retries = settings.issue_key_bloom_retry_max
        attempts = 0
        while attempts < max_retries and _fp_in_filter(latest_filter, key_id):
            attempts += 1
            serial += 1
            key_id = compute_key_id(reader_id, phone_pubkey, issued_at_ts, serial)
        still_fp = _fp_in_filter(latest_filter, key_id)
        if still_fp:
            needs_filter_regen = True
            logger.warning(
                "issue_key: %d retries exhausted, key %s falls into bloom FP "
                "of latest filter v%d (permit=%s, reader=%s). "
                "Filter regeneration will include it in whitelist.",
                max_retries, key_id.hex(), latest_filter.filter_version,
                permit_id, reader_id.hex(),
            )
        elif attempts > 0:
            logger.info(
                "issue_key: recovered from bloom FP after %d retries "
                "against latest filter v%d (permit=%s)",
                attempts, latest_filter.filter_version, permit_id,
            )

    payload = b"\x00\x00"

    full_key_bytes = pack_issued_key(
        reader_id=reader_id,
        phone_pubkey=phone_pubkey,
        issued_at=issued_at_ts,
        expires_at=expires_at_ts,
        permit_id=permit_id_bytes,
        serial=serial,
        payload=payload,
        server_privkey=reader.server_ed25519_privkey,
    )

    issued_key = IssuedKey(
        key_id=key_id,
        permit_id=permit_id,
        reader_id=reader_id,
        phone_pubkey=phone_pubkey,
        device_id=device.device_id,
        issued_at=datetime.fromtimestamp(issued_at_ts, tz=timezone.utc),
        expires_at=expires_dt,
        serial=serial,
        payload=payload,
        status="active",
        full_key_bytes=full_key_bytes,
    )
    await key_repo.insert_issued_key(session, issued_key)

    new_grant: Optional[TimeGrant] = None
    if request_grant:
        existing = await grant_repo.get_active_grant_for_permit_pubkey(
            session, permit_id, phone_pubkey
        )
        if existing is None:
            new_grant = await create_time_grant(
                session=session,
                permit=permit,
                reader=reader,
                authority_user_id=user_id,
                authority_pubkey=phone_pubkey,
            )

    # Генерацию нового фильтра триггерим ТОЛЬКО если текущий фильтр даёт FP
    # на вновь выпущенный key_id (fallback-ветка выше). В остальных случаях
    # ключ уже совместим с актуальным фильтром — ридер примет его без апдейта,
    # и лишняя filter_package + courier-доставка не нужны.
    if needs_filter_regen:
        await task_repo.enqueue_generate_filter_debounced(
            session, reader_id.hex()
        )

    return issued_key, new_grant


async def revoke_key_on_server(
    session: AsyncSession,
    key_id_hex: str,
    user_id: int,
) -> None:
    """Revoke key by user. Raises appropriate errors."""
    key_id = bytes.fromhex(key_id_hex)
    key = await key_repo.get_key_by_id(session, key_id)
    if key is None:
        raise LookupError("key_not_found")

    # Check ownership via permit
    permit = await permit_repo.get_permit_by_id(session, key.permit_id)
    if permit is None or permit.user_id != user_id:
        raise PermissionError("not_owner")

    if key.status != "active":
        raise ValueError("not_active")

    await key_repo.revoke_key(session, key_id, "revoked_by_server", 0)

    reader = await reader_repo.get_reader_by_id(session, key.reader_id)
    if reader:
        await task_repo.enqueue_generate_filter_debounced(session, key.reader_id.hex())


async def admin_revoke_key(
    session: AsyncSession,
    key_id_hex: str,
) -> None:
    key_id = bytes.fromhex(key_id_hex)
    key = await key_repo.get_key_by_id(session, key_id)
    if key is None:
        raise LookupError("key_not_found")
    await key_repo.revoke_key(session, key_id, "revoked_by_server", 0)
    await task_repo.enqueue_generate_filter_debounced(session, key.reader_id.hex())
