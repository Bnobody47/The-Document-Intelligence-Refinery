"""
PageIndex Builder (Stage 4).
Builds a hierarchical navigation tree over the document with optional
LLM-generated section summaries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from models.schemas import ExtractedDocument, PageIndex, PageIndexNode
from refinery.config import RefineryConfig


def _infer_section_title(text: str) -> str | None:
    """Heuristic: short line or numbered heading."""
    import re
    t = text.strip()
    if len(t) > 100:
        return None
    if re.match(r"^\d+[.)]\s*\S", t) or re.match(r"^[IVX]+[.)]\s*\S", t):
        return t
    if t.isupper() and len(t.split()) <= 12:
        return t
    return None


def _heading_level(title: str) -> int:
    """
    Infer hierarchy level from heading numbering.
    Examples:
      "1. Title" -> 1
      "1.2 Title" -> 2
      "1.2.3 Title" -> 3
    Non-numbered headings default to level 1.
    """
    import re

    t = title.strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", t)
    if not m:
        return 1
    return max(1, m.group(1).count(".") + 1)


def _extract_key_entities(text: str, max_entities: int = 12) -> list[str]:
    """
    Cheap, local entity heuristic (no external model):
    - acronyms (2+ caps), e.g. "CBE", "DBE"
    - capitalized phrases up to 4 words, e.g. "Ministry of Finance"
    """
    import re

    if not text:
        return []
    candidates: set[str] = set()
    for m in re.finditer(r"\b[A-Z]{2,}\b", text):
        candidates.add(m.group(0))
    for m in re.finditer(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", text):
        s = m.group(0).strip()
        if len(s) >= 3:
            candidates.add(s)
    # Keep deterministic order: longer first, then lexicographic.
    ordered = sorted(candidates, key=lambda x: (-len(x), x))[:max_entities]
    return ordered


def _build_flat_sections(
    extracted: ExtractedDocument,
) -> list[PageIndexNode]:
    """
    Infer flat list of sections from headers and page ranges.
    Each section: title from header, page_start/page_end from block/tables/figures.
    """
    nodes: list[PageIndexNode] = []
    # Order blocks by reading order
    order_pairs = sorted(set(extracted.reading_order), key=lambda x: (x[0], x[1]))
    current_title: str | None = None
    current_start: int | None = None
    current_end: int | None = None
    current_preview: list[str] = []

    for page_num, idx in order_pairs:
        if idx >= len(extracted.text_blocks):
            continue
        block = extracted.text_blocks[idx]
        text = (getattr(block, "text", None) or "").strip()
        page_number = getattr(block, "page_number", page_num)
        if not text:
            continue
        title = _infer_section_title(text)
        if title:
            if current_title is not None and current_start is not None:
                nodes.append(
                    PageIndexNode(
                        title=current_title,
                        page_start=current_start,
                        page_end=current_end or current_start,
                        child_sections=[],
                        key_entities=_extract_key_entities("\n".join(current_preview)),
                        summary=None,
                        data_types_present=[],
                    )
                )
            current_title = title
            current_start = page_number
            current_end = page_number
            current_preview = []
        else:
            if current_end is not None:
                current_end = max(current_end, page_number)
            elif current_start is not None:
                current_end = max(current_start, page_number)
            if len(current_preview) < 30:
                current_preview.append(text[:300])

    if current_title is not None and current_start is not None:
        nodes.append(
            PageIndexNode(
                title=current_title,
                page_start=current_start,
                page_end=current_end or current_start,
                child_sections=[],
                key_entities=_extract_key_entities("\n".join(current_preview)),
                summary=None,
                data_types_present=[],
            )
        )

    # Attach data_types from tables/figures per section
    for i, node in enumerate(nodes):
        types_present: list[str] = []
        for t in extracted.tables:
            p = getattr(t, "page_number", 0)
            if node.page_start <= p <= node.page_end:
                types_present.append("tables")
                break
        for f in extracted.figures:
            p = getattr(f, "page_number", 0)
            if node.page_start <= p <= node.page_end:
                types_present.append("figures")
                break
        nodes[i] = node.model_copy(update={"data_types_present": list(set(types_present))})

    return nodes


def _nest_sections(flat: list[PageIndexNode]) -> list[PageIndexNode]:
    """
    Convert a flat heading list into a hierarchy using heading levels.
    """
    if not flat:
        return []
    stack: list[tuple[int, PageIndexNode]] = []
    roots: list[PageIndexNode] = []
    for node in flat:
        lvl = _heading_level(node.title)
        # Pop until parent is above this level.
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        if not stack:
            roots.append(node)
        else:
            parent = stack[-1][1]
            parent.child_sections.append(node)
            parent.page_end = max(parent.page_end, node.page_end)
        stack.append((lvl, node))
    return roots


def _summarize_section_with_llm(title: str, content_preview: str) -> str | None:
    """Optional: call OpenAI/OpenRouter for 2–3 sentence summary. Returns None if no API key."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import httpx
        url = "https://openrouter.ai/api/v1/chat/completions" if os.getenv("OPENROUTER_API_KEY") else "https://api.openai.com/v1/chat/completions"
        model = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001") if os.getenv("OPENROUTER_API_KEY") else "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Summarize in 2–3 sentences for a table of contents. Section: {title}\nPreview: {content_preview[:800]}",
                }
            ],
            "max_tokens": 150,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=30) as client:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip() if content else None
    except Exception:
        return None


