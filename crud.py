"""
crud.py
───────
All database read / write operations — kept separate from routing logic
so they can be reused, tested, and swapped independently.

Functions are pure async and receive an AsyncSession injected by FastAPI's
dependency system (no global state).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import CINResult, ProcessingLog


# ─── CINResult CRUD ───────────────────────────────────────────────────────────

async def create_cin_result(
    db: AsyncSession,
    *,
    cin_number: Optional[str],
    name: Optional[str],
    birth_date: Optional[str],
    issue_date: Optional[str],
    place_of_birth: Optional[str],
    raw_ocr_text: Optional[str],
    warnings: List[str],
    original_filename: Optional[str] = None,
) -> CINResult:
    """
    Insert a new CINResult row and return the persisted object (with id set).
    """
    record = CINResult(
        cin_number=cin_number,
        name=name,
        birth_date=birth_date,
        issue_date=issue_date,
        place_of_birth=place_of_birth,
        raw_ocr_text=raw_ocr_text,
        has_warnings=bool(warnings),
        warnings_text=json.dumps(warnings, ensure_ascii=False) if warnings else None,
        original_filename=original_filename,
    )
    db.add(record)
    await db.flush()   # Assigns record.id without committing the transaction
    logger.info(f"Inserted CINResult id={record.id} cin={cin_number!r}")
    return record


async def get_cin_result_by_id(db: AsyncSession, result_id: int) -> Optional[CINResult]:
    """Fetch a single CINResult by its primary key, including its logs."""
    stmt = (
        select(CINResult)
        .where(CINResult.id == result_id)
        .options(selectinload(CINResult.logs))
    )
    row = await db.execute(stmt)
    return row.scalar_one_or_none()


async def get_results_by_cin_number(
    db: AsyncSession, cin_number: str
) -> List[CINResult]:
    """
    Return all records matching a CIN number (most recent first).
    Useful when the same card is submitted multiple times.
    """
    stmt = (
        select(CINResult)
        .where(CINResult.cin_number == cin_number.upper())
        .order_by(CINResult.created_at.desc())
    )
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


async def list_cin_results(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    warnings_only: bool = False,
) -> tuple[List[CINResult], int]:
    """
    Paginated list of all CINResult records.

    Args:
        skip:          Offset for pagination.
        limit:         Max rows to return (capped at 100).
        search:        Optional free-text filter on cin_number or name.
        warnings_only: If True, return only records with extraction warnings.

    Returns:
        Tuple of (records list, total count).
    """
    limit = min(limit, 100)  # Hard cap to avoid runaway queries

    base_stmt = select(CINResult)

    if search:
        pattern = f"%{search}%"
        base_stmt = base_stmt.where(
            or_(
                CINResult.cin_number.ilike(pattern),
                CINResult.name.ilike(pattern),
                CINResult.place_of_birth.ilike(pattern),
            )
        )

    if warnings_only:
        base_stmt = base_stmt.where(CINResult.has_warnings == True)  # noqa: E712

    # Total count (for pagination metadata)
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # Paginated results
    data_stmt = (
        base_stmt
        .order_by(CINResult.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = await db.execute(data_stmt)
    records = list(rows.scalars().all())

    return records, total


async def delete_cin_result(db: AsyncSession, result_id: int) -> bool:
    """
    Delete a CINResult (and its cascaded ProcessingLogs) by id.
    Returns True if a row was deleted, False if not found.
    """
    record = await get_cin_result_by_id(db, result_id)
    if record is None:
        return False
    await db.delete(record)
    logger.info(f"Deleted CINResult id={result_id}")
    return True


# ─── ProcessingLog CRUD ───────────────────────────────────────────────────────

async def create_processing_log(
    db: AsyncSession,
    *,
    result_id: Optional[int],
    status: str,
    duration_ms: Optional[float] = None,
    error_message: Optional[str] = None,
) -> ProcessingLog:
    """Record one processing attempt in the audit log."""
    log = ProcessingLog(
        result_id=result_id,
        status=status,
        duration_ms=duration_ms,
        error_message=error_message,
    )
    db.add(log)
    await db.flush()
    return log


async def get_processing_stats(db: AsyncSession) -> dict:
    """
    Aggregate stats for the /stats endpoint:
      - total documents processed
      - success / partial / error counts
      - average processing time
    """
    from sqlalchemy import case, avg

    stmt = select(
        func.count(ProcessingLog.id).label("total"),
        func.sum(case((ProcessingLog.status == "success", 1), else_=0)).label("success"),
        func.sum(case((ProcessingLog.status == "partial", 1), else_=0)).label("partial"),
        func.sum(case((ProcessingLog.status == "error",   1), else_=0)).label("error"),
        func.avg(ProcessingLog.duration_ms).label("avg_ms"),
    )
    row = (await db.execute(stmt)).one()
    return {
        "total_processed": row.total or 0,
        "success":         row.success or 0,
        "partial":         row.partial or 0,
        "error":           row.error or 0,
        "avg_duration_ms": round(row.avg_ms or 0, 1),
    }