"""
Semantic Chunking Engine (Stage 3).
Converts ExtractedDocument into Logical Document Units (LDUs) with all five
chunking rules enforced via ChunkValidator.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from models.schemas import (
    ExtractedDocument,
    Figure,
    LDU,
    LDUType,
    PageRef,
    Table,
    TextBlock,
)
from refinery.config import RefineryConfig
from refinery.utils import stable_content_hash


def _approx_token_count(text: str) -> int:
    """Rough token count (words * 1.35) for chunk size limits."""
    return max(0, int(len(text.split()) * 1.35))


class ChunkValidator:
    """
    Verifies LDUs satisfy the five chunking rules before emission.
    """

    @staticmethod
    def rule1_table_header_unsplit(ldu: LDU) -> tuple[bool, str]:
        """Table cell is never split from its header row."""
        if ldu.chunk_type != LDUType.table:
            return True, ""
        if not ldu.content.strip():
            return False, "Table LDU has empty content"
        # Expect first line to be a header row (tab-separated).
        first = ldu.content.splitlines()[0].strip()
        if not first:
            return False, "Table LDU missing header row"
        if "\t" not in first and len(first.split()) < 2:
            return False, "Table LDU header row looks empty/degenerate"
        return True, ""

    @staticmethod
    def rule2_figure_caption_metadata(ldu: LDU) -> tuple[bool, str]:
        """Figure caption is stored as metadata of parent figure chunk."""
        if ldu.chunk_type != LDUType.figure:
            return True, ""
        caption = (ldu.metadata or {}).get("caption")
        if caption is None:
            return False, "Figure LDU missing metadata.caption"
        return True, ""

    @staticmethod
    def rule3_list_single_ldu(ldu: LDU, max_tokens: int) -> tuple[bool, str]:
        """Numbered list kept as single LDU unless exceeds max_tokens."""
        if ldu.chunk_type != LDUType.list_item:
            return True, ""
        # This validator assumes chunker already split oversized lists.
        if ldu.token_count > max_tokens:
            return False, f"List LDU chunk still exceeds max_tokens ({ldu.token_count} > {max_tokens})"
        return True, ""

    @staticmethod
    def rule4_section_headers_parent(ldu: LDU) -> tuple[bool, str]:
        """Section headers stored as parent metadata on child chunks."""
        if ldu.chunk_type == LDUType.header:
            return True, ""
        if ldu.parent_section is not None and not str(ldu.parent_section).strip():
            return False, "LDU parent_section is empty string"
        return True, ""

    @staticmethod
    def rule5_crossref_relationships(ldu: LDU) -> tuple[bool, str]:
        """Cross-references resolved into chunk relationships."""
        refs = (ldu.relationships or {}).get("cross_refs") or []
        if not refs:
            return True, ""
        resolved = (ldu.relationships or {}).get("resolved_refs") or []
        unresolved = (ldu.relationships or {}).get("unresolved_refs") or []
        if not resolved and not unresolved:
            return False, "LDU has cross_refs but no resolved_refs/unresolved_refs"
        return True, ""

    @classmethod
    def validate(cls, ldu: LDU, max_tokens: int = 900) -> list[str]:
        """Return list of violation messages; empty if valid."""
        violations: list[str] = []
        ok, msg = cls.rule1_table_header_unsplit(ldu)
        if not ok:
            violations.append(msg)
        ok, msg = cls.rule2_figure_caption_metadata(ldu)
        if not ok:
            violations.append(msg)
        ok, msg = cls.rule3_list_single_ldu(ldu, max_tokens)
        if not ok:
            violations.append(msg)
        ok, msg = cls.rule4_section_headers_parent(ldu)
        if not ok:
            violations.append(msg)
        ok, msg = cls.rule5_crossref_relationships(ldu)
        if not ok:
            violations.append(msg)
        return violations


# Cross-reference pattern: "see Table 3", "Table 4.2", "Appendix A"
TABLE_REF_PATTERN = re.compile(
    r"\b(?:see\s+)?(?:Table|Appendix|Figure|Section)\s+[\w.]+\b",
    re.IGNORECASE,
)


def _make_ldu_id(doc_id: str, page_number: int, content_hash: str, seq: int) -> str:
    return f"{doc_id}:p{page_number}:{content_hash[:16]}:{seq}"


def _table_to_ldu(doc_id: str, table: Table, parent_section: str | None) -> LDU:
    """One table = one LDU; headers + rows never split (Rule 1)."""
    headers = list(table.headers or [])
    rows = list(table.rows or [])
    if (not headers) and rows:
        # Promote first row to headers when extractor couldn't decide.
        headers = [str(c or "").strip() for c in rows[0]]
        rows = rows[1:]

    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(c) for c in row))
    content = "\n".join(lines)
    token_count = _approx_token_count(content)
    content_hash = stable_content_hash(content)
    page_refs = [
        PageRef(page_number=table.page_number, bbox=table.bbox)
    ]
    refs = TABLE_REF_PATTERN.findall(content)
    relationships = {"cross_refs": sorted(set(r.strip() for r in refs if r.strip()))} if refs else {}
    return LDU(
        ldu_id=_make_ldu_id(doc_id, table.page_number, content_hash, seq=0),
        content=content,
        chunk_type=LDUType.table,
        page_refs=page_refs,
        bounding_box=table.bbox,
        parent_section=parent_section,
        token_count=token_count,
        content_hash=content_hash,
        relationships=relationships,
        metadata={"kind": "table"},
    )


def _figure_to_ldu(doc_id: str, figure: Figure, parent_section: str | None) -> LDU:
    """One figure = one LDU; caption stored in metadata (Rule 2)."""
    content = "[Figure]"
    token_count = _approx_token_count(content)
    content_hash = stable_content_hash(content)
    page_refs = [PageRef(page_number=figure.page_number, bbox=figure.bbox)]
    return LDU(
        ldu_id=_make_ldu_id(doc_id, figure.page_number, content_hash, seq=0),
        content=content,
        chunk_type=LDUType.figure,
        page_refs=page_refs,
        bounding_box=figure.bbox,
        parent_section=parent_section,
        token_count=token_count,
        content_hash=content_hash,
        relationships={},
        metadata={"kind": "figure", "caption": figure.caption},
    )


def _infer_section_title(text: str) -> str | None:
    """Heuristic: short line, or starts with number like '1.2 Title'."""
    t = text.strip()
    if len(t) > 80:
        return None
    if re.match(r"^\d+[.)]\s*\S", t) or re.match(r"^[IVX]+[.)]\s*\S", t):
        return t
    if t.isupper() and len(t.split()) <= 10:
        return t
    return None


def _text_blocks_to_ldus(
    doc_id: str,
    text_blocks: list[TextBlock],
    reading_order: list[tuple[int, int]],
    max_tokens: int,
) -> list[LDU]:
    """
    Convert text blocks to paragraph/list LDUs. Respects reading order.
    Section headers become parent_section for following chunks (Rule 4).
    Consecutive list-like lines stay in one LDU until max_tokens (Rule 3).
    """
    ldus: list[LDU] = []
    # Build ordered list of (page_number, block_index) then resolve to blocks
    order_pairs = sorted(set(reading_order), key=lambda x: (x[0], x[1]))
    parent_section: str | None = None
    seq = 0
    for page_num, idx in order_pairs:
        if idx >= len(text_blocks):
            continue
        block = text_blocks[idx]
        text = (block.text or "").strip()
        if not text:
            continue
        section_title = _infer_section_title(text)
        if section_title:
            parent_section = section_title
            token_count = _approx_token_count(text)
            ldus.append(
                LDU(
                    ldu_id=_make_ldu_id(doc_id, block.page_number, stable_content_hash(text), seq=seq),
                    content=text,
                    chunk_type=LDUType.header,
                    page_refs=[PageRef(page_number=block.page_number, bbox=block.bbox)],
                    bounding_box=block.bbox,
                    parent_section=None,
                    token_count=token_count,
                    content_hash=stable_content_hash(text),
                    relationships={},
                    metadata={"kind": "header"},
                )
            )
            seq += 1
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        list_like = (
            len(lines) >= 1
            and all(
                re.match(r"^[\d•\-*]+\s*", ln) or re.match(r"^[a-z]\)\s*", ln)
                for ln in lines[: min(5, len(lines))]
            )
        )
        if list_like:
            # Rule 3: keep list as a single LDU unless it exceeds max_tokens.
            if _approx_token_count(text) <= max_tokens:
                parts = [text]
            else:
                parts = []
                current: list[str] = []
                current_tokens = 0
                for line in lines:
                    t = _approx_token_count(line) + 1
                    if current and current_tokens + t > max_tokens:
                        parts.append("\n".join(current))
                        current = []
                        current_tokens = 0
                    current.append(line)
                    current_tokens += t
                if current:
                    parts.append("\n".join(current))

            for part_i, part in enumerate(parts):
                refs = TABLE_REF_PATTERN.findall(part)
                relationships = {"cross_refs": sorted(set(r.strip() for r in refs if r.strip()))} if refs else {}
                ch = stable_content_hash(part)
                ldus.append(
                    LDU(
                        ldu_id=_make_ldu_id(doc_id, block.page_number, ch, seq=seq),
                        content=part,
                        chunk_type=LDUType.list_item,
                        page_refs=[PageRef(page_number=block.page_number, bbox=block.bbox)],
                        bounding_box=block.bbox,
                        parent_section=parent_section,
                        token_count=_approx_token_count(part),
                        content_hash=ch,
                        relationships=relationships,
                        metadata={"kind": "list", "part": part_i, "parts": len(parts)},
                    )
                )
                seq += 1
        else:
            content = text
            if _approx_token_count(content) > max_tokens:
                parts = []
                current = []
                current_tokens = 0
                for line in lines:
                    line_tokens = _approx_token_count(line) + 1
                    if current_tokens + line_tokens > max_tokens and current:
                        parts.append("\n".join(current))
                        current = []
                        current_tokens = 0
                    current.append(line)
                    current_tokens += line_tokens
                if current:
                    parts.append("\n".join(current))
            else:
                parts = [content]
            for part in parts:
                refs = TABLE_REF_PATTERN.findall(part)
                relationships = {"cross_refs": sorted(set(r.strip() for r in refs if r.strip()))} if refs else {}
                ch = stable_content_hash(part)
                ldus.append(
                    LDU(
                        ldu_id=_make_ldu_id(doc_id, block.page_number, ch, seq=seq),
                        content=part,
                        chunk_type=LDUType.paragraph,
                        page_refs=[PageRef(page_number=block.page_number, bbox=block.bbox)],
                        bounding_box=block.bbox,
                        parent_section=parent_section,
                        token_count=_approx_token_count(part),
                        content_hash=ch,
                        relationships=relationships,
                        metadata={"kind": "paragraph"},
                    )
                )
                seq += 1
    return ldus


def _resolve_crossrefs(ldus: list[LDU]) -> list[LDU]:
    """
    Best-effort cross-reference resolution (Rule 5).
    For each cross-ref string, link to any other LDU whose content contains it.
    """
    if not ldus:
        return ldus
    content_index: list[tuple[str, str]] = [(u.ldu_id, (u.content or "").lower()) for u in ldus]
    updated: list[LDU] = []
    for u in ldus:
        refs = (u.relationships or {}).get("cross_refs") or []
        if not refs:
            updated.append(u)
            continue
        resolved: set[str] = set()
        unresolved: set[str] = set()
        for ref in refs:
            ref_l = ref.lower().strip()
            targets = [tid for (tid, txt) in content_index if tid != u.ldu_id and ref_l and ref_l in txt]
            if targets:
                resolved.update(targets)
            else:
                unresolved.add(ref)
        rel = dict(u.relationships or {})
        rel["resolved_refs"] = sorted(resolved)
        rel["unresolved_refs"] = sorted(unresolved)
        updated.append(u.model_copy(update={"relationships": rel}))
    return updated


class ChunkingEngine:
    """
    Converts ExtractedDocument into a list of LDUs with all five rules enforced.
    """

    def __init__(self, config: RefineryConfig | None = None):
        from refinery.config import load_config
        self.config = config or load_config()
        chunk_cfg = (self.config.rules.get("chunking") or {})
        self.max_tokens = int(chunk_cfg.get("max_tokens", 900))

    def run(self, extracted: ExtractedDocument) -> list[LDU]:
        """Produce LDUs from ExtractedDocument; ChunkValidator runs before emit."""
        ldus: list[LDU] = []
        parent_section: str | None = None

        for table in extracted.tables:
            ldu = _table_to_ldu(extracted.doc_id, table, parent_section)
            violations = ChunkValidator.validate(ldu, self.max_tokens)
            if violations:
                continue
            ldus.append(ldu)

        for figure in extracted.figures:
            ldu = _figure_to_ldu(extracted.doc_id, figure, parent_section)
            violations = ChunkValidator.validate(ldu, self.max_tokens)
            if violations:
                continue
            ldus.append(ldu)

        text_ldus = _text_blocks_to_ldus(
            extracted.doc_id,
            extracted.text_blocks,
            extracted.reading_order,
            self.max_tokens,
        )
        for ldu in text_ldus:
            violations = ChunkValidator.validate(ldu, self.max_tokens)
            if not violations:
                ldus.append(ldu)

        # Resolve cross references after all LDUs exist.
        ldus = _resolve_crossrefs(ldus)
        # Validate Rule 5 post-resolution.
        final: list[LDU] = []
        for u in ldus:
            violations = ChunkValidator.validate(u, self.max_tokens)
            if violations:
                continue
            final.append(u)
        return final


def run_chunker(extracted_path: str | Path, out_dir: str | Path = Path(".refinery") / "ldus") -> Path:
    """Load ExtractedDocument JSON, run ChunkingEngine, save LDUs JSON."""
    import json
    path = Path(extracted_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    from models.schemas import ExtractedDocument
    doc = ExtractedDocument.model_validate(data)
    engine = ChunkingEngine()
    ldus = engine.run(doc)
    out_path = out_dir / f"{doc.doc_id}_ldus.json"
    out_path.write_text(
        json.dumps([u.model_dump(mode="json") for u in ldus], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
