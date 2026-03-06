"""
Query Interface Agent (Stage 5).
Agent with three tools: pageindex_navigate, semantic_search, structured_query.
Every answer includes a ProvenanceChain. Audit mode: verify_claim.
"""
from __future__ import annotations

import json
from pathlib import Path

from models.schemas import PageIndex, ProvenanceChain, ProvenanceCitation
from refinery.vector_store import semantic_search as _semantic_search


PAGEINDEX_DIR = Path(".refinery/pageindex")
FACTS_DB = Path(".refinery/facts.db")


def _load_pageindex(doc_id: str) -> PageIndex | None:
    path = PAGEINDEX_DIR / f"{doc_id}.json"
    if not path.exists():
        return None
    return PageIndex.model_validate_json(path.read_text(encoding="utf-8"))


def pageindex_navigate(
    topic: str,
    doc_id: str | None = None,
) -> tuple[str, ProvenanceChain]:
    """
    Traverse PageIndex tree to find sections matching topic. Returns (summary, provenance).
    """
    citations = []
    if doc_id:
        index = _load_pageindex(doc_id)
        if index and index.root.child_sections:
            from agents.indexer import find_relevant_sections

            nodes = find_relevant_sections(index, topic, top_k=3)
            if not nodes:
                return "No matching section found.", ProvenanceChain(citations=[])
            lines = []
            for node in nodes:
                lines.append(f"- {node.title} (pages {node.page_start}-{node.page_end})")
                citations.append(
                    ProvenanceCitation(
                        document_name=doc_id,
                        page_number=node.page_start,
                        content_hash=None,
                    )
                )
            return "\n".join(lines), ProvenanceChain(citations=citations)
    indices = list(PAGEINDEX_DIR.glob("*.json")) if PAGEINDEX_DIR.exists() else []
    results = []
    for p in indices[:5]:
        idx = PageIndex.model_validate_json(p.read_text(encoding="utf-8"))
        from agents.indexer import find_relevant_sections

        for node in find_relevant_sections(idx, topic, top_k=2):
            results.append(f"[{idx.doc_id}] {node.title} (p.{node.page_start}-{node.page_end})")
            citations.append(
                ProvenanceCitation(document_name=idx.doc_id, page_number=node.page_start, content_hash=None)
            )
    return "\n".join(results) if results else "No matching sections.", ProvenanceChain(citations=citations)


def semantic_search_tool(
    query: str,
    n_results: int = 5,
    doc_id: str | None = None,
) -> tuple[str, ProvenanceChain]:
    """Vector search over LDUs. Returns (concatenated content, provenance)."""
    hits, chain = _semantic_search(query, n_results=n_results, doc_id_filter=doc_id)
    text = "\n\n---\n\n".join((h.get("content", "") or "") for h in hits)
    return text or "No results.", chain


def structured_query_tool(
    query: str,
    doc_id: str | None = None,
) -> tuple[str, ProvenanceChain]:
    """
    Run SQL over refinery_facts. query can be a SQL fragment like 'SELECT * FROM refinery_facts WHERE fact_key LIKE \"%revenue%\"'
    or a natural language hint we convert to SQL.
    """
    from agents.fact_table import query_facts_sql
    sql = query.strip()
    if not sql.upper().startswith("SELECT"):
        sql = f"SELECT doc_id, page_number, fact_key, fact_value FROM refinery_facts WHERE fact_value LIKE '%{query[:50]}%' OR fact_key LIKE '%{query[:30]}%' LIMIT 20"
    rows, chain = query_facts_sql(sql, FACTS_DB, doc_id=doc_id)
    if not rows:
        return "No matching facts.", chain
    return json.dumps(rows, indent=2, default=str), chain


def run_query(question: str, doc_id: str | None = None) -> tuple[str, ProvenanceChain]:
    """
    Run the query agent: use semantic search by default, optionally navigate PageIndex first.
    Returns (answer_text, ProvenanceChain).
    """
    # Simple flow: semantic search + optional structured query if question looks factual
    query_lower = question.lower()
    if any(k in query_lower for k in ["revenue", "total", "amount", "figure", "number", "table"]):
        text, chain = structured_query_tool(question, doc_id)
        if "No matching" not in text:
            return f"Facts:\n{text}", chain
    nav_text, nav_chain = pageindex_navigate(question, doc_id)
    sem_text, sem_chain = semantic_search_tool(question, n_results=5, doc_id=doc_id)
    combined = f"PageIndex: {nav_text}\n\nContent:\n{sem_text}"
    citations = list(nav_chain.citations) + list(sem_chain.citations)
    return combined, ProvenanceChain(citations=citations[:15])


def verify_claim(claim: str, doc_id: str | None = None) -> dict:
    """
    Audit Mode: verify a claim against the refinery. Returns
    { "verified": bool, "citations": ProvenanceChain, "answer": str } or "unverifiable".
    """
    answer, chain = run_query(claim, doc_id)
    if not chain.citations:
        return {"verified": False, "citations": chain, "answer": "unverifiable"}
    # Heuristic: if we got back content that overlaps with claim words, consider verified
    claim_words = set(claim.lower().split()) - {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at"}
    overlap = claim_words & set(answer.lower().split())
    verified = len(overlap) >= max(1, len(claim_words) // 2)
    return {"verified": verified, "citations": chain, "answer": answer}
