from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.db.models import IssuedKey, Permit


async def get_permit_by_id(session: AsyncSession, permit_id: uuid.UUID) -> Optional[Permit]:
    return await session.get(Permit, permit_id)


async def get_permit_for_update(
    session: AsyncSession, permit_id: uuid.UUID
) -> Optional[Permit]:
    """SELECT FOR UPDATE — use inside an active transaction."""
    result = await session.execute(
        select(Permit)
        .where(Permit.permit_id == permit_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def list_permits_for_user(
    session: AsyncSession,
    user_id: int,
    active_only: bool = True,
) -> list[Permit]:
    q = select(Permit).where(Permit.user_id == user_id)
    if active_only:
        now = datetime.now(timezone.utc)
        q = q.where(Permit.revoked_at.is_(None), Permit.valid_until > now)
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_permits_admin(
    session: AsyncSession,
    cursor: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
    reader_id: Optional[bytes] = None,
) -> list[Permit]:
    q = select(Permit)
    if user_id is not None:
        q = q.where(Permit.user_id == user_id)
    if reader_id is not None:
        q = q.where(Permit.reader_id == reader_id)
    q = q.offset(cursor).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def create_permit(
    session: AsyncSession,
    user_id: int,
    reader_id: bytes,
    display_name: str,
    description: Optional[str],
    valid_from: datetime,
    valid_until: datetime,
    n_parallel: int,
    max_token_ttl_seconds: int,
    max_total_issued: Optional[int] = None,
    created_by_api_key: Optional[uuid.UUID] = None,
) -> Permit:
    permit = Permit(
        user_id=user_id,
        reader_id=reader_id,
        display_name=display_name,
        description=description,
        valid_from=valid_from,
        valid_until=valid_until,
        n_parallel=n_parallel,
        max_token_ttl_seconds=max_token_ttl_seconds,
        max_total_issued=max_total_issued,
        created_by_api_key=created_by_api_key,
    )
    session.add(permit)
    await session.flush()
    await session.refresh(permit)
    return permit


async def mark_permit_revoke_initiated(
    session: AsyncSession, permit_id: uuid.UUID
) -> None:
    """Phase 1 admin-revoke: пометить permit как «revoke pending».

    Ставит revoke_initiated_at = now, но revoked_at пока NULL — финализация
    произойдёт когда все ключи permit'а перейдут в is_active=False (после
    применения нового bloom на ридере).
    """
    now = datetime.now(timezone.utc)
    await session.execute(
        update(Permit)
        .where(
            Permit.permit_id == permit_id,
            Permit.revoked_at.is_(None),
            Permit.revoke_initiated_at.is_(None),
        )
        .values(revoke_initiated_at=now)
    )


async def finalize_permit_revoke(
    session: AsyncSession, permit_id: uuid.UUID
) -> None:
    """Phase 2 (или короткий путь): окончательно отозвать permit.

    Ставит revoked_at = now. Также гарантирует revoke_initiated_at != NULL
    (если короткий путь без активных ключей — заполняем тем же now).
    """
    now = datetime.now(timezone.utc)
    await session.execute(
        update(Permit)
        .where(Permit.permit_id == permit_id, Permit.revoked_at.is_(None))
        .values(
            revoked_at=now,
            # Если revoke_initiated_at был NULL (короткий путь), ставим тот же now;
            # COALESCE сохранит уже выставленное значение для двухфазного сценария.
            revoke_initiated_at=func.coalesce(Permit.revoke_initiated_at, now),
        )
    )


async def list_pending_revokes_for_reader(
    session: AsyncSession, reader_id: bytes
) -> list[Permit]:
    """Permit'ы конкретного ридера в состоянии "revoke pending".

    Используется в Phase 2: после процессинга delivery_receipt / FDI ridge'а
    мы перебираем такие permit'ы и финализируем те, у кого active=0.
    """
    result = await session.execute(
        select(Permit).where(
            Permit.reader_id == reader_id,
            Permit.revoke_initiated_at.is_not(None),
            Permit.revoked_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def count_active_keys(session: AsyncSession, permit_id: uuid.UUID) -> int:
    """Единый счётчик активных ключей по is_active.

    PG-trigger sync_is_active определяет: is_active = status NOT IN
    (revoked_by_reader, revoked_in_bloom, expired). То есть active и
    revoked_by_server считаются «активными» — это by-design, чтобы:
      - n_parallel-гейт не пускал новый выпуск, пока ридер не применил bloom,
      - admin-revoke оставался в «pending», пока is_active не станет False
        у всех ключей (что произойдёт при переходе → revoked_in_bloom).
    """
    result = await session.execute(
        select(func.count())
        .select_from(IssuedKey)
        .where(IssuedKey.permit_id == permit_id, IssuedKey.is_active.is_(True))
    )
    return result.scalar_one()


async def count_unexpired_keys(
    session: AsyncSession, permit_id: uuid.UUID, now: datetime
) -> int:
    """Счётчик ещё-не-истёкших ключей permit'а: expires_at > now (любой статус).

    В отличие от count_active_keys (гейт n_parallel, фильтр по is_active) сюда
    попадают и revoked_by_reader / revoked_in_bloom — пока их срок не вышел.
    Не попадают expired-ключи: ридер отбрасывает их по сроку, поэтому они не
    нуждаются в записи в bloom-фильтре отзыва и не должны держать место в квоте.

    Используется для гейта permit.max_total_issued — защиты bloom-фильтра отзыва
    от раздувания: квота ограничивает, сколько ключей одного permit'а могут
    одновременно находиться в окне валидности (и, как следствие, в фильтре).
    """
    result = await session.execute(
        select(func.count())
        .select_from(IssuedKey)
        .where(IssuedKey.permit_id == permit_id, IssuedKey.expires_at > now)
    )
    return result.scalar_one()
