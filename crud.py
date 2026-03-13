"""
database.py
───────────
Database layer — supports both SQLite (dev) and PostgreSQL (prod).

Switch between backends via the DATABASE_URL environment variable:
  SQLite    (default):  sqlite+aiosqlite:///./cin_results.db
  PostgreSQL:           postgresql+asyncpg://user:pass@host:5432/cin_db

Uses SQLAlchemy 2.x async API so DB I/O never blocks FastAPI's event loop.

Tables
  cin_results   — one row per processed document
  processing_logs — per-request audit trail
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, ForeignKey, text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship

# ─── Engine ───────────────────────────────────────────────────────────────────

# Defaults to a local SQLite file — zero config for development.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./cin_results.db",
)

# connect_args only meaningful for SQLite (enables WAL for concurrent reads)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,           # Set to True to log all SQL statements
    future=True,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ─── ORM Models ───────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class CINResult(Base):
    """
    Stores one extracted CIN record per uploaded document.

    The `cin_number` column is indexed for fast lookups by ID.
    Multiple records can share the same cin_number (re-submissions).
    """
    __tablename__ = "cin_results"

    id               = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cin_number       = Column(String(20), index=True, nullable=True)
    name             = Column(String(120), nullable=True)
    birth_date       = Column(String(10), nullable=True)   # ISO-8601 string
    issue_date       = Column(String(10), nullable=True)
    place_of_birth   = Column(String(80), nullable=True)
    raw_ocr_text     = Column(Text, nullable=True)
    has_warnings     = Column(Boolean, default=False, nullable=False)
    warnings_text    = Column(Text, nullable=True)         # JSON-serialised list
    original_filename = Column(String(255), nullable=True)
    created_at       = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Back-reference to all processing attempts for this record
    logs = relationship("ProcessingLog", back_populates="result", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<CINResult id={self.id} cin={self.cin_number} name={self.name!r}>"


class ProcessingLog(Base):
    """
    Per-request audit entry: tracks timing, status, and error messages.
    Useful for debugging OCR failures and monitoring pipeline performance.
    """
    __tablename__ = "processing_logs"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    result_id     = Column(Integer, ForeignKey("cin_results.id", ondelete="CASCADE"), nullable=True)
    status        = Column(String(20), nullable=False)   # "success" | "error" | "partial"
    duration_ms   = Column(Float, nullable=True)         # End-to-end processing time
    error_message = Column(Text, nullable=True)
    created_at    = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    result = relationship("CINResult", back_populates="logs")


# ─── Lifecycle ────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Create all tables if they don't exist.
    Called once at application startup via FastAPI lifespan.
    Safe to call multiple times (CREATE TABLE IF NOT EXISTS semantics).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info(f"Database ready → {DATABASE_URL}")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async DB session per request.
    Automatically commits on success, rolls back on exception.

    Usage in endpoint:
        async def my_endpoint(db: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise