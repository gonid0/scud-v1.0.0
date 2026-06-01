"""Passage receipt unit + integration tests (shared §15).

Покрывают:
  - serialization round-trip (192 B layout стабилен);
  - signature verification (положительный + три отрицательных кейса);
  - ingestion idempotency (повторная доставка не дублирует);
  - resolution user_id через permit_id;
  - rejection BAD_SIGNATURE / BAD_FORMAT.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from scud.crypto.serialization import (
    PASSAGE_RECEIPT_LEN,
    parse_passage_receipt,
)
from scud.crypto.signing import DOMAIN_PSG, sign_detached, verify_detached
from scud.db.models import IssuedKey, PassageEvent, Permit, Reader, ReaderGroup, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_receipt(
    *,
    reader_id: bytes,
    reader_priv: bytes,
    key_id: bytes,
    permit_id: bytes,
    phone_pubkey: bytes,
    passed_at: int,
    direction: int = 1,
    verdict: int = 0,
    session_seq: int = 7,
    flags: int = 1,
    issued_key_issued_at: int = 1700000000,
    issued_key_serial: int = 42,
    receipt_nonce: bytes | None = None,
    sign: bool = True,
) -> bytes:
    """Build a 192-byte passage_receipt as the firmware would."""
    nonce = receipt_nonce or os.urandom(16)
    body = (
        b"\x01"
        + reader_id
        + nonce
        + key_id
        + passed_at.to_bytes(8, "little")
        + bytes([direction])
        + bytes([verdict])
        + session_seq.to_bytes(4, "little")
        + bytes([flags])
        + phone_pubkey
        + permit_id
        + issued_key_issued_at.to_bytes(8, "little")
        + issued_key_serial.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
    )
    assert len(body) == 128, len(body)
    if sign:
        sig = sign_detached(reader_priv, DOMAIN_PSG, body)
    else:
        sig = b"\x00" * 64
    blob = body + sig
    assert len(blob) == PASSAGE_RECEIPT_LEN
    return blob


# ---------------------------------------------------------------------------
# 1. Parser unit tests
# ---------------------------------------------------------------------------

def test_parse_receipt_round_trip():
    reader_id = os.urandom(16)
    sk = SigningKey.generate()
    receipt_blob = _build_receipt(
        reader_id=reader_id,
        reader_priv=bytes(sk),
        key_id=os.urandom(16),
        permit_id=os.urandom(16),
        phone_pubkey=os.urandom(32),
        passed_at=1700000123,
    )
    parsed = parse_passage_receipt(receipt_blob)
    assert parsed.reader_id == reader_id
    assert parsed.passed_at == 1700000123
    assert parsed.direction == 1
    assert parsed.session_seq == 7
    assert len(parsed.signature) == 64
    assert verify_detached(
        bytes(sk.verify_key), DOMAIN_PSG, parsed.signed_payload, parsed.signature
    )


def test_parse_receipt_rejects_wrong_size():
    with pytest.raises(ValueError):
        parse_passage_receipt(b"\x01" * 191)
    with pytest.raises(ValueError):
        parse_passage_receipt(b"\x01" * 193)


def test_parse_receipt_rejects_bad_format_version():
    sk = SigningKey.generate()
    receipt_blob = bytearray(_build_receipt(
        reader_id=os.urandom(16),
        reader_priv=bytes(sk),
        key_id=os.urandom(16),
        permit_id=os.urandom(16),
        phone_pubkey=os.urandom(32),
        passed_at=1700000000,
    ))
    receipt_blob[0] = 0x02
    with pytest.raises(ValueError):
        parse_passage_receipt(bytes(receipt_blob))


# ---------------------------------------------------------------------------
# 2. Ingestion (domain) tests
# ---------------------------------------------------------------------------

@pytest.fixture
def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def _make_reader(db_session, reader_priv_sk: SigningKey) -> Reader:
    """Persist a reader row with the given reader_priv as its pubkey owner."""
    group = ReaderGroup(name="g-" + secrets.token_hex(4))
    db_session.add(group)
    await db_session.flush()

    server_sk = SigningKey.generate()
    from nacl.public import PrivateKey as XPrivateKey
    server_x = XPrivateKey.generate()
    reader = Reader(
        reader_id=os.urandom(16),
        reader_group_id=group.group_id,
        display_name="Test reader",
        reader_pubkey=bytes(reader_priv_sk.verify_key),
        server_ed25519_privkey=bytes(server_sk),
        server_ed25519_pubkey=bytes(server_sk.verify_key),
        server_x25519_privkey=bytes(server_x),
        server_x25519_pubkey=bytes(server_x.public_key),
    )
    db_session.add(reader)
    await db_session.flush()
    return reader


async def _make_permit_for_user(db_session, user_id: int, reader_id: bytes) -> Permit:
    permit = Permit(
        user_id=user_id,
        reader_id=reader_id,
        display_name="test permit",
        valid_from=datetime.now(timezone.utc),
        valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
        n_parallel=1,
        max_token_ttl_seconds=86400,
    )
    db_session.add(permit)
    await db_session.flush()
    return permit


@pytest.mark.asyncio
async def test_ingest_passage_creates_event(db_session, test_user, now_ts):
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit_for_user(db_session, test_user["user_id"], reader.reader_id)

    blob = _build_receipt(
        reader_id=reader.reader_id,
        reader_priv=bytes(reader_sk),
        key_id=os.urandom(16),
        permit_id=permit.permit_id.bytes,
        phone_pubkey=os.urandom(32),
        passed_at=now_ts - 60,
    )

    await process_passage_receipt(
        db_session, blob, reader, delivered_by_user_id=test_user["user_id"]
    )

    rows = (await db_session.execute(select(PassageEvent))).scalars().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.reader_id == reader.reader_id
    assert ev.user_id == test_user["user_id"]
    assert ev.permit_id == permit.permit_id
    assert ev.delivered_by == test_user["user_id"]


@pytest.mark.asyncio
async def test_ingest_passage_is_idempotent(db_session, test_user, now_ts):
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit_for_user(db_session, test_user["user_id"], reader.reader_id)

    blob = _build_receipt(
        reader_id=reader.reader_id,
        reader_priv=bytes(reader_sk),
        key_id=os.urandom(16),
        permit_id=permit.permit_id.bytes,
        phone_pubkey=os.urandom(32),
        passed_at=now_ts - 60,
    )

    await process_passage_receipt(db_session, blob, reader)
    await process_passage_receipt(db_session, blob, reader)  # second time: no-op
    rows = (await db_session.execute(select(PassageEvent))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ingest_passage_rejects_bad_signature(db_session, test_user, now_ts):
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit_for_user(db_session, test_user["user_id"], reader.reader_id)

    other_sk = SigningKey.generate()  # signs with WRONG key
    blob = _build_receipt(
        reader_id=reader.reader_id,
        reader_priv=bytes(other_sk),
        key_id=os.urandom(16),
        permit_id=permit.permit_id.bytes,
        phone_pubkey=os.urandom(32),
        passed_at=now_ts - 60,
    )

    with pytest.raises(ValueError, match="signature"):
        await process_passage_receipt(db_session, blob, reader)


@pytest.mark.asyncio
async def test_ingest_passage_rejects_wrong_reader_id(db_session, test_user, now_ts):
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit_for_user(db_session, test_user["user_id"], reader.reader_id)

    blob = _build_receipt(
        reader_id=os.urandom(16),  # different reader_id in receipt body
        reader_priv=bytes(reader_sk),
        key_id=os.urandom(16),
        permit_id=permit.permit_id.bytes,
        phone_pubkey=os.urandom(32),
        passed_at=now_ts - 60,
    )

    with pytest.raises(ValueError, match="reader_id mismatch"):
        await process_passage_receipt(db_session, blob, reader)


@pytest.mark.asyncio
async def test_ingest_passage_rejects_too_old(db_session, test_user):
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    permit = await _make_permit_for_user(db_session, test_user["user_id"], reader.reader_id)

    very_old = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    blob = _build_receipt(
        reader_id=reader.reader_id,
        reader_priv=bytes(reader_sk),
        key_id=os.urandom(16),
        permit_id=permit.permit_id.bytes,
        phone_pubkey=os.urandom(32),
        passed_at=very_old,
    )

    with pytest.raises(ValueError, match="too old"):
        await process_passage_receipt(db_session, blob, reader)


@pytest.mark.asyncio
async def test_ingest_passage_resolves_unknown_permit_as_null_user(db_session, now_ts):
    """Если permit_id неизвестен (ex: permit удалён), user_id = NULL,
    но receipt всё равно сохраняется — это улика."""
    from scud.domain.reports import process_passage_receipt

    reader_sk = SigningKey.generate()
    reader = await _make_reader(db_session, reader_sk)
    rand_permit_id = uuid.uuid4().bytes

    blob = _build_receipt(
        reader_id=reader.reader_id,
        reader_priv=bytes(reader_sk),
        key_id=os.urandom(16),
        permit_id=rand_permit_id,
        phone_pubkey=os.urandom(32),
        passed_at=now_ts - 30,
    )

    await process_passage_receipt(db_session, blob, reader)
    rows = (await db_session.execute(select(PassageEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id is None
