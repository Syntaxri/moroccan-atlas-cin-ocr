"""
schemas.py
──────────
Pydantic v2 schemas used by the DB-backed API endpoints.

Separating schemas from ORM models keeps the API contract stable
even when the database schema evolves.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Shared ───────────────────────────────────────────────────────────────────

class ProcessingLogSchema(BaseModel):
    id: int
    status: str
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── CINResult ────────────────────────────────────────────────────────────────

class CINResultBase(BaseModel):
    """Fields shared between create and read schemas."""
    cin_number:     Optional[str] = Field(None, example="AB123456")
    name:           Optional[str] = Field(None, example="Ahmed Benali")
    birth_date:     Optional[str] = Field(None, example="2000-01-12")
    issue_date:     Optional[str] = Field(None, example="2018-03-05")
    place_of_birth: Optional[str] = Field(None, example="Casablanca")


class CINResultCreate(CINResultBase):
    """Input schema (internal use — not exposed directly via API)."""
    raw_ocr_text:       Optional[str] = None
    warnings:           List[str]     = []
    original_filename:  Optional[str] = None


class CINResultRead(CINResultBase):
    """Full read schema returned by GET endpoints."""
    id:                int
    has_warnings:      bool
    warnings:          List[str]     = []
    original_filename: Optional[str] = None
    created_at:        datetime
    logs:              List[ProcessingLogSchema] = []

    model_config = {"from_attributes": True}

    @field_validator("warnings", mode="before")
    @classmethod
    def parse_warnings(cls, v):
        """
        warnings_text is stored as a JSON string in the DB.
        Pydantic receives it as a raw string; we decode it here.
        """
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return [v] if v else []
        return v or []


class CINResultSummary(CINResultBase):
    """Lightweight schema used in paginated list responses."""
    id:           int
    has_warnings: bool
    created_at:   datetime

    model_config = {"from_attributes": True}


# ─── Paginated list ───────────────────────────────────────────────────────────

class PaginatedCINResults(BaseModel):
    total:   int
    skip:    int
    limit:   int
    results: List[CINResultSummary]


# ─── Stats ────────────────────────────────────────────────────────────────────

class ProcessingStats(BaseModel):
    total_processed: int
    success:         int
    partial:         int
    error:           int
    avg_duration_ms: float