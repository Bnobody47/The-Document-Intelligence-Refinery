"""
FactTable extractor: key-value facts from numerical/financial documents into SQLite.
Used by the Query Agent for structured_query tool.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from models.schemas import BoundingBox, ExtractedDocument, ProvenanceCitation, ProvenanceChain
from refinery.utils import stable_content_hash


FACTS_DB_NAME = ".refinery/facts.db"
TABLE_NAME = "refinery_facts"


def _extract_facts_from_table(
    page_number: int,
    headers: list[str],
    rows: list[list[str]],
    bbox: Any | None = None,
) -> list[dict]:
    """Turn table rows into key-value facts. Header[i] + row[i] -> fact_key, fact_value."""
    facts = []
    for row in rows:
        for i, val in enumerate(row):
            if i < len(headers) and headers[i] and str(val).strip():
                key = re.sub(r"[^a-zA-Z0-9_]", "_", headers[i].strip())[:128]
                facts.append({
                    "page_number": page_number,
                    "fact_key": key,
                    "fact_value": str(val).strip(),
                    "bbox_json": json.dumps(bbox.model_dump(mode="json")) if bbox else "",
                })
    return facts


def _extract_facts_from_text_blocks(blocks: list[Any]) -> list[dict]:
    """Heuristic: look for currency amounts, dates, 'key: value' in text."""
    facts = []
    currency = re.compile(r"(\$|USD|ETB|Birr)\s*([\d,.]+\s*(?:million|billion|B|M)?)")
    date = re.compile(r"(?:Q[1-4]\s*\d{4}|FY\s*\d{4}/\d{2}|\d{4}-\d{2}-\d{2})")
    for b in blocks:
        text = getattr(b, "text", "") or ""
        page = getattr(b, "page_number", 1)
        bbox = getattr(b, "bbox", None)
        bbox_json = ""
        try:
            if bbox is not None:
                bbox_json = json.dumps(bbox.model_dump(mode="json"))
        except Exception:
            bbox_json = ""
        for m in currency.finditer(text):
            facts.append({"page_number": page, "fact_key": "amount", "fact_value": m.group(0).strip(), "bbox_json": bbox_json})
        for m in date.finditer(text):
            facts.append({"page_number": page, "fact_key": "date", "fact_value": m.group(0).strip(), "bbox_json": bbox_json})
    return facts


def extract_fact_table(extracted: ExtractedDocument, db_path: str | Path = FACTS_DB_NAME) -> Path:
    """
    Extract key-value facts from ExtractedDocument and write to SQLite.
    Creates table refinery_facts(doc_id, page_number, fact_key, fact_value, content_hash).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refinery_facts (
                doc_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                content_hash TEXT,
                bbox_json TEXT,
                PRIMARY KEY (doc_id, page_number, fact_key, fact_value)
            )
            """
        )
        facts: list[dict] = []
        for t in extracted.tables:
            for f in _extract_facts_from_table(t.page_number, t.headers, t.rows, bbox=t.bbox):
                f["content_hash"] = stable_content_hash(f"{f['fact_key']}:{f['fact_value']}")
                facts.append(f)
        for f in _extract_facts_from_text_blocks(extracted.text_blocks):
            f["content_hash"] = stable_content_hash(f"{f['fact_key']}:{f['fact_value']}")
            facts.append(f)
        for fa in facts:
            conn.execute(
                "INSERT OR REPLACE INTO refinery_facts (doc_id, page_number, fact_key, fact_value, content_hash, bbox_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    extracted.doc_id,
                    fa["page_number"],
                    fa["fact_key"],
                    fa["fact_value"],
                    fa.get("content_hash", ""),
                    fa.get("bbox_json", ""),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def query_facts_sql(
    sql: str,
    db_path: str | Path = FACTS_DB_NAME,
    doc_id: str | None = None,
) -> tuple[list[dict], ProvenanceChain]:
    """
    Run a SQL query against refinery_facts. Returns (rows, provenance).
    If doc_id is set, restricts to that document for provenance.
    """
    path = Path(db_path)
    if not path.exists():
        return [], ProvenanceChain(citations=[])
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    citations = []
    for r in rows:
        doc = r.get("doc_id") or doc_id
        if doc:
            bbox = None
            try:
                bbox_json = r.get("bbox_json") or ""
                if bbox_json:
                    bbox = BoundingBox.model_validate_json(bbox_json)
            except Exception:
                bbox = None
            citations.append(
                ProvenanceCitation(
                    document_name=doc,
                    page_number=int(r.get("page_number", 1)),
                    bbox=bbox,
                    content_hash=r.get("content_hash"),
                )
            )
    return rows, ProvenanceChain(citations=citations[:20])
