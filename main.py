"""
main.py
───────
FastAPI application — CIN pipeline + persistent database layer.

Endpoints
  POST   /upload-cin/           → Save PDF, no OCR
  POST   /process-cin/          → Full pipeline → persist → return JSON
  GET    /results/               → Paginated list of all results
  GET    /results/{id}           → Single result by DB id
  GET    /results/cin/{number}   → All results for a given CIN number
  DELETE /results/{id}           → Delete a record
  GET    /stats                  → Aggregate processing statistics
  GET    /health                 → Liveness probe

Run:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import (
    Depends, FastAPI, File, HTTPException,
    Query, UploadFile, status,
)
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import crud
from database import get_session, init_db
from extractor import CINFields, cin_extractor
from ocr import ocr_pdf_pages
from schemas import (
    CINResultRead, CINResultSummary,
    PaginatedCINResults, ProcessingStats,
)
from utils import cleanup_temp_file, pdf_to_images, preprocess_image, save_temp_file


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup (idempotent)."""
    await init_db()
    yield


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CIN Document Processor",
    description=(
        "Moroccan CIN card OCR pipeline: PDF → preprocessing → "
        "PaddleOCR (FR + AR) → structured JSON → SQLite/PostgreSQL persistence."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Inline schemas ───────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    message:    str
    file_path:  str
    file_name:  str
    size_bytes: int


class CINProcessResponse(BaseModel):
    db_id:          int   = Field(..., description="DB record ID for later retrieval")
    cin_number:     Optional[str] = Field(None, example="AB123456")
    name:           Optional[str] = Field(None, example="Ahmed Benali")
    birth_date:     Optional[str] = Field(None, example="2000-01-12")
    issue_date:     Optional[str] = Field(None, example="2018-03-05")
    place_of_birth: Optional[str] = Field(None, example="Casablanca")
    raw_ocr_text:   Optional[str] = None
    warnings:       List[str]     = []
    duration_ms:    float         = Field(..., description="End-to-end processing time in ms")


class HealthResponse(BaseModel):
    status: str
    version: str


# ─── Guards ───────────────────────────────────────────────────────────────────

ALLOWED_CONTENT_TYPES = {
    "application/pdf", "application/x-pdf", "binary/octet-stream",
}
MAX_FILE_SIZE_MB = 20


def _validate_upload(file: UploadFile) -> None:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Only PDF files accepted. Got: {file.content_type}",
            )


# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    return HealthResponse(status="ok", version=app.version)


@app.get("/stats", response_model=ProcessingStats, tags=["System"])
async def get_stats(db: AsyncSession = Depends(get_session)):
    """Aggregate processing statistics."""
    return await crud.get_processing_stats(db)


# ─── Upload only ──────────────────────────────────────────────────────────────

@app.post(
    "/upload-cin/",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["CIN Processing"],
)
async def upload_cin(file: UploadFile = File(...)):
    """Save an uploaded PDF to temp/ — no OCR, no DB write."""
    _validate_upload(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_SIZE_MB} MB")
    temp_path = save_temp_file(content, suffix=".pdf")
    return UploadResponse(
        message="File uploaded successfully.",
        file_path=str(temp_path),
        file_name=file.filename or temp_path.name,
        size_bytes=len(content),
    )


# ─── Full pipeline ────────────────────────────────────────────────────────────

