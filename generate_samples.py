"""
generate_samples.py
────────────────────
Generates 10 mock Moroccan CIN PDF samples and saves them to data/cin_pdfs/.

These are placeholder documents for development and testing. They simulate
the bilingual (French + Arabic) layout of a real Moroccan CIN card.

Run once:
    pip install reportlab --break-system-packages
    python generate_samples.py
"""

from __future__ import annotations

import random
from pathlib import Path

from reportlab.lib.pagesizes import A6  # CIN cards are roughly A6
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path("data/cin_pdfs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Mock data pools ──────────────────────────────────────────────────────────
FIRST_NAMES_FR = ["Ahmed", "Fatima", "Mohamed", "Khadija", "Youssef",
                  "Aicha", "Hassan", "Zineb", "Omar", "Nadia"]
LAST_NAMES_FR  = ["Benali", "El Ouazzani", "Tazi", "Benaissa", "Chraibi",
                  "Benkirane", "Lahlou", "Ziani", "Mansouri", "Bouazza"]
CITIES         = ["Casablanca", "Rabat", "Fès", "Marrakech", "Agadir",
                  "Tanger", "Meknès", "Oujda", "Tétouan", "Salé"]
# Arabic first names (transliterations kept simple for mock data)
FIRST_NAMES_AR = ["أحمد", "فاطمة", "محمد", "خديجة", "يوسف",
                  "عائشة", "حسن", "زينب", "عمر", "نادية"]
CITIES_AR      = ["الدار البيضاء", "الرباط", "فاس", "مراكش", "أكادير",
                  "طنجة", "مكناس", "وجدة", "تطوان", "سلا"]


def _rand_cin() -> str:
    letters = random.choice(["A", "AB", "B", "BK", "C", "D", "EE", "G", "HH", "J"])
    digits  = str(random.randint(10000, 999999)).zfill(6)
    return letters + digits


def _rand_date(year_min: int, year_max: int) -> str:
    y = random.randint(year_min, year_max)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{d:02d}/{m:02d}/{y}"


def generate_cin_pdf(index: int) -> Path:
    """Create one mock CIN PDF and return its path."""
    fn_fr = random.choice(FIRST_NAMES_FR)
    ln_fr = random.choice(LAST_NAMES_FR)
    fn_ar = random.choice(FIRST_NAMES_AR)
    city_fr = random.choice(CITIES)
    city_ar = CITIES_AR[CITIES.index(city_fr)]
    cin    = _rand_cin()
    dob    = _rand_date(1970, 2003)
    issue  = _rand_date(2010, 2023)

    out_path = OUTPUT_DIR / f"cin_sample_{index:02d}.pdf"
    c = canvas.Canvas(str(out_path), pagesize=A6)
    w, h = A6

    # ── Background stripe ─────────────────────────────────────────────────────
    c.setFillColorRGB(0.13, 0.37, 0.65)   # Moroccan flag blue
    c.rect(0, h - 20 * mm, w, 20 * mm, fill=1, stroke=0)

    # ── Header ────────────────────────────────────────────────────────────────
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(w / 2, h - 10 * mm, "ROYAUME DU MAROC — المملكة المغربية")
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(w / 2, h - 15 * mm, "CARTE NATIONALE D'IDENTITÉ — بطاقة التعريف الوطنية")

    # ── CIN Number ───────────────────────────────────────────────────────────
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(10 * mm, h - 28 * mm, f"N° CIN: {cin}")

    # ── French fields ─────────────────────────────────────────────────────────
    c.setFont("Helvetica", 8)
    y = h - 36 * mm
    line_h = 7 * mm

    rows_fr = [
        ("Nom et Prénom:",     f"{fn_fr} {ln_fr}"),
        ("Né(e) le:",          dob),
        ("Lieu de naissance:", city_fr),
        ("Délivrée le:",       issue),
    ]
    for label, value in rows_fr:
        c.setFont("Helvetica-Bold", 7)
        c.drawString(10 * mm, y, label)
        c.setFont("Helvetica", 7)
        c.drawString(48 * mm, y, value)
        y -= line_h

    # ── Divider ───────────────────────────────────────────────────────────────
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.line(10 * mm, y + 3 * mm, w - 10 * mm, y + 3 * mm)
    y -= 4 * mm

    # ── Arabic fields (right-aligned, simple LTR placeholder) ────────────────
    c.setFont("Helvetica-Bold", 7)
    c.drawRightString(w - 10 * mm, y, f"الاسم الكامل: {fn_ar} {ln_fr}")
    y -= line_h
    c.drawRightString(w - 10 * mm, y, f"تاريخ الازدياد: {dob}")
    y -= line_h
    c.drawRightString(w - 10 * mm, y, f"مكان الازدياد: {city_ar}")
    y -= line_h
    c.drawRightString(w - 10 * mm, y, f"صالحة: {issue}")

    # ── Footer note ───────────────────────────────────────────────────────────
    c.setFont("Helvetica-Oblique", 5)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawCentredString(w / 2, 5 * mm, "SPECIMEN — Document fictif pour tests uniquement")

    c.save()
    return out_path


if __name__ == "__main__":
    print(f"Generating 10 mock CIN PDFs → {OUTPUT_DIR.resolve()}")
    for i in range(1, 11):
        p = generate_cin_pdf(i)
        print(f"  ✓ {p.name}")
    print("Done.")