from __future__ import annotations

import argparse
import os
from pathlib import Path

from agents.extractor import extract_with_router
from agents.triage import classify_profile, save_profile
from refinery.config import load_config


def _iter_pdfs(pdf: str | None, input_path: str | None) -> list[Path]:
    if pdf:
        p = Path(pdf)
        if not p.exists():
            raise FileNotFoundError(str(p))
        return [p]
    if input_path:
        root = Path(input_path)
        if not root.exists():
            raise FileNotFoundError(str(root))
        if root.is_file() and root.suffix.lower() == ".pdf":
            return [root]
        return sorted([p for p in root.rglob("*.pdf")])
    raise ValueError("Provide --pdf or --input.")


def cmd_triage(args: argparse.Namespace) -> int:
    config = load_config(args.rules)
    pdfs = _iter_pdfs(args.pdf, args.input)
    for p in pdfs:
        profile = classify_profile(p, config)
        out = save_profile(profile)
        print(str(out))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    config = load_config(args.rules)
    pdfs = _iter_pdfs(args.pdf, args.input)
    for p in pdfs:
        outcome = extract_with_router(p, config, strategy=args.strategy)
        print(str(outcome.extraction_path))
    return 0


def cmd_chunk(args: argparse.Namespace) -> int:
    from agents.chunker import run_chunker
    path = Path(args.extraction) if args.extraction else Path(".refinery/extractions")
    if path.is_file():
        out = run_chunker(path, args.output)
        print(str(out))
    else:
        for f in path.glob("*.json"):
            out = run_chunker(f, args.output)
            print(str(out))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from agents.indexer import run_indexer
    path = Path(args.extraction) if args.extraction else Path(".refinery/extractions")
    out_dir = Path(args.output)
    if path.is_file():
        out = run_indexer(path, out_dir, use_llm_summaries=args.llm)
        print(str(out))
    else:
        for f in path.glob("*.json"):
            out = run_indexer(f, out_dir, use_llm_summaries=args.llm)
            print(str(out))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Run chunk + index + fact_table + chroma for each extraction in .refinery/extractions."""
    import json
    from agents.chunker import ChunkingEngine
    from agents.indexer import build_page_index
    from agents.fact_table import extract_fact_table
    from refinery.vector_store import ingest_ldus
    from models.schemas import ExtractedDocument

    extractions_dir = Path(args.extractions or ".refinery/extractions")
    if not extractions_dir.exists():
        print("No extractions dir:", extractions_dir)
        return 1
    for p in extractions_dir.glob("*.json"):
        doc = ExtractedDocument.model_validate_json(p.read_text(encoding="utf-8"))
        engine = ChunkingEngine()
        ldus = engine.run(doc)
        (Path(args.ldus or ".refinery/ldus")).mkdir(parents=True, exist_ok=True)
        (Path(args.ldus or ".refinery/ldus") / f"{doc.doc_id}_ldus.json").write_text(
            json.dumps([u.model_dump(mode="json") for u in ldus], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        index = build_page_index(doc)
        (Path(args.pageindex or ".refinery/pageindex")).mkdir(parents=True, exist_ok=True)
        (Path(args.pageindex or ".refinery/pageindex") / f"{doc.doc_id}.json").write_text(
            index.model_dump_json(indent=2), encoding="utf-8"
        )
        extract_fact_table(doc, Path(args.db or ".refinery/facts.db"))
        n = ingest_ldus(ldus, doc.doc_id, Path(args.chroma or ".refinery/chroma"))
        print(doc.doc_id, "ldus:", len(ldus), "chroma:", n)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from agents.query_agent import run_query
    answer, chain = run_query(args.question, args.doc_id)
    print("Answer:", answer[:2000])
    print("Provenance:", chain.model_dump_json(indent=2))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from agents.query_agent import verify_claim
    result = verify_claim(args.claim, args.doc_id)
    print("Verified:", result["verified"])
    print("Citations:", result["citations"].model_dump_json(indent=2))
    print("Answer:", result["answer"][:1500])
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """
    Generate a small 12-document demo corpus (3 per class A–D) and run the full pipeline
    to produce final-submission artifacts in `.refinery/`, including a populated
    `.refinery/example_qa_12.json`.
    """
    from refinery.demo_corpus import build_demo_qa_template, generate_demo_corpus, write_demo_qa_file
    from refinery.utils import doc_id_from_path

    config = load_config(args.rules)
    docs = generate_demo_corpus(args.output)

    # Extract all demo PDFs.
    for d in docs:
        extract_with_router(d.path, config, strategy="auto")

    # Ingest all extractions to produce LDUs, PageIndex, facts, and vector store.
    ingest_args = argparse.Namespace(
        extractions=str(args.extractions),
        ldus=str(args.ldus),
        pageindex=str(args.pageindex),
        db=str(args.db),
        chroma=str(args.chroma),
    )
    cmd_ingest(ingest_args)

    # Build and fill QA file.
    qa = build_demo_qa_template(docs)
    from agents.query_agent import run_query

    for item in qa:
        doc_path = Path(item["doc_path"])
        doc_id = doc_id_from_path(doc_path)
        ans, chain = run_query(item["question"], doc_id=doc_id)
        item["doc_id"] = doc_id
        item["answer"] = ans[:5000]
        item["provenance_chain"] = chain.model_dump(mode="json")

    out = write_demo_qa_file(args.qa_out, qa)
    print(str(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refinery")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_triage = sub.add_parser("triage", help="Run document triage and write DocumentProfile JSON.")
    p_triage.add_argument("--pdf", type=str, default=None, help="Path to a single PDF.")
    p_triage.add_argument("--input", type=str, default=None, help="Directory of PDFs (recursive).")
    p_triage.add_argument("--rules", type=str, default="rubric/extraction_rules.yaml", help="Rules YAML.")
    p_triage.set_defaults(func=cmd_triage)

    p_extract = sub.add_parser("extract", help="Run extraction router and write normalized extraction JSON.")
    p_extract.add_argument("--pdf", type=str, default=None, help="Path to a single PDF.")
    p_extract.add_argument("--input", type=str, default=None, help="Directory of PDFs (recursive).")
    p_extract.add_argument("--rules", type=str, default="rubric/extraction_rules.yaml", help="Rules YAML.")
    p_extract.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Override router strategy (auto|fast_text|layout|vision).",
    )
    p_extract.set_defaults(func=cmd_extract)

    p_chunk = sub.add_parser("chunk", help="Run ChunkingEngine on extraction JSON(s).")
    p_chunk.add_argument("--extraction", type=str, default=None, help="File or dir of extraction JSON.")
    p_chunk.add_argument("--output", type=str, default=".refinery/ldus", help="Output dir for LDU JSON.")
    p_chunk.set_defaults(func=cmd_chunk)

    p_index = sub.add_parser("index", help="Build PageIndex from extraction JSON(s).")
    p_index.add_argument("--extraction", type=str, default=None, help="File or dir of extraction JSON.")
    p_index.add_argument("--output", type=str, default=".refinery/pageindex", help="Output dir.")
    p_index.add_argument("--llm", action="store_true", help="Use LLM for section summaries.")
    p_index.set_defaults(func=cmd_index)

    p_ingest = sub.add_parser("ingest", help="Chunk + index + fact table + ChromaDB for all extractions.")
    p_ingest.add_argument("--extractions", type=str, default=".refinery/extractions", help="Extractions dir.")
    p_ingest.add_argument("--ldus", type=str, default=".refinery/ldus", help="LDUs dir.")
    p_ingest.add_argument("--pageindex", type=str, default=".refinery/pageindex", help="PageIndex dir.")
    p_ingest.add_argument("--db", type=str, default=".refinery/facts.db", help="SQLite facts DB.")
    p_ingest.add_argument("--chroma", type=str, default=".refinery/chroma", help="ChromaDB persist dir.")
    p_ingest.set_defaults(func=cmd_ingest)

    p_query = sub.add_parser("query", help="Ask a question; get answer + ProvenanceChain.")
    p_query.add_argument("question", type=str, help="Natural language question.")
    p_query.add_argument("--doc-id", type=str, default=None, help="Restrict to document.")
    p_query.set_defaults(func=cmd_query)

    p_audit = sub.add_parser("audit", help="Verify a claim; returns verified + citations or unverifiable.")
    p_audit.add_argument("claim", type=str, help="Claim to verify.")
    p_audit.add_argument("--doc-id", type=str, default=None, help="Restrict to document.")
    p_audit.set_defaults(func=cmd_audit)

    p_demo = sub.add_parser("demo", help="Generate demo corpus + run full pipeline + write example QA JSON.")
    p_demo.add_argument("--output", type=str, default="data/demo_corpus", help="Where to write demo PDFs.")
    p_demo.add_argument("--rules", type=str, default="rubric/extraction_rules.yaml", help="Rules YAML.")
    p_demo.add_argument("--extractions", type=str, default=".refinery/extractions", help="Extractions dir.")
    p_demo.add_argument("--ldus", type=str, default=".refinery/ldus", help="LDUs dir.")
    p_demo.add_argument("--pageindex", type=str, default=".refinery/pageindex", help="PageIndex dir.")
    p_demo.add_argument("--db", type=str, default=".refinery/facts.db", help="SQLite facts DB.")
    p_demo.add_argument("--chroma", type=str, default=".refinery/chroma", help="ChromaDB persist dir.")
    p_demo.add_argument("--qa-out", type=str, default=".refinery/example_qa_12.json", help="Output QA JSON path.")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    # Load environment variables from .env if present (OPENAI_API_KEY, OPENROUTER_API_KEY, etc).
    # This keeps the CLI demo reproducible without requiring shell-level exports.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

