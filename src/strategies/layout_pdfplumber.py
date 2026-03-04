from __future__ import annotations

from collections import defaultdict
from statistics import mean

import pdfplumber

from models.schemas import BoundingBox, DocumentProfile, ExtractedDocument, Table, TextBlock
from refinery.config import RefineryConfig
from strategies.base import BaseExtractor, ExtractionResult


def _union_bbox(words: list[dict]) -> BoundingBox | None:
    if not words:
        return None
    x0 = min(float(w["x0"]) for w in words)
    top = min(float(w["top"]) for w in words)
    x1 = max(float(w["x1"]) for w in words)
    bottom = max(float(w["bottom"]) for w in words)
    return BoundingBox(x0=x0, top=top, x1=x1, bottom=bottom)


def _table_to_schema(t, page_number: int) -> Table:
    bbox = None
    try:
        x0, top, x1, bottom = t.bbox
        bbox = BoundingBox(x0=float(x0), top=float(top), x1=float(x1), bottom=float(bottom))
    except Exception:
        bbox = None

    raw = t.extract()
    rows: list[list[str]] = []
    for r in raw:
        if r is None:
            continue
        rows.append([(c or "").strip() for c in r])

    headers: list[str] = []
    body: list[list[str]] = rows
    if rows and sum(1 for c in rows[0] if c) >= max(1, len(rows[0]) // 2):
        headers = rows[0]
        body = rows[1:]

    return Table(page_number=page_number, bbox=bbox, headers=headers, rows=body)


class LayoutPdfPlumberExtractor(BaseExtractor):
    name = "layout"

    def extract(self, profile: DocumentProfile, config: RefineryConfig) -> ExtractionResult:
        blocks: list[TextBlock] = []
        reading_order: list[tuple[int, int]] = []
        tables: list[Table] = []

        page_scores: list[float] = []

        with pdfplumber.open(profile.source_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    words = page.extract_words(
                        keep_blank_chars=False,
                        use_text_flow=True,
                        extra_attrs=[],
                    )
                except Exception:
                    words = []

                # Group words into "lines" by top coordinate buckets.
                line_buckets: dict[int, list[dict]] = defaultdict(list)
                for w in words:
                    try:
                        key = int(float(w["top"]) // 6.0)  # ~6pt bucket
                    except Exception:
                        key = 0
                    line_buckets[key].append(w)

                for key in sorted(line_buckets.keys()):
                    ws = sorted(line_buckets[key], key=lambda d: float(d.get("x0", 0.0)))
                    text = " ".join((w.get("text") or "").strip() for w in ws).strip()
                    if not text:
                        continue
                    bbox = _union_bbox(ws)
                    blocks.append(TextBlock(text=text, page_number=page_number, bbox=bbox))
                    reading_order.append((page_number, len(blocks) - 1))

                # Tables with bbox + structured rows.
                try:
                    found = page.find_tables() or []
                except Exception:
                    found = []
                for t in found:
                    try:
                        tables.append(_table_to_schema(t, page_number=page_number))
                    except Exception:
                        continue

                # Rough confidence: did we get any structure?
                page_scores.append(1.0 if (words or found) else 0.2)

        doc_conf = float(mean(page_scores)) if page_scores else 0.0
        extracted = ExtractedDocument(
            doc_id=profile.doc_id,
            source_path=profile.source_path,
            strategy_used="layout",
            page_count=len(page_scores),
            text_blocks=blocks,
            tables=tables,
            figures=[],
            reading_order=reading_order,
            raw=None,
        )
        return ExtractionResult(extracted=extracted, confidence=doc_conf, cost_estimate_usd=0.0)

