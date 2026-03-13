"""
extractor.py
────────────
Field extraction from raw OCR text.

Architecture (easy to extend):
  1. RegexExtractor  – fast, deterministic, handles well-formatted scans.
  2. NLPExtractor    – keyword/proximity search, used when regex fails.
  3. CINExtractor    – orchestrates both layers with a confidence model.

Adding an ML extractor later only requires implementing the same
`_extract_*` interface and inserting it into CINExtractor._pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from dateutil import parser as dateutil_parser
from loguru import logger


# ─── Result Schema ────────────────────────────────────────────────────────────

@dataclass
class CINFields:
    """
    Structured container for extracted CIN document fields.
    All fields are Optional – the caller decides how to handle None values.
    """
    cin_number: Optional[str] = None
    name: Optional[str] = None
    birth_date: Optional[str] = None   # ISO-8601: YYYY-MM-DD
    issue_date: Optional[str] = None   # ISO-8601: YYYY-MM-DD
    place_of_birth: Optional[str] = None
    # Confidence flags help downstream code decide whether to request manual review
    extraction_warnings: list = field(default_factory=list)


# ─── Normalisation Helpers ────────────────────────────────────────────────────

def _normalise_date(raw: str) -> Optional[str]:
    """
    Parse a date string in any reasonable format (DD/MM/YYYY, YYYY-MM-DD,
    DD-MM-YYYY, 12 janvier 2000, …) and return ISO-8601 YYYY-MM-DD.

    Returns None if parsing fails.
    """
    raw = raw.strip().replace(".", "/").replace(" ", " ")
    try:
        parsed: date = dateutil_parser.parse(raw, dayfirst=True)
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        logger.debug(f"Could not parse date: {raw!r}")
        return None


def _clean_name(raw: str) -> str:
    """Remove stray OCR artefacts from a name string."""
    # Keep letters (including accented), spaces, hyphens, apostrophes
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ\s\-\']", "", raw)
    # Collapse multiple spaces
    return re.sub(r"\s{2,}", " ", cleaned).strip().title()


# ─── Layer 1: Regex Extractor ─────────────────────────────────────────────────

class RegexExtractor:
    """
    Rule-based extractor for Moroccan CIN documents.

    CIN number format: 1-2 letters followed by 5-6 digits  e.g. AB123456, BK45678
    Dates: DD/MM/YYYY or DD-MM-YYYY common on Moroccan IDs

    Each pattern targets both the French label and its Arabic equivalent
    (transliterated triggers are omitted for brevity – Arabic is handled by
    proximity search in NLPExtractor).
    """

    # CIN: 1-2 uppercase letters + 5-6 digits (Moroccan national ID format)
    _CIN_PATTERN = re.compile(
        r"\b([A-Z]{1,2}\d{5,6})\b",
        re.IGNORECASE,
    )

    # Dates: DD/MM/YYYY, DD-MM-YYYY, YYYY/MM/DD, YYYY-MM-DD
    _DATE_PATTERN = re.compile(
        r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}|\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2})\b"
    )

    # Name label triggers (French)
    _NAME_TRIGGERS = re.compile(
        r"(?:Nom\s*(?:et\s*[Pp]r[eé]nom)?|[Pp]r[eé]nom|Titulaire)[:\s]+([A-Za-zÀ-ÿ\s\-\']{3,60})",
        re.IGNORECASE,
    )

    # Date-of-birth label triggers
    _DOB_TRIGGERS = re.compile(
        r"(?:N[eé]\s*le|Date\s*de\s*naissance|D\.?\s*N\.?)[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        re.IGNORECASE,
    )

    # Issue date label triggers
    _ISSUE_TRIGGERS = re.compile(
        r"(?:D[ée]livr[eé]e?\s*le|Date\s*(?:de\s*)?(?:d[eé]livrance|d[eé]livr[eé]e?)|Valable\s*jusqu)[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        re.IGNORECASE,
    )

    # Place of birth label triggers
    _POB_TRIGGERS = re.compile(
        r"(?:[Aa]\s*|[Ll]ieu\s*(?:de\s*)?naissance|N[eé]\s*[aà])[:\s]+([A-Za-zÀ-ÿ\s\-]{3,40})",
        re.IGNORECASE,
    )

    def extract(self, text: str) -> CINFields:
        result = CINFields()

        # ── CIN number ────────────────────────────────────────────────────────
        cin_match = self._CIN_PATTERN.search(text)
        if cin_match:
            result.cin_number = cin_match.group(1).upper()
            logger.debug(f"[Regex] CIN: {result.cin_number}")

        # ── Name ──────────────────────────────────────────────────────────────
        name_match = self._NAME_TRIGGERS.search(text)
        if name_match:
            result.name = _clean_name(name_match.group(1))
            logger.debug(f"[Regex] Name: {result.name}")

        # ── Date of birth ─────────────────────────────────────────────────────
        dob_match = self._DOB_TRIGGERS.search(text)
        if dob_match:
            result.birth_date = _normalise_date(dob_match.group(1))
            logger.debug(f"[Regex] DOB: {result.birth_date}")

        # ── Issue date ────────────────────────────────────────────────────────
        issue_match = self._ISSUE_TRIGGERS.search(text)
        if issue_match:
            result.issue_date = _normalise_date(issue_match.group(1))
            logger.debug(f"[Regex] Issue: {result.issue_date}")

        # ── Place of birth ────────────────────────────────────────────────────
        pob_match = self._POB_TRIGGERS.search(text)
        if pob_match:
            result.place_of_birth = _clean_name(pob_match.group(1))
            logger.debug(f"[Regex] POB: {result.place_of_birth}")

        return result


# ─── Layer 2: NLP / Proximity Extractor ──────────────────────────────────────

class NLPExtractor:
    """
    Lightweight keyword-proximity extractor used as a fallback when regex
    cannot find labelled fields (common with heavily scanned or OCR-garbled text).

    Strategy:
      • Split text into lines.
      • For each target field, scan lines for a trigger keyword.
      • The extracted value is taken from the *same line* (after the keyword)
        or the *next non-empty line* (common in OCR output where label and
        value land on separate lines).

    This is intentionally simple; it can be replaced by a fine-tuned NER
    model (e.g., CamemBERT for French) with zero changes to the orchestrator.
    """

    # Each entry: (field_name, [trigger_keywords_lowercase])
    _FIELD_TRIGGERS = {
        "cin_number": ["carte nationale", "cin", "n°", "numéro"],
        "name": ["nom", "prénom", "titulaire", "اسم"],
        "birth_date": ["né le", "naissance", "date de naissance", "تاريخ الازدياد"],
        "issue_date": ["délivrée le", "délivrance", "date", "صالحة"],
        "place_of_birth": ["lieu", "né à", "مكان الازدياد"],
    }

    def _find_value_near_keyword(self, lines: list[str], keywords: list[str]) -> Optional[str]:
        """
        Walk through document lines looking for a keyword; return the value
        fragment found on the same line (after ':') or on the immediately
        following non-empty line.
        """
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if any(kw in line_lower for kw in keywords):
                # Try same-line value: text after last ':' or after keyword
                if ":" in line:
                    value = line.split(":", 1)[-1].strip()
                    if value:
                        return value
                # Try next non-empty line
                for j in range(i + 1, min(i + 3, len(lines))):
                    candidate = lines[j].strip()
                    if candidate:
                        return candidate
        return None

    def extract(self, text: str, existing: CINFields) -> CINFields:
        """Fill in any None fields in `existing` using proximity heuristics."""
        lines = [ln.strip() for ln in text.splitlines()]
        result = existing

        if not result.cin_number:
            raw = self._find_value_near_keyword(lines, self._FIELD_TRIGGERS["cin_number"])
            if raw:
                m = re.search(r"[A-Z]{1,2}\d{5,6}", raw.upper())
                if m:
                    result.cin_number = m.group(0)
                    logger.debug(f"[NLP] CIN: {result.cin_number}")

        if not result.name:
            raw = self._find_value_near_keyword(lines, self._FIELD_TRIGGERS["name"])
            if raw:
                result.name = _clean_name(raw)
                logger.debug(f"[NLP] Name: {result.name}")

        if not result.birth_date:
            raw = self._find_value_near_keyword(lines, self._FIELD_TRIGGERS["birth_date"])
            if raw:
                m = re.search(r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}", raw)
                if m:
                    result.birth_date = _normalise_date(m.group(0))
                    logger.debug(f"[NLP] DOB: {result.birth_date}")

        if not result.issue_date:
            raw = self._find_value_near_keyword(lines, self._FIELD_TRIGGERS["issue_date"])
            if raw:
                m = re.search(r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}", raw)
                if m:
                    result.issue_date = _normalise_date(m.group(0))
                    logger.debug(f"[NLP] Issue: {result.issue_date}")

        if not result.place_of_birth:
            raw = self._find_value_near_keyword(lines, self._FIELD_TRIGGERS["place_of_birth"])
            if raw:
                result.place_of_birth = _clean_name(raw)
                logger.debug(f"[NLP] POB: {result.place_of_birth}")

        return result


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class CINExtractor:
    """
    Top-level extractor that chains RegexExtractor → NLPExtractor.

    Adding a third ML-based layer in the future:
      self._pipeline.append(MLExtractor())
    and implement `MLExtractor.extract(text, existing) -> CINFields`.
    """

    def __init__(self):
        self._regex = RegexExtractor()
        self._nlp = NLPExtractor()

    def extract(self, ocr_text: str) -> CINFields:
        """
        Run the full extraction pipeline on raw OCR text.

        Returns a CINFields dataclass. Any field still None after both passes
        is recorded as a warning so the API can flag it for the caller.
        """
        logger.info("Starting field extraction pipeline…")

        # Pass 1 – Regex
        fields = self._regex.extract(ocr_text)

        # Pass 2 – NLP fallback for any missing fields
        fields = self._nlp.extract(ocr_text, fields)

        # Audit warnings
        missing = [
            f for f in ("cin_number", "name", "birth_date", "issue_date", "place_of_birth")
            if getattr(fields, f) is None
        ]
        if missing:
            msg = f"Could not extract: {', '.join(missing)}"
            fields.extraction_warnings.append(msg)
            logger.warning(msg)
        else:
            logger.info("All fields extracted successfully.")

        return fields


# Module-level singleton – reuse across requests
cin_extractor = CINExtractor()