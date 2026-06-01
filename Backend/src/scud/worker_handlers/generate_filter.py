"""Worker handler: generate_filter (§6.1)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from scud.domain.filters import handle_generate_filter

logger = logging.getLogger(__name__)


async def handle(session: AsyncSession, payload: dict) -> None:
    reader_id_hex: str = payload["reader_id"]
    reader_id = bytes.fromhex(reader_id_hex)
    logger.info("generating filter for reader %s", reader_id_hex)
    fp = await handle_generate_filter(session, reader_id)
    logger.info("filter_package v%d generated for reader %s", fp.filter_version, reader_id_hex)
