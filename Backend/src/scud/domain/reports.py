"""Report processing domain (§6.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scud.crypto.sealed_box import decrypt_sealed_blob
from scud.crypto.serialization import (
    parse_blk_envelope,
    parse_blk_plaintext,
    parse_delivery_receipt,
    parse_fdi_blob,
    parse_fdi_courier_plaintext,
    parse_passage_receipt,
)
from scud.crypto.signing import (
    DOMAIN_BLK,
    DOMAIN_FDI,
    DOMAIN_PSG,
    DOMAIN_RCP,
    verify_detached,
)
from scud.db.models import DeliveryTask, IssuedKey, PassageEvent, Permit, Reader
from scud.db.repositories import tasks as task_repo


async def process_delivery_receipt(
    session: AsyncSession,
    raw: bytes,
    reader: Reader,
) -> None:
    receipt = parse_delivery_receipt(raw)

    if receipt.reader_id != reader.reader_id:
        raise ValueError("reader_id mismatch")

    if not verify_detached(reader.reader_pubkey, DOMAIN_RCP, raw[:48], receipt.signature):
        raise ValueError("invalid reader signature on delivery_receipt")

    # Find open delivery task for this version
    result = await session.execute(
        select(DeliveryTask).where(
            DeliveryTask.reader_id == reader.reader_id,
            DeliveryTask.filter_version == receipt.applied_filter_version,
            DeliveryTask.completed_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()
    if task is not None:
        now = datetime.now(timezone.utc)
        task.completed_at = now
        task.completion_source = "receipt"
        # courier_id resolution: not invertible, leave NULL
        task.courier_user_id = None

    # Update reader state
    await session.execute(
        update(Reader)
        .where(Reader.reader_id == reader.reader_id)
        .values(
            last_applied_filter_version=receipt.applied_filter_version,
            last_contact_at=datetime.now(timezone.utc),
            last_known_time=datetime.fromtimestamp(receipt.applied_at, tz=timezone.utc),
        )
    )

    # Promote revoked keys to revoked_in_bloom
    await session.execute(
        update(IssuedKey)
        .where(
            IssuedKey.reader_id == reader.reader_id,
            IssuedKey.status.in_(["revoked_by_server", "revoked_by_reader"]),
            IssuedKey.committed_filter_version.is_not(None),
            IssuedKey.committed_filter_version <= receipt.applied_filter_version,
        )
        .values(status="revoked_in_bloom", is_active=False)
    )

    # Phase 2 admin-revoke: после того как часть ключей перешла в
    # revoked_in_bloom (is_active=False), у некоторых "revoke pending" permit'ов
    # этого ридера могло обнулиться количество active-ключей — финализируем.
    from scud.domain.permits import finalize_pending_revokes
    await finalize_pending_revokes(session, reader.reader_id)

    await session.flush()


async def process_fdi_blob(
    session: AsyncSession,
    raw: bytes,
    reader: Reader,
) -> None:
    fdi = parse_fdi_blob(raw)

    if fdi.reader_id != reader.reader_id:
        raise ValueError("reader_id mismatch in FDI")

    # Verify reader_signature over bytes[0:145] with domain_FDI.
    # Firmware (ESP32 ops/fdi.cpp) signs DOMAIN_FDI || resp[0..145] — включая
    # result_marker по смещению 0. Slicing должен быть raw[0:145], не raw[1:145].
    if not verify_detached(reader.reader_pubkey, DOMAIN_FDI, raw[0:145], fdi.reader_signature):
        raise ValueError("invalid reader signature on FDI")

    # Decrypt courier blob
    plaintext = decrypt_sealed_blob(
        fdi.encrypted_courier_blob,
        reader.server_x25519_privkey,
        reader.server_x25519_pubkey,
    )
    courier_pt = parse_fdi_courier_plaintext(plaintext)

    if courier_pt.reader_id != fdi.reader_id:
        raise ValueError("FDI inner reader_id mismatch")
    if courier_pt.filter_version != fdi.filter_version:
        raise ValueError("FDI inner filter_version mismatch")

    # Same as delivery_receipt logic
    result = await session.execute(
        select(DeliveryTask).where(
            DeliveryTask.reader_id == reader.reader_id,
            DeliveryTask.filter_version == fdi.filter_version,
            DeliveryTask.completed_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()
    if task is not None:
        now = datetime.now(timezone.utc)
        task.completed_at = now
        task.completion_source = "fdi"

    await session.execute(
        update(Reader)
        .where(Reader.reader_id == reader.reader_id)
        .values(
            last_applied_filter_version=fdi.filter_version,
            last_contact_at=datetime.now(timezone.utc),
            last_known_time=datetime.fromtimestamp(fdi.reader_time_now, tz=timezone.utc),
        )
    )

    await session.execute(
        update(IssuedKey)
        .where(
            IssuedKey.reader_id == reader.reader_id,
            IssuedKey.status.in_(["revoked_by_server", "revoked_by_reader"]),
            IssuedKey.committed_filter_version.is_not(None),
            IssuedKey.committed_filter_version <= fdi.filter_version,
        )
        .values(status="revoked_in_bloom", is_active=False)
    )

    # Phase 2 admin-revoke: тот же триггер, что и в process_delivery_receipt.
    from scud.domain.permits import finalize_pending_revokes
    await finalize_pending_revokes(session, reader.reader_id)

    await session.flush()


async def process_blk_report(
    session: AsyncSession,
    raw: bytes,
    reader: Reader,
) -> None:
    envelope = parse_blk_envelope(raw)

    if envelope.reader_id != reader.reader_id:
        raise ValueError("reader_id mismatch in BLK")

    # Verify reader signature over cleartext envelope (all except sig and nonce).
    # Firmware (ESP32 ops/blacklist.cpp) подписывает DOMAIN_BLK || resp[0 : 29+blob_len]
    # — включая result_marker (0x94). Slicing с индекса 0, не 1.
    signed_payload = raw[0 : 29 + len(envelope.encrypted_blob)]
    if not verify_detached(
        reader.reader_pubkey, DOMAIN_BLK, signed_payload, envelope.reader_signature
    ):
        raise ValueError("invalid reader signature on BLK")

    # Decrypt
    plaintext = decrypt_sealed_blob(
        envelope.encrypted_blob,
        reader.server_x25519_privkey,
        reader.server_x25519_pubkey,
    )
    blk_pt = parse_blk_plaintext(plaintext)

    if blk_pt.reader_id != envelope.reader_id:
        raise ValueError("BLK inner reader_id mismatch")
    if blk_pt.reader_time != envelope.reader_time:
        raise ValueError("BLK inner reader_time mismatch")

    # Update reader contact
    await session.execute(
        update(Reader)
        .where(Reader.reader_id == reader.reader_id)
        .values(
            last_contact_at=datetime.now(timezone.utc),
            last_known_time=datetime.fromtimestamp(envelope.reader_time, tz=timezone.utc),
        )
    )

    # Update key statuses — is_active=False for revoked_by_reader (mirrors PG trigger)
    any_affected = False
    for entry in blk_pt.entries:
        # Check if key exists and needs update
        check = await session.execute(
            select(IssuedKey.key_id).where(
                IssuedKey.key_id == entry.key_id,
                IssuedKey.status.in_(["active", "revoked_by_server"]),
            )
        )
        if check.scalar_one_or_none() is not None:
            await session.execute(
                update(IssuedKey)
                .where(IssuedKey.key_id == entry.key_id)
                .values(
                    status="revoked_by_reader",
                    is_active=False,
                    revoke_reason=1,
                    revoked_at=datetime.fromtimestamp(entry.revoked_at, tz=timezone.utc),
                )
            )
            any_affected = True

    if any_affected:
        await task_repo.enqueue_generate_filter_debounced(
            session, reader.reader_id.hex()
        )

    await session.flush()


async def process_passage_receipt(
    session: AsyncSession,
    raw: bytes,
    reader: Reader,
    delivered_by_user_id: int | None = None,
) -> None:
    """Принимает 192-байтовую passage_receipt (shared §15).

    Делает: parse → signature verify → dedup → resolve user → insert.
    Идемпотентна по (reader_id, receipt_nonce): дубль не считается ошибкой,
    INSERT в таком случае молча пропускается.
    """
    receipt = parse_passage_receipt(raw)

    if receipt.reader_id != reader.reader_id:
        raise ValueError("reader_id mismatch in passage_receipt")

    if not verify_detached(
        reader.reader_pubkey, DOMAIN_PSG, receipt.signed_payload, receipt.signature
    ):
        raise ValueError("invalid reader signature on passage_receipt")

    # Sanity-check timestamp. 30 дней назад / 1 день вперёд (на случай дрейфа RTC).
    now = datetime.now(timezone.utc)
    passed_dt = datetime.fromtimestamp(receipt.passed_at, tz=timezone.utc)
    max_age = 30 * 86400
    max_future = 86400
    if (now - passed_dt).total_seconds() > max_age:
        raise ValueError(
            f"passage_receipt too old: passed_at={passed_dt.isoformat()}"
        )
    if (passed_dt - now).total_seconds() > max_future:
        raise ValueError(
            f"passage_receipt too far in future: passed_at={passed_dt.isoformat()}"
        )

    # Dedup. Проверяем до INSERT — это даёт чистый «idempotent OK» без
    # IntegrityError → rollback дорогих сайдэффектов.
    existing = await session.execute(
        select(PassageEvent.event_id).where(
            PassageEvent.reader_id == receipt.reader_id,
            PassageEvent.receipt_nonce == receipt.receipt_nonce,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return  # idempotent

    # Резолвим user_id через permit. NULL допустим: квитанция могла придти
    # уже после удаления permit'а (это всё равно ценная улика).
    # permit_id из receipt — это raw 16 B; для UUIDType колонки нужен UUID.
    permit_uuid = uuid.UUID(bytes=receipt.permit_id)
    permit_lookup = await session.execute(
        select(Permit.user_id).where(Permit.permit_id == permit_uuid)
    )
    user_id: int | None = permit_lookup.scalar_one_or_none()

    event = PassageEvent(
        reader_id=receipt.reader_id,
        receipt_nonce=receipt.receipt_nonce,
        key_id=receipt.key_id,
        permit_id=permit_uuid,
        user_id=user_id,
        phone_pubkey=receipt.phone_pubkey,
        passed_at=passed_dt,
        direction=receipt.direction,
        session_seq=receipt.session_seq,
        verdict=receipt.verdict,
        flags=receipt.flags,
        delivered_by=delivered_by_user_id,
        raw_receipt=receipt.raw,
    )
    session.add(event)

    # Last-contact обновляем, но без перетирания свежего last_known_time
    # (passed_at может быть старее, чем уже записанный).
    await session.execute(
        update(Reader)
        .where(Reader.reader_id == reader.reader_id)
        .values(last_contact_at=now)
    )

    # Webhook fan-out: пользовательские интеграции (CRM, табельные). Не блокируем
    # ingestion — задача ставится в очередь, фактический POST делает worker.
    # Импорт локальный: модуль notify_webhook тянет httpx, незачем грузить его
    # при импорте scud.domain.reports.
    from scud.worker_handlers.notify_webhook import enqueue_for_event
    await enqueue_for_event(session, "passage_event", {
        "event_id": str(event.event_id),
        "reader_id": receipt.reader_id.hex(),
        "key_id": receipt.key_id.hex(),
        "permit_id": str(permit_uuid),
        "user_id": user_id,
        "phone_pubkey": receipt.phone_pubkey.hex(),
        "passed_at": passed_dt.isoformat(),
        "direction": receipt.direction,
        "session_seq": receipt.session_seq,
    })

    await session.flush()


async def dispatch_report(
    session: AsyncSession,
    report_type: str,
    raw: bytes,
    reader: Reader,
    delivered_by_user_id: int | None = None,
) -> None:
    if report_type == "delivery_receipt":
        await process_delivery_receipt(session, raw, reader)
    elif report_type == "filter_delivery_info":
        await process_fdi_blob(session, raw, reader)
    elif report_type == "blacklist_report":
        await process_blk_report(session, raw, reader)
    elif report_type == "passage_receipt":
        await process_passage_receipt(session, raw, reader, delivered_by_user_id)
    else:
        raise ValueError(f"unknown report_type: {report_type}")