def build_page_index(
    extracted: ExtractedDocument,
    config: RefineryConfig | None = None,
    use_llm_summaries: bool = False,
) -> PageIndex:
    """
    Build PageIndex from ExtractedDocument. Optionally fill summaries via LLM.
    """
    config = config or __import__("refinery.config", fromlist=["load_config"]).load_config()
    flat = _build_flat_sections(extracted)
    nodes = _nest_sections(flat)
    if use_llm_summaries:
        def walk(n: PageIndexNode) -> None:
            preview = f"{n.title} (pages {n.page_start}-{n.page_end})\nEntities: {', '.join(n.key_entities[:8])}"
            n.summary = _summarize_section_with_llm(n.title, preview)
            for c in n.child_sections or []:
                walk(c)
        for n in nodes:
            walk(n)
    start_min = min((n.page_start for n in nodes), default=1)
    end_max = max((n.page_end for n in nodes), default=1)
    if not nodes:
        start_min, end_max = 1, extracted.page_count
    root = PageIndexNode(
        title=extracted.doc_id,
        page_start=start_min,
        page_end=end_max,
        child_sections=nodes,
        key_entities=[],
        summary=None,
        data_types_present=list(set(
            (["tables"] if extracted.tables else []) + (["figures"] if extracted.figures else [])
        )),
    )
    return PageIndex(doc_id=extracted.doc_id, root=root)


def find_relevant_sections(index: PageIndex, query: str, top_k: int = 3) -> list[PageIndexNode]:
    """
    Traverse the tree and return top-k relevant sections for a query.
    Uses simple lexical scoring over title/summary/entities.
    """
    import re

    q = (query or "").lower()
    q_terms = {t for t in re.split(r"\W+", q) if t}

    scored: list[tuple[float, PageIndexNode]] = []

    def walk(n: PageIndexNode) -> None:
        hay = " ".join(
            [n.title or "", n.summary or "", " ".join(n.key_entities or [])]
        ).lower()
        if not hay.strip():
            score = 0.0
        else:
            overlap = sum(1 for t in q_terms if t and t in hay)
            score = float(overlap) + (2.0 if q and q in (n.title or "").lower() else 0.0)
        scored.append((score, n))
        for c in n.child_sections or []:
            walk(c)

    for child in index.root.child_sections or []:
        walk(child)
    scored = [s for s in scored if s[0] > 0.0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _s, n in scored[: max(1, top_k)]]


def run_indexer(
    extracted_path: str | Path,
    out_dir: str | Path = Path(".refinery") / "pageindex",
    use_llm_summaries: bool = False,
) -> Path:
    """Load ExtractedDocument, build PageIndex, save JSON."""
    path = Path(extracted_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = ExtractedDocument.model_validate(data)
    config = __import__("refinery.config", fromlist=["load_config"]).load_config()
    index = build_page_index(doc, config, use_llm_summaries=use_llm_summaries)
    out_path = out_dir / f"{doc.doc_id}.json"
    out_path.write_text(index.model_dump_json(indent=2), encoding="utf-8")
    return out_path
