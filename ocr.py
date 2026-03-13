"""
ocr.py
──────
OCR layer: wraps PaddleOCR to extract raw text from document images.

Design decisions
  • A module-level singleton (`_ocr_engine`) is initialised once on first use
    (lazy init) so FastAPI worker startup is fast.
  • We run with lang="french" + a second Arabic pass when Arabic glyphs are
    detected, then merge results. PaddleOCR v2.7 supports both scripts.
  • The function signature accepts a preprocessed OpenCV ndarray so that
    utils.preprocess_image() can be swapped independently.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
from loguru import logger

# PaddleOCR import is deferred inside _get_engine() so the module can be
# imported without crashing if PaddlePaddle isn't installed yet (useful for
# unit-testing the extractor in isolation).
_ocr_engine_fr: Optional[object] = None
_ocr_engine_ar: Optional[object] = None


# ─── Engine Initialisation ────────────────────────────────────────────────────

def _get_engine_fr():
    """Lazy-load the French/Latin PaddleOCR engine (singleton)."""
    global _ocr_engine_fr
    if _ocr_engine_fr is None:
        from paddleocr import PaddleOCR  # noqa: PLC0415
        logger.info("Initialising PaddleOCR [French/Latin]…")
        _ocr_engine_fr = PaddleOCR(
            use_angle_cls=True,   # Auto-rotate 90°/180°/270° upside-down text
            lang="fr",            # French – also handles Latin chars, digits
            show_log=False,       # Suppress verbose PaddlePaddle output
        )
        logger.info("PaddleOCR [French] ready.")
    return _ocr_engine_fr


def _get_engine_ar():
    """Lazy-load the Arabic PaddleOCR engine (singleton)."""
    global _ocr_engine_ar
    if _ocr_engine_ar is None:
        from paddleocr import PaddleOCR  # noqa: PLC0415
        logger.info("Initialising PaddleOCR [Arabic]…")
        _ocr_engine_ar = PaddleOCR(
            use_angle_cls=True,
            lang="arabic",
            show_log=False,
        )
        logger.info("PaddleOCR [Arabic] ready.")
    return _ocr_engine_ar


# ─── Text Extraction ──────────────────────────────────────────────────────────

def _contains_arabic(text: str) -> bool:
    """Return True if the string contains at least one Arabic Unicode character."""
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def extract_text_from_image(image: np.ndarray) -> str:
    """
    Run OCR on a single preprocessed image and return concatenated text.

    Strategy:
      1. Run French/Latin engine → collect all detected lines.
      2. If the collected text appears to include Arabic characters, also run
         the Arabic engine and append those lines.
      3. Return the merged text block, preserving visual reading order.

    Args:
        image: OpenCV BGR ndarray (output of utils.preprocess_image).

    Returns:
        Newline-separated raw OCR text.
    """
    lines: List[str] = []

    # ── Pass 1: French / Latin ────────────────────────────────────────────────
    fr_engine = _get_engine_fr()
    fr_result = fr_engine.ocr(image, cls=True)

    if fr_result and fr_result[0]:
        for line in fr_result[0]:
            # Each line: [[box_coords], (text, confidence)]
            text, confidence = line[1]
            if confidence > 0.4 and text.strip():
                lines.append(text.strip())
                logger.debug(f"[FR] {text!r} ({confidence:.2f})")

    french_text = "\n".join(lines)

    # ── Pass 2: Arabic (if Arabic glyphs detected or as a fallback) ───────────
    # Always run Arabic pass for CIN documents because they are bilingual.
    ar_engine = _get_engine_ar()
    ar_result = ar_engine.ocr(image, cls=True)

    ar_lines: List[str] = []
    if ar_result and ar_result[0]:
        for line in ar_result[0]:
            text, confidence = line[1]
            if confidence > 0.4 and text.strip() and _contains_arabic(text):
                ar_lines.append(text.strip())
                logger.debug(f"[AR] {text!r} ({confidence:.2f})")

    # Merge: French lines first (structured fields), Arabic lines appended
    all_lines = lines + ar_lines
    return "\n".join(all_lines)


def ocr_pdf_pages(page_images: list) -> str:
    """
    Run OCR across all pages of a document (list of preprocessed ndarrays).

    Pages are separated by a blank line in the output so that field
    extractors can reason about page boundaries if needed.

    Args:
        page_images: List of OpenCV BGR ndarrays from utils.preprocess_image.

    Returns:
        Full document text as a single string.
    """
    page_texts: List[str] = []

    for idx, img in enumerate(page_images):
        logger.info(f"OCR → page {idx + 1}/{len(page_images)}")
        text = extract_text_from_image(img)
        page_texts.append(text)

    return "\n\n--- PAGE BREAK ---\n\n".join(page_texts)