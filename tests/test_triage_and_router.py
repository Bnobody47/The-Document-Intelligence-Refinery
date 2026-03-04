from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz  # pymupdf
from PIL import Image

from agents.extractor import extract_with_router
from agents.triage import classify_profile
from refinery.config import load_config


def _make_png_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_text_pdf(path: Path) -> None:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Hello refinery.\nThis is a digital PDF.")
        doc.save(str(path))
    finally:
        doc.close()


def _make_scanned_like_pdf(path: Path) -> None:
    doc = fitz.open()
    try:
        page = doc.new_page()
        png = _make_png_bytes(1200, 1600)
        page.insert_image(page.rect, stream=png)
        doc.save(str(path))
    finally:
        doc.close()


def _make_low_conf_fasttext_pdf(path: Path) -> None:
    # Some text, but large image area (below scanned threshold) to depress Strategy A confidence.
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "x")  # intentionally tiny char stream
        png = _make_png_bytes(1200, 1600)
        r = page.rect
        # Insert image covering ~40% of page area.
        img_rect = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + (r.height * 0.4))
        page.insert_image(img_rect, stream=png)
        doc.save(str(path))
    finally:
        doc.close()


def test_triage_detects_native_digital() -> None:
    cfg = load_config()
    with TemporaryDirectory() as td:
        p = Path(td) / "digital.pdf"
        _make_text_pdf(p)
        prof = classify_profile(p, cfg)
        assert prof.origin_type.value in {"native_digital", "mixed"}


def test_triage_detects_scanned_image() -> None:
    cfg = load_config()
    with TemporaryDirectory() as td:
        p = Path(td) / "scanned.pdf"
        _make_scanned_like_pdf(p)
        prof = classify_profile(p, cfg)
        assert prof.origin_type.value == "scanned_image"


def test_router_escalates_fasttext_to_layout_on_low_confidence() -> None:
    cfg = load_config()
    with TemporaryDirectory() as td:
        p = Path(td) / "lowconf.pdf"
        _make_low_conf_fasttext_pdf(p)
        outcome = extract_with_router(p, cfg, strategy="fast_text")
        assert outcome.escalated_from == "fast_text"
        assert outcome.extracted.strategy_used == "layout"

