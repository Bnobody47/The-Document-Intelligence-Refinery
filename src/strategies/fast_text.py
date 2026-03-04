from __future__ import annotations

from statistics import mean

import pdfplumber

from models.schemas import BoundingBox, DocumentProfile, ExtractedDocument, TextBlock
from refinery.config import RefineryConfig
from strategies.base import BaseExtractor, ExtractionResult


def _bbox_area(x0: float, top: float, x1: float, bottom: float) -> float:
    return max(0.0, x1 - x0) * max(0.0, bottom - top)


def _safe_div(num: float, den: float) -> float:
    return 0.0 if den <= 0 else num / den


def _page_fasttext_confidence(page: pdfplumber.page.Page) -> float:
    page_area = float(page.width * page.height)
    chars = len(page.chars or [])
    char_density = _safe_div(chars, page_area)

    images = page.images or []
    img_area = 0.0
    for im in images:
        try:
            img_area += _bbox_area(float(im["x0"]), float(im["top"]), float(im["x1"]), float(im["bottom"]))
        except Exception:
            continue
    image_ratio = max(0.0, min(1.0, _safe_div(img_area, page_area)))

    # Simple bounded scoring.
    chars_score = min(1.0, chars / 800.0)
    density_score = min(1.0, char_density / 0.0012)
    image_score = 1.0 - min(1.0, image_ratio / 0.65)

    score = 0.50 * chars_score + 0.30 * density_score + 0.20 * image_score
    return float(max(0.0, min(1.0, score)))


class FastTextExtractor(BaseExtractor):
    name = "fast_text"

    def extract(self, profile: DocumentProfile, config: RefineryConfig) -> ExtractionResult:
        blocks: list[TextBlock] = []
        reading_order: list[tuple[int, int]] = []
        page_scores: list[float] = []

        with pdfplumber.open(profile.source_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                page_scores.append(_page_fasttext_confidence(page))
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""

                # Full-page bbox as a coarse provenance anchor.
                bbox = BoundingBox(x0=0.0, top=0.0, x1=float(page.width), bottom=float(page.height))
                blocks.append(TextBlock(text=text, page_number=i, bbox=bbox))
                reading_order.append((i, len(blocks) - 1))

        doc_conf = float(mean(page_scores)) if page_scores else 0.0
        extracted = ExtractedDocument(
            doc_id=profile.doc_id,
            source_path=profile.source_path,
            strategy_used="fast_text",
            page_count=len(page_scores),
            text_blocks=blocks,
            tables=[],
            figures=[],
            reading_order=reading_order,
            raw=None,
        )
        return ExtractionResult(extracted=extracted, confidence=doc_conf, cost_estimate_usd=0.0)

