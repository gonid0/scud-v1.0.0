"""Permit domain: listing, revoke."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.repositories import grants as grant_repo
from scud.db.repositories import keys as key_repo
from scud.db.repositories import permits as permit_repo

logger = logging.getLogger(__name__)


async def revoke_permit_by_user(
    session: AsyncSession,
    permit_id: uuid.UUID,
    user_id: int,
) -> None:
    """Revoke permit. Raises ValueError if has active keys or 403 if not owned.

    Пользовательский revoke — синхронный: разрешён только когда нет активных
    ключей. Сразу выставляет revoked_at = now (никакого revoke-pending).
    """
    permit = await permit_repo.get_permit_by_id(session, permit_id)
    if permit is None or permit.user_id != user_id:
        raise PermissionError("not_owner")

    active_count = await permit_repo.count_active_keys(session, permit_id)
    if active_count > 0:
        raise ValueError(f"has_active_keys:{active_count}")

    await permit_repo.finalize_permit_revoke(session, permit_id)
    await grant_repo.revoke_grants_for_permit(session, permit_id)


async def admin_revoke_permit(
    session: AsyncSession,
    permit_id: uuid.UUID,
) -> list[bytes]:
    """Двухфазный admin-revoke.

    Phase 1 (synchronous):
      - Все active-ключи permit'а помечаются revoked_by_server.
      - permit.revoke_initiated_at = now, но revoked_at пока NULL — состояние
        "revoke pending", т.к. is_active=True у revoked_by_server-ключей
        остаётся пока ридер не применит новый bloom (см. PG-trigger
        sync_is_active).
      - Если active-ключей не было — сразу финализируем: revoked_at = now.
      - Time-grants отзываются сразу.
      - Возвращаемые reader_id передаются caller'у — endpoint триггерит для
        них generate_filter, чтобы свежий bloom включил все revoked_by_server.

    Phase 2 (asynchronous, через finalize_pending_revokes):
      - В process_delivery_receipt / process_fdi_blob: когда ключи переходят
        revoked_by_server → revoked_in_bloom (is_active=False), мы перебираем
        permit'ы в состоянии "revoke pending" для затронутого ридера и
        финализируем те, у кого active=0.

    Returns: список reader_id, для которых надо регенерить фильтр.
    """
    permit = await permit_repo.get_permit_by_id(session, permit_id)
    if permit is None:
        raise LookupError("permit_not_found")

    reader_ids = await key_repo.revoke_all_keys_for_permit(session, permit_id)
    await grant_repo.revoke_grants_for_permit(session, permit_id)

    active_after = await permit_repo.count_active_keys(session, permit_id)
    if active_after == 0:
        # Активных ключей нет (либо их и не было, либо все уже revoked_in_bloom)
        # — финализируем сразу.
        await permit_repo.finalize_permit_revoke(session, permit_id)
    else:
        # Есть active=True ключи (revoked_by_server). Ждём пока ридер их в bloom
        # включит и переведёт в revoked_in_bloom — Phase 2 финализирует permit.
        await permit_repo.mark_permit_revoke_initiated(session, permit_id)
        logger.info(
            "admin_revoke_permit: permit %s entered REVOKE_PENDING "
            "(still %d active keys awaiting bloom apply)",
            permit_id, active_after,
        )

    return reader_ids


async def finalize_pending_revokes(
    session: AsyncSession,
    reader_id: bytes,
) -> int:
    """Завершить admin-revoke у permit'ов, у которых все ключи уже is_active=False.

    Вызывается после process_delivery_receipt / process_fdi_blob (когда ключи
    переходят в revoked_in_bloom). Перебираем permit'ы этого ридера в состоянии
    "revoke pending"; для каждого с active_keys=0 — финализируем (revoked_at = now).

    Returns: количество финализированных permit'ов.
    """
    pending = await permit_repo.list_pending_revokes_for_reader(session, reader_id)
    finalized = 0
    for permit in pending:
        active = await permit_repo.count_active_keys(session, permit.permit_id)
        if active == 0:
            await permit_repo.finalize_permit_revoke(session, permit.permit_id)
            logger.info(
                "finalize_pending_revokes: permit %s fully revoked "
                "(all keys is_active=False)",
                permit.permit_id,
            )
            finalized += 1
    return finalized
