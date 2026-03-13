"""
utils.py
────────
Low-level helpers shared across the pipeline:
  • PDF → list of PIL Images
  • OpenCV preprocessing (deskew, denoise, contrast enhancement)
  • Temporary-file management
"""

from __future__ import annotations

import math
import tempfile
import uuid
from pathlib import Path
from typing import List

import cv2
import fitz  # PyMuPDF
import numpy as np
from loguru import logger
from PIL import Image


# ─── Constants ────────────────────────────────────────────────────────────────
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# DPI used when rasterising PDF pages (higher = better OCR, slower)
PDF_RENDER_DPI = 200


# ─── PDF Utilities ────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path: str | Path, dpi: int = PDF_RENDER_DPI) -> List[Image.Image]:
    """
    Convert every page of a PDF file into a list of PIL Image objects.

    PyMuPDF (fitz) renders vector content faithfully and is much faster
    than pdf2image/poppler for server workloads.

    Args:
        pdf_path: Path to the PDF file.
        dpi:      Rendering resolution (dots-per-inch). 200 is a good tradeoff.

    Returns:
        Ordered list of PIL Images, one per page.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    images: List[Image.Image] = []
    zoom = dpi / 72.0  # fitz uses 72 dpi as its base

    with fitz.open(str(pdf_path)) as doc:
        logger.debug(f"Rendering {len(doc)} page(s) from '{pdf_path.name}' @ {dpi} dpi")
        for page_num, page in enumerate(doc):
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            # Convert raw pixmap bytes → PIL Image (RGB)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
            logger.debug(f"  Page {page_num + 1}: {img.size}")

    return images


# ─── OpenCV Preprocessing ─────────────────────────────────────────────────────

def pil_to_cv2(image: Image.Image) -> np.ndarray:
    """Convert a PIL Image (RGB) to an OpenCV ndarray (BGR)."""
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def cv2_to_pil(array: np.ndarray) -> Image.Image:
    """Convert an OpenCV ndarray (BGR) back to a PIL Image (RGB)."""
    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def deskew(image: np.ndarray) -> np.ndarray:
    """
    Correct small rotations in a scanned document image.

    Algorithm:
      1. Convert to grayscale + binary (Otsu threshold).
      2. Detect all non-zero pixel coordinates.
      3. Use cv2.minAreaRect to find the dominant text angle.
      4. Rotate the original colour image by that angle.

    Skips correction if the detected angle is < 0.5° (avoid unnecessary
    interpolation artifacts on already-straight images).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(binary > 0))
    if coords.shape[0] < 10:
        # Not enough content to estimate skew
        return image

    angle = cv2.minAreaRect(coords)[-1]

    # minAreaRect returns angles in [-90, 0); normalise to [-45, 45]
    if angle < -45:
        angle += 90

    if abs(angle) < 0.5:
        return image  # Already straight

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    logger.debug(f"Deskewed by {angle:.2f}°")
    return rotated


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    """
    Apply CLAHE (Contrast-Limited Adaptive Histogram Equalisation) on the
    luminance channel so text pops against the background without blowing
    out already-bright regions.

    Works in LAB colour space to avoid hue shifts.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)

    lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
    return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)


def remove_noise(image: np.ndarray) -> np.ndarray:
    """
    Apply a fast bilateral filter to reduce scanner noise while preserving
    sharp text edges (unlike a simple Gaussian blur).
    """
    # d=9 neighbourhood diameter; sigmaColor/sigmaSpace=75 are balanced defaults
    return cv2.bilateralFilter(image, d=9, sigmaColor=75, sigmaSpace=75)


def preprocess_image(pil_image: Image.Image) -> np.ndarray:
    """
    Full preprocessing pipeline for a single document page:
      1. Deskew
      2. Contrast enhancement (CLAHE)
      3. Noise removal (bilateral filter)

    Returns an OpenCV ndarray ready to be passed to PaddleOCR.
    """
    cv_img = pil_to_cv2(pil_image)
    cv_img = deskew(cv_img)
    cv_img = enhance_contrast(cv_img)
    cv_img = remove_noise(cv_img)
    return cv_img


# ─── Temp-File Helpers ────────────────────────────────────────────────────────

def save_temp_file(data: bytes, suffix: str = ".pdf") -> Path:
    """
    Persist uploaded binary data to a uniquely-named file in `temp/`.

    Using a UUID guarantees no collision under concurrent requests.
    """
    filename = f"{uuid.uuid4().hex}{suffix}"
    dest = TEMP_DIR / filename
    dest.write_bytes(data)
    logger.info(f"Saved temp file → {dest}")
    return dest


def cleanup_temp_file(path: str | Path) -> None:
    """
    Silently remove a temporary file after processing.
    Errors are logged but never raised (best-effort cleanup).
    """
    try:
        Path(path).unlink(missing_ok=True)
        logger.debug(f"Cleaned up temp file: {path}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not delete temp file {path}: {exc}")