@app.post(
    "/process-cin/",
    response_model=CINProcessResponse,
    tags=["CIN Processing"],
    summary="Upload → OCR → extract fields → persist → return JSON",
)
async def process_cin(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    """
    Full pipeline:
    1. Save PDF to temp/
    2. Render pages + preprocess (deskew, CLAHE, denoise)
    3. PaddleOCR French + Arabic
    4. Regex → NLP extraction
    5. Persist CINResult + ProcessingLog
    6. Return JSON with db_id
    """
    _validate_upload(file)
    content = await file.read()
    original_filename = file.filename or "unknown.pdf"
    temp_path: Path = save_temp_file(content, suffix=".pdf")
    t_start = time.perf_counter()

    try:
        # PDF → images
        try:
            pil_pages = pdf_to_images(temp_path)
        except Exception as exc:
            ms = (time.perf_counter() - t_start) * 1000
            await crud.create_processing_log(db, result_id=None, status="error",
                                             duration_ms=ms, error_message=str(exc))
            raise HTTPException(status_code=422, detail=f"Cannot render PDF: {exc}") from exc

        if not pil_pages:
            raise HTTPException(status_code=422, detail="PDF has no renderable pages.")

        # Preprocess
        preprocessed = [preprocess_image(img) for img in pil_pages]

        # OCR
        try:
            raw_text = ocr_pdf_pages(preprocessed)
        except Exception as exc:
            ms = (time.perf_counter() - t_start) * 1000
            await crud.create_processing_log(db, result_id=None, status="error",
                                             duration_ms=ms, error_message=f"OCR: {exc}")
            raise HTTPException(status_code=500, detail=f"OCR error: {exc}") from exc

        # Extract
        fields: CINFields = cin_extractor.extract(raw_text)
        ms = (time.perf_counter() - t_start) * 1000

        # Persist
        record = await crud.create_cin_result(
            db,
            cin_number=fields.cin_number,
            name=fields.name,
            birth_date=fields.birth_date,
            issue_date=fields.issue_date,
            place_of_birth=fields.place_of_birth,
            raw_ocr_text=raw_text,
            warnings=fields.extraction_warnings,
            original_filename=original_filename,
        )
        await crud.create_processing_log(
            db, result_id=record.id,
            status="partial" if fields.extraction_warnings else "success",
            duration_ms=ms,
        )

        logger.info(f"Processed '{original_filename}' → db_id={record.id} ({ms:.0f} ms)")

        return CINProcessResponse(
            db_id=record.id,
            cin_number=fields.cin_number,
            name=fields.name,
            birth_date=fields.birth_date,
            issue_date=fields.issue_date,
            place_of_birth=fields.place_of_birth,
            raw_ocr_text=raw_text,
            warnings=fields.extraction_warnings,
            duration_ms=round(ms, 1),
        )

    finally:
        cleanup_temp_file(temp_path)


# ─── Results CRUD ─────────────────────────────────────────────────────────────

@app.get(
    "/results/",
    response_model=PaginatedCINResults,
    tags=["Results"],
)
async def list_results(
    skip:          int           = Query(0,     ge=0),
    limit:         int           = Query(20,    ge=1, le=100),
    search:        Optional[str] = Query(None,  description="Filter on CIN, name, or city"),
    warnings_only: bool          = Query(False, description="Only records needing manual review"),
    db: AsyncSession = Depends(get_session),
):
    """Paginated list of all processed CIN documents."""
    records, total = await crud.list_cin_results(
        db, skip=skip, limit=limit,
        search=search, warnings_only=warnings_only,
    )
    return PaginatedCINResults(total=total, skip=skip, limit=limit, results=records)


@app.get(
    "/results/cin/{cin_number}",
    response_model=List[CINResultSummary],
    tags=["Results"],
)
async def get_results_by_cin(
    cin_number: str,
    db: AsyncSession = Depends(get_session),
):
    """All submissions for a specific CIN card number (most recent first)."""
    records = await crud.get_results_by_cin_number(db, cin_number)
    if not records:
        raise HTTPException(status_code=404,
                            detail=f"No results for CIN '{cin_number.upper()}'.")
    return records


@app.get(
    "/results/{result_id}",
    response_model=CINResultRead,
    tags=["Results"],
)
async def get_result(result_id: int, db: AsyncSession = Depends(get_session)):
    """Full record including processing log history."""
    record = await crud.get_cin_result_by_id(db, result_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Result id={result_id} not found.")
    record.warnings = json.loads(record.warnings_text or "[]")
    return record


@app.delete(
    "/results/{result_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Results"],
)
async def delete_result(result_id: int, db: AsyncSession = Depends(get_session)):
    """Delete a record and its processing logs (cascade)."""
    if not await crud.delete_cin_result(db, result_id):
        raise HTTPException(status_code=404, detail=f"Result id={result_id} not found.")