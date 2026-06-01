"""App reports endpoint (§5.6)."""

from __future__ import annotations

import base64
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from scud.api.deps import CurrentUser, DbSession
from scud.db.repositories import readers as reader_repo
from scud.db.repositories import reports as report_repo
from scud.db.repositories import tasks as task_repo

router = APIRouter(prefix="/reports", tags=["app-reports"])

VALID_REPORT_TYPES = {
    "delivery_receipt",
    "filter_delivery_info",
    "blacklist_report",
    "passage_receipt",
}

# Minimum byte sizes per report type for basic sanity check.
# passage_receipt уникален тем, что имеет фиксированный размер 192 B (см. §15.3);
# проверка <192 отсекает мусор не доставляя его до crypto-verify.
MIN_SIZES = {
    "delivery_receipt": 112,
    "filter_delivery_info": 241,
    "blacklist_report": 29 + 82 + 64 + 32,  # minimum with 0 entries
    "passage_receipt": 192,
}

# Жёсткий потолок размера. Большинство report'ов — фиксированной длины;
# у BLK размер ≤ 8399 B (N=256, см. §5.9). 16 KB — комфортный запас.
MAX_REPORT_SIZE = 16 * 1024


class ReportItem(BaseModel):
    type: str
    target_reader_id: str  # hex
    bytes: str  # base64


class ReportsRequest(BaseModel):
    reports: list[ReportItem]


@router.post("/submit", response_model=dict)
async def submit_reports(
    body: ReportsRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []

    for idx, item in enumerate(body.reports):
        # Validate type
        if item.type not in VALID_REPORT_TYPES:
            rejected.append({"index": idx, "reason": f"unknown_type:{item.type}"})
            continue

        # Decode bytes
        try:
            raw = base64.b64decode(item.bytes)
        except Exception:
            rejected.append({"index": idx, "reason": "invalid_base64"})
            continue

        # Sanity check size
        min_size = MIN_SIZES.get(item.type, 1)
        if len(raw) < min_size:
            rejected.append({"index": idx, "reason": f"too_short:{len(raw)}"})
            continue
        if len(raw) > MAX_REPORT_SIZE:
            rejected.append({"index": idx, "reason": f"too_large:{len(raw)}"})
            continue
        # passage_receipt — фиксированной длины. Сразу отсекаем "длинный" формат:
        # для passage это всегда баг или попытка прокинуть мусор в очередь.
        if item.type == "passage_receipt" and len(raw) != 192:
            rejected.append({"index": idx, "reason": f"bad_size:{len(raw)}"})
            continue

        # Validate reader
        try:
            reader_id = bytes.fromhex(item.target_reader_id)
        except ValueError:
            rejected.append({"index": idx, "reason": "invalid_reader_id_hex"})
            continue

        reader = await reader_repo.get_reader_by_id(db, reader_id)
        if reader is None or not reader.is_active:
            rejected.append({"index": idx, "reason": "reader_not_found"})
            continue

        # Insert report
        report = await report_repo.insert_report(
            db,
            report_type=item.type,
            reader_id=reader_id,
            raw_bytes=raw,
            delivered_by_user_id=current_user.user_id,
        )

        # Enqueue processing
        await task_repo.enqueue_task(
            db,
            task_type="process_report",
            payload={"report_id": str(report.report_id)},
        )

        accepted.append(str(report.report_id))

    await db.commit()
    return {"accepted": accepted, "rejected": rejected}
