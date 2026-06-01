"""Time authority grant creation."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from scud.crypto.serialization import KIND_SOFT, pack_time_authority_grant
from scud.db.models import Permit, Reader, TimeGrant
from scud.db.repositories import grants as grant_repo


async def create_time_grant(
    session: AsyncSession,
    permit: Permit,
    reader: Reader,
    authority_user_id: int,
    authority_pubkey: bytes,
    kind: int = KIND_SOFT,
) -> TimeGrant:
    """Create and persist a time_authority_grant."""
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    expires_at_ts = int(permit.valid_until.timestamp())

    # authority_id is a random UUID v4 raw bytes
    authority_id = os.urandom(16)

    full_grant_bytes = pack_time_authority_grant(
        reader_id=reader.reader_id,
        authority_pubkey=authority_pubkey,
        authority_id=authority_id,
        issued_at=now_ts,
        expires_at=expires_at_ts,
        kind=kind,
        server_privkey=reader.server_ed25519_privkey,
    )

    kind_str = "soft" if kind == KIND_SOFT else "hard"
    grant = TimeGrant(
        permit_id=permit.permit_id,
        reader_id=reader.reader_id,
        authority_user_id=authority_user_id,
        authority_pubkey=authority_pubkey,
        kind=kind_str,
        issued_at=now,
        expires_at=permit.valid_until,
        full_grant_bytes=full_grant_bytes,
    )
    await grant_repo.insert_grant(session, grant)
    return grant
