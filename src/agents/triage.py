from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter

import pdfplumber
from langdetect import DetectorFactory, detect_langs

from models.schemas import (
    DocumentProfile,
    DomainHint,
    ExtractionCostTier,
    LayoutComplexity,
    OriginType,
    TriageSignals,
)
from refinery.config import RefineryConfig
from refinery.utils import doc_id_from_path


DetectorFactory.seed = 7


@dataclass(frozen=True)
class PageSignals:
    chars: int
    char_density: float
    image_area_ratio: float
    multi_column_score: float
    tables_found: int


def _bbox_area(x0: float, top: float, x1: float, bottom: float) -> float:
    return max(0.0, x1 - x0) * max(0.0, bottom - top)


def _safe_div(num: float, den: float) -> float:
    return 0.0 if den <= 0 else num / den


def _estimate_multi_column_score(page: pdfplumber.page.Page, min_sep_pts: float) -> float:
    # Heuristic: if x-positions of characters show two strong modes separated by min_sep_pts.
    chars = page.chars or []
    if len(chars) < 250:
        return 0.0

    xs = [float(c.get("x0", 0.0)) for c in chars if c.get("text")]
    if len(xs) < 250:
        return 0.0

    bin_width = 50.0
    bins: dict[int, int] = {}
    for x in xs:
        b = int(x // bin_width)
        bins[b] = bins.get(b, 0) + 1

    if len(bins) < 3:
        return 0.0

    # Get top-2 bins by count.
    top = sorted(bins.items(), key=lambda kv: kv[1], reverse=True)[:2]
    (b1, c1), (b2, c2) = top[0], top[1]

    x1 = (b1 + 0.5) * bin_width
    x2 = (b2 + 0.5) * bin_width
    sep = abs(x1 - x2)

    total = sum(bins.values())
    strength = (c1 + c2) / max(1, total)
    if sep < min_sep_pts:
        return 0.0

    # Score grows with separation and peak strength.
    sep_score = min(1.0, (sep - min_sep_pts) / (min_sep_pts))
    return float(max(0.0, min(1.0, 0.25 + 0.75 * strength * (0.5 + 0.5 * sep_score))))


def _count_tables(page: pdfplumber.page.Page) -> int:
    # pdfplumber table detection is expensive; keep it light and best-effort.
    try:
        tables = page.find_tables()
        return len(tables) if tables else 0
    except Exception:
        return 0


def _page_signals(page: pdfplumber.page.Page, rules: dict) -> PageSignals:
    page_area = float(page.width * page.height)
    chars = len(page.chars or [])
    char_density = _safe_div(chars, page_area)

    images = page.images or []
    img_area = 0.0
    for im in images:
        # pdfplumber uses (x0, x1, top, bottom) for image bbox.
        try:
            img_area += _bbox_area(float(im["x0"]), float(im["top"]), float(im["x1"]), float(im["bottom"]))
        except Exception:
            continue
    image_area_ratio = float(max(0.0, min(1.0, _safe_div(img_area, page_area))))

    min_sep = float(rules["triage"]["layout_detection"]["multi_column_min_separation_pts"])
    multi_col = _estimate_multi_column_score(page, min_sep_pts=min_sep)

    tables_found = _count_tables(page)
    return PageSignals(
        chars=chars,
        char_density=float(char_density),
        image_area_ratio=image_area_ratio,
        multi_column_score=multi_col,
        tables_found=tables_found,
    )


def _detect_language(sample_text: str) -> tuple[str, float]:
    text = (sample_text or "").strip()
    if len(text) < 50:
        return "unknown", 0.0
    try:
        candidates = detect_langs(text[:5000])
        if not candidates:
            return "unknown", 0.0
        best = candidates[0]
        return best.lang, float(max(0.0, min(1.0, best.prob)))
    except Exception:
        return "unknown", 0.0


def _domain_hint(sample_text: str, rules: dict) -> DomainHint:
    """Infer high-level domain from keywords externalized in the YAML rules."""
    t = (sample_text or "").lower()
    cfg = (rules.get("triage") or {}).get("domain_hints") or {}

    def has_any(label: str) -> bool:
        keywords = cfg.get(label) or []
        return any(k.lower() in t for k in keywords)

    if has_any("financial"):
        return DomainHint.financial
    if has_any("legal"):
        return DomainHint.legal
    if has_any("medical"):
        return DomainHint.medical
    if has_any("technical"):
        return DomainHint.technical
    return DomainHint.general


def _has_form_fields(pdf_path: str | Path) -> bool:
    # Best-effort: PyMuPDF can detect widgets/annotations in fillable PDFs.
    try:
        import fitz  # pymupdf

        doc = fitz.open(str(pdf_path))
        try:
            for i in range(doc.page_count):
                page = doc.load_page(i)
                widgets = page.widgets()
                if widgets is None:
                    continue
                # In PyMuPDF, widgets may be an iterator/generator; iterate to test emptiness.
                for _w in widgets:
                    return True
            return False
        finally:
            doc.close()
    except Exception:
        return False


def classify_profile(pdf_path: str | Path, config: RefineryConfig) -> DocumentProfile:
    rules = config.rules
    pdf_path = Path(pdf_path)
    doc_id = doc_id_from_path(pdf_path)

    t0 = perf_counter()
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages
        if not pages:
            raise ValueError("PDF has no pages.")
        total_pages = len(pages)

        sample_pages = pages[: min(10, total_pages)]
        psigs = [_page_signals(p, rules) for p in sample_pages]

        mean_chars = mean(s.chars for s in psigs)
        mean_density = mean(s.char_density for s in psigs)
        mean_img_ratio = mean(s.image_area_ratio for s in psigs)
        mean_multi_col = mean(s.multi_column_score for s in psigs)
        tables_per_10 = _safe_div(sum(s.tables_found for s in psigs), len(psigs)) * 10.0

        # Sample text for language/domain.
        sample_text = ""
        for p in sample_pages[:3]:
            try:
                txt = p.extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                sample_text += txt + "\n"

    has_forms = _has_form_fields(pdf_path)
    lang, lang_conf = _detect_language(sample_text)
    domain = _domain_hint(sample_text, rules)

    # Origin classification.
    od = rules["triage"]["origin_detection"]
    scanned_like = (
        mean_density <= float(od["scanned_char_density_max"])
        or mean_chars <= float(od["scanned_char_count_max"])
    ) and mean_img_ratio >= float(od["scanned_image_area_ratio_min"])

    if has_forms:
        origin = OriginType.form_fillable
    elif scanned_like:
        origin = OriginType.scanned_image
    elif mean_img_ratio > 0.25 and mean_chars > 80:
        origin = OriginType.mixed
    else:
        origin = OriginType.native_digital

    # Layout complexity classification.
    ld = rules["triage"]["layout_detection"]
    table_heavy = tables_per_10 >= float(ld["table_heavy_min_tables_per_10_pages"])
    multi_col = mean_multi_col >= 0.55

    if table_heavy and multi_col:
        layout = LayoutComplexity.mixed
    elif table_heavy:
        layout = LayoutComplexity.table_heavy
    elif multi_col:
        layout = LayoutComplexity.multi_column
    elif mean_img_ratio >= 0.45 and mean_chars >= 80:
        layout = LayoutComplexity.figure_heavy
    else:
        layout = LayoutComplexity.single_column

    # Cost tier.
    if origin == OriginType.scanned_image:
        cost = ExtractionCostTier.needs_vision_model
    elif origin == OriginType.native_digital and layout == LayoutComplexity.single_column:
        cost = ExtractionCostTier.fast_text_sufficient
    else:
        cost = ExtractionCostTier.needs_layout_model

    signals = TriageSignals(
        page_count=int(total_pages),
        mean_chars_per_page=float(mean_chars),
        mean_char_density=float(mean_density),
        mean_image_area_ratio=float(mean_img_ratio),
        estimated_tables_per_10_pages=float(tables_per_10),
        multi_column_score=float(mean_multi_col),
        has_form_fields=bool(has_forms),
    )

    notes: list[str] = []
    if scanned_like:
        notes.append("Scanned-like signals: low char density + high image area ratio on sampled pages.")
    if table_heavy:
        notes.append("Table-heavy heuristic triggered (pdfplumber find_tables on sampled pages).")
    if perf_counter() - t0 > 5.0:
        notes.append("Triage took >5s (table detection can be expensive).")

    return DocumentProfile(
        doc_id=doc_id,
        source_path=str(pdf_path),
        page_count=int(total_pages),
        origin_type=origin,
        layout_complexity=layout,
        language=lang,
        language_confidence=lang_conf,
        domain_hint=domain,
        estimated_extraction_cost=cost,
        triage_signals=signals,
        notes=notes,
    )


def save_profile(profile: DocumentProfile, out_dir: str | Path = Path(".refinery") / "profiles") -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{profile.doc_id}.json"
    out_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return out_path

