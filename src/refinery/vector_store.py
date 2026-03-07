"""
Vector store ingestion (ChromaDB) for LDUs. Used by Query Agent semantic_search.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from models.schemas import BoundingBox, LDU, ProvenanceCitation, ProvenanceChain


CHROMA_PERSIST_DIR = ".refinery/chroma"


class RefineryHashEmbeddingFunction:
    """
    Lightweight, fully local embedding function (no model downloads).
    Produces deterministic vectors from text using SHA-256 expansion.
    """

    def __init__(self, dim: int = 384):
        self.dim = int(dim)

    @staticmethod
    def name() -> str:
        return "refinery_hash_v1"

    def get_config(self) -> dict[str, Any]:
        return {"dim": self.dim}

    @classmethod
    def build_from_config(cls, config: dict[str, Any]) -> "RefineryHashEmbeddingFunction":
        return cls(dim=int((config or {}).get("dim", 384)))

    def __call__(self, input: list[str]) -> list[list[float]]:  # chromadb interface
        vectors: list[list[float]] = []
        for text in input:
            t = (text or "").encode("utf-8")
            # Expand hash material until we have enough bytes.
            material = b""
            counter = 0
            while len(material) < self.dim * 4:
                material += hashlib.sha256(t + counter.to_bytes(4, "little")).digest()
                counter += 1
            # Convert bytes -> floats in [-1, 1]
            vec: list[float] = []
            for i in range(self.dim):
                chunk = material[i * 4 : i * 4 + 4]
                n = int.from_bytes(chunk, "little", signed=False)
                vec.append(((n % 2000000) / 1000000.0) - 1.0)
            vectors.append(vec)
        return vectors

    # Newer Chroma expects these method names.
    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: Any) -> list[list[float]]:
        if isinstance(input, list):
            text = " ".join(str(x) for x in input)
        else:
            text = str(input)
        return self([text])


def get_or_create_collection(persist_dir: str | Path = CHROMA_PERSIST_DIR):
    """
    Create or load the Chroma collection used for LDUs.

    Some chromadb+pydantic versions can raise ConfigError when instantiating Settings
    (e.g. around attributes like `chroma_server_nofile`). To keep the demo portable
    across environments, we try to use Settings but gracefully fall back to the
    default client configuration if that fails.
    """
    import chromadb

    path = Path(persist_dir)
    path.mkdir(parents=True, exist_ok=True)

    client = None
    try:
        from chromadb.config import Settings

        client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    except Exception:
        # Fallback: rely on chromadb defaults (still persistent at `path`).
        client = chromadb.PersistentClient(path=str(path))

    return client.get_or_create_collection(
        "refinery_ldus_hash_v1",
        metadata={"description": "Document LDUs"},
        embedding_function=RefineryHashEmbeddingFunction(),
    )


def ingest_ldus(ldus: list[LDU], doc_id: str, persist_dir: str | Path = CHROMA_PERSIST_DIR) -> int:
    """
    Add LDUs to ChromaDB. Uses content as document, metadata for provenance.
    Returns count of ingested documents.
    """
    if not ldus:
        return 0
    coll = get_or_create_collection(persist_dir)
    ids = []
    documents = []
    metadatas = []
    for _i, ldu in enumerate(ldus):
        ids.append(ldu.ldu_id)
        documents.append(ldu.content[:50_000])
        page_ref = ldu.page_refs[0] if ldu.page_refs else None
        bbox = page_ref.bbox if page_ref else None
        metadatas.append({
            "doc_id": doc_id,
            "page_number": str(page_ref.page_number) if page_ref else "1",
            "chunk_type": ldu.chunk_type.value,
            "content_hash": ldu.content_hash,
            "parent_section": (ldu.parent_section or "")[:500],
            "page_refs_json": json.dumps([pr.model_dump(mode="json") for pr in (ldu.page_refs or [])]),
            "bbox_json": json.dumps(bbox.model_dump(mode="json")) if bbox else "",
        })
    # Chroma has a max batch size; slice to avoid InternalError.
    max_batch = 1000
    total = len(ids)
    for start in range(0, total, max_batch):
        end = start + max_batch
        coll.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )
    return total


def semantic_search(
    query: str,
    n_results: int = 5,
    persist_dir: str | Path = CHROMA_PERSIST_DIR,
    doc_id_filter: str | None = None,
) -> tuple[list[dict], ProvenanceChain]:
    """
    Query ChromaDB for similar LDUs. Returns (list of {content, metadata}, ProvenanceChain).
    """
    path = Path(persist_dir)
    if not path.exists():
        return [], ProvenanceChain(citations=[])
    coll = get_or_create_collection(persist_dir)
    where = {"doc_id": doc_id_filter} if doc_id_filter else None
    results = coll.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    out = []
    citations = []
    if results and results["documents"] and results["documents"][0]:
        for j, doc in enumerate(results["documents"][0]):
            meta = (results["metadatas"][0] or [{}])[j] if results["metadatas"] else {}
            out.append({"content": doc, "metadata": meta})
            bbox = None
            try:
                bbox_json = (meta or {}).get("bbox_json") or ""
                if bbox_json:
                    bbox = BoundingBox.model_validate_json(bbox_json)
            except Exception:
                bbox = None
            citations.append(
                ProvenanceCitation(
                    document_name=meta.get("doc_id", ""),
                    page_number=int(meta.get("page_number", 1)),
                    bbox=bbox,
                    content_hash=meta.get("content_hash"),
                )
            )
    return out, ProvenanceChain(citations=citations)
