from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from time import perf_counter

import httpx

from models.schemas import DocumentProfile, ExtractedDocument, Table, TextBlock
from refinery.config import RefineryConfig
from strategies.base import BaseExtractor, ExtractionResult


@dataclass(frozen=True)
class OpenRouterSettings:
    api_key: str
    model: str


def _settings() -> OpenRouterSettings:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001").strip()
    if not api_key:
        raise ValueError("Missing OPENROUTER_API_KEY for vision extraction.")
    return OpenRouterSettings(api_key=api_key, model=model)


def _render_page_png_bytes(pdf_path: str, page_index0: int) -> bytes:
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_index0)
        pix = page.get_pixmap(dpi=180)
        return pix.tobytes("png")
    finally:
        doc.close()


class VisionOpenRouterExtractor(BaseExtractor):
    name = "vision"

    def extract(self, profile: DocumentProfile, config: RefineryConfig) -> ExtractionResult:
        """
        Vision-based extraction with an explicit budget guard.

        The budget guard uses approximated per-page token usage and pricing from
        `extraction.budget_guard` to decide how many pages to send. If the estimated
        cost of adding another page would exceed `max_usd_per_document`, it stops
        adding pages and proceeds with the subset selected so far.
        """
        try:
            s = _settings()
        except Exception as e:
            extracted = ExtractedDocument(
                doc_id=profile.doc_id,
                source_path=profile.source_path,
                strategy_used="vision",
                page_count=profile.page_count,
                text_blocks=[],
                tables=[],
                figures=[],
                reading_order=[],
                raw={"error": f"vision_unavailable: {type(e).__name__}: {e}"},
            )
            return ExtractionResult(
                extracted=extracted,
                confidence=0.0,
                cost_estimate_usd=0.0,
                notes=["Vision extractor unavailable; set OPENROUTER_API_KEY to enable."],
            )
        rules = config.rules
        budget_cfg = (rules.get("extraction") or {}).get("budget_guard") or {}
        budget_cap = float(budget_cfg.get("max_usd_per_document", 1.50))

        pricing = budget_cfg.get("vision_pricing_per_1k") or {}
        p_in = float(pricing.get("prompt", 0.00060))
        p_out = float(pricing.get("completion", 0.00120))

        approx = budget_cfg.get("approx_tokens_per_page") or {}
        approx_prompt = float(approx.get("prompt", 1500))
        approx_completion = float(approx.get("completion", 500))

        # Conservative per-page cost estimate in USD.
        est_cost_per_page = (
            (approx_prompt / 1000.0) * p_in + (approx_completion / 1000.0) * p_out
        )

        max_pages_allowed = max(1, int(budget_cap // max(est_cost_per_page, 1e-9)))
        pages_to_consider = min(profile.page_count, max_pages_allowed)

        t0 = perf_counter()

        images_b64 = []
        for i in range(pages_to_consider):
            png = _render_page_png_bytes(profile.source_path, i)
            images_b64.append(base64.b64encode(png).decode("ascii"))

        prompt = (
            "Extract structured data from document page images.\n"
            "Return ONLY valid JSON with keys: text_blocks, tables.\n"
            "text_blocks: list of {page_number, text}.\n"
            "tables: list of {page_number, headers, rows}.\n"
            "Do not hallucinate values not visible in the page.\n"
        )

        payload = {
            "model": s.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        *[
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                            for b64 in images_b64
                        ],
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }

        headers = {"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=120) as client:
            r = client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()

        usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
        total_tokens = float(usage.get("total_tokens") or 0)

        # If usage is available, prefer that for cost; otherwise fall back to approximation.
        if total_tokens > 0:
            # Assume a 3:1 ratio of prompt:completion tokens when not broken out.
            est_cost = min(
                budget_cap,
                (total_tokens * 0.75 / 1000.0) * p_in + (total_tokens * 0.25 / 1000.0) * p_out,
            )
        else:
            est_cost = min(budget_cap, est_cost_per_page * float(pages_to_consider))

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            import json

            parsed = json.loads(content)
        else:
            parsed = content

        text_blocks: list[TextBlock] = []
        for b in (parsed.get("text_blocks") or []) if isinstance(parsed, dict) else []:
            try:
                text_blocks.append(TextBlock(text=b.get("text", ""), page_number=int(b.get("page_number", 1))))
            except Exception:
                continue

        tables: list[Table] = []
        for t in (parsed.get("tables") or []) if isinstance(parsed, dict) else []:
            try:
                tables.append(
                    Table(
                        page_number=int(t.get("page_number", 1)),
                        headers=[str(x) for x in (t.get("headers") or [])],
                        rows=[[str(x) for x in row] for row in (t.get("rows") or [])],
                    )
                )
            except Exception:
                continue

        extracted = ExtractedDocument(
            doc_id=profile.doc_id,
            source_path=profile.source_path,
            strategy_used="vision",
            page_count=pages_to_consider,
            text_blocks=text_blocks,
            tables=tables,
            figures=[],
            reading_order=[(tb.page_number, i) for i, tb in enumerate(text_blocks)],
            raw={
                "openrouter_model": s.model,
                "usage": usage,
                "approx_pages_processed": pages_to_consider,
                "approx_cost_usd": est_cost,
                "elapsed_s": perf_counter() - t0,
            },
        )

        conf = 0.7 if (text_blocks or tables) else 0.2
        return ExtractionResult(extracted=extracted, confidence=conf, cost_estimate_usd=float(est_cost))

