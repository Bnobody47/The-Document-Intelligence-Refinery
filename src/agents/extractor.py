from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from agents.triage import classify_profile, save_profile
from models.schemas import DocumentProfile, ExtractedDocument
from refinery.config import RefineryConfig, load_config
from refinery.utils import append_jsonl
from strategies.base import ExtractionResult
from strategies.fast_text import FastTextExtractor
from strategies.layout_pdfplumber import LayoutPdfPlumberExtractor
from strategies.vision_openrouter import VisionOpenRouterExtractor


@dataclass(frozen=True)
class RouterOutcome:
    profile: DocumentProfile
    profile_path: Path
    extracted: ExtractedDocument
    extraction_path: Path
    result: ExtractionResult
    escalated_from: str | None


def _save_extraction(
    extracted: ExtractedDocument, out_dir: str | Path = Path(".refinery") / "extractions"
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{extracted.doc_id}.json"
    out_path.write_text(extracted.model_dump_json(indent=2), encoding="utf-8")
    return out_path


def _choose_auto(profile: DocumentProfile) -> str:
    if profile.origin_type.value == "scanned_image":
        return "vision"
    if profile.origin_type.value == "native_digital" and profile.layout_complexity.value == "single_column":
        return "fast_text"
    return "layout"


def extract_with_router(
    pdf_path: str | Path,
    config: RefineryConfig | None = None,
    *,
    strategy: str | None = None,  # auto | fast_text | layout | vision
) -> RouterOutcome:
    config = config or load_config()
    rules = config.rules

    pdf_path = Path(pdf_path)
    t0 = perf_counter()

    profile = classify_profile(pdf_path, config)
    profile_path = save_profile(profile)

    requested_input = (strategy or rules["extraction"]["router_defaults"]["strategy"]).strip().lower()
    requested = requested_input
    if requested_input == "auto":
        requested = _choose_auto(profile)

    fast = FastTextExtractor()
    layout = LayoutPdfPlumberExtractor()
    vision = VisionOpenRouterExtractor()

    extractor_map = {"fast_text": fast, "layout": layout, "vision": vision}
    if requested not in extractor_map:
        raise ValueError(
            f"Unknown strategy '{requested}'. Expected one of: auto, fast_text, layout, vision."
        )

    escalated_from: str | None = None
    result = extractor_map[requested].extract(profile, config)

    # Escalation guard (critical for RAG quality).
    conf_rules = rules["extraction"]["confidence"]
    min_doc_conf = float(conf_rules["min_doc_confidence_fast_text"])

    if requested == "fast_text" and result.confidence < min_doc_conf:
        escalated_from = "fast_text"
        result = layout.extract(profile, config)

    if requested in {"fast_text", "layout"} and profile.origin_type.value == "scanned_image":
        # If profile says scanned, do not trust non-vision extraction.
        escalated_from = requested if escalated_from is None else escalated_from
        result = vision.extract(profile, config)

    extracted = result.extracted
    extraction_path = _save_extraction(extracted)

    elapsed_ms = int((perf_counter() - t0) * 1000)
    ledger_record = {
        "ts_ms": int(time.time() * 1000),
        "doc_id": profile.doc_id,
        "source_path": str(pdf_path),
        "strategy_requested": requested_input,
        "strategy_selected": requested,
        "strategy_used": extracted.strategy_used,
        "escalated_from": escalated_from,
        "confidence_score": float(result.confidence),
        "cost_estimate_usd": float(result.cost_estimate_usd),
        "processing_ms": elapsed_ms,
        "profile_path": str(profile_path),
        "extraction_path": str(extraction_path),
        "rules_path": str(config.rules_path),
    }
    append_jsonl(Path(".refinery") / "extraction_ledger.jsonl", ledger_record)

    return RouterOutcome(
        profile=profile,
        profile_path=profile_path,
        extracted=extracted,
        extraction_path=extraction_path,
        result=result,
        escalated_from=escalated_from,
    )

