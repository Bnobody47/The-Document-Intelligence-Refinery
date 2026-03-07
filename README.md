## The Document Intelligence Refinery (Week 3)

Production-style, multi-stage document intelligence pipeline that ingests heterogeneous documents and emits **structured, queryable, provenance-preserving** artifacts.

### Pipeline (all 5 stages)

1. **Triage** → `DocumentProfile` in `.refinery/profiles/`
2. **Extraction** → normalized `ExtractedDocument` in `.refinery/extractions/` + `.refinery/extraction_ledger.jsonl`
3. **Chunking** → LDUs in `.refinery/ldus/` (ChunkValidator enforces 5 rules)
4. **PageIndex** → section tree in `.refinery/pageindex/`
5. **Query** → `pageindex_navigate`, `semantic_search`, `structured_query`; every answer has a `ProvenanceChain`

### Setup

```bash
py -m pip install -e .[dev]
```

### Run full pipeline (single PDF or folder)

```bash
# 1. Triage + extract
py -m refinery.cli triage --input data
py -m refinery.cli extract --input data

# 2. Chunk, index, fact table, vector store
py -m refinery.cli ingest

# 3. Query (answer + ProvenanceChain)
py -m refinery.cli query "What is total revenue?"

# 4. Audit (verify claim)
py -m refinery.cli audit "The report states revenue was 4.2B in Q3"
```

### Commands

| Command   | Description |
|----------|-------------|
| `triage` | DocumentProfile per PDF → `.refinery/profiles/` |
| `extract`| Multi-strategy extraction + ledger → `.refinery/extractions/`, `extraction_ledger.jsonl` |
| `chunk`  | LDU generation from extraction JSON → `.refinery/ldus/` |
| `index`  | PageIndex from extraction → `.refinery/pageindex/` |
| `ingest` | Chunk + index + fact table + ChromaDB in one step |
| `query`  | Natural language question → answer + ProvenanceChain |
| `audit`  | Claim verification → verified + citations or unverifiable |

### Configuration

- `rubric/extraction_rules.yaml`: triage thresholds, confidence weights, budget guard, chunking rules, domain keywords.

### Environment

- **Vision extraction (scanned PDFs)**:
  - Preferred: `OPENAI_API_KEY` (and optional `OPENAI_VISION_MODEL` / `OPENAI_MODEL`, default `gpt-4o-mini`).
  - Fallback: `OPENROUTER_API_KEY` (and optionally `OPENROUTER_MODEL`, default `google/gemini-2.0-flash-001`).
- **LLM summaries for PageIndex (CLI)**: `OPENAI_API_KEY` or `OPENROUTER_API_KEY`; use `py -m refinery.cli index --llm`.
- **LLM answer rewriting for queries**: if `OPENAI_API_KEY` is set, the query agent sends retrieved context
  (PageIndex + semantic search + facts) to OpenAI (default `gpt-4o-mini`) to generate a clear natural‑language answer,
  while provenance (ProvenanceChain) is still computed locally.

### Web UI

There is a small FastAPI demo app with a modern, dark‑themed UI:

```bash
py main.py
```

Then open `http://localhost:8000` and:

1. **Triage** — drag & drop a PDF. The app uploads it, runs triage, and shows a compact `DocumentProfile` summary card
   (doc_id, origin, layout, pages, domain, strategy).
2. **Run Pipeline** — click **Run Pipeline** to execute extraction, chunking, PageIndex, fact table, and vector store
   ingestion for the uploaded PDF.
3. **Ask with LLM Tools** — type a natural language question and click **Ask with LLM Tools**. The backend:
   - navigates the PageIndex,
   - runs semantic search over LDUs in Chroma,
   - optionally queries the facts table,
   - and (if `OPENAI_API_KEY` is set) calls OpenAI to rewrite the retrieved snippets into a concise answer.
4. **Provenance** — the UI shows the answer in a styled panel and a separate Provenance panel listing citations
   (document name, page numbers, and bounding boxes when available), so you can quickly verify the answer
   against the original PDF.

### Docker

```bash
docker build -t refinery .
docker run --rm refinery
```

### Artifacts (final submission)

- `.refinery/profiles/` — DocumentProfile JSON (min 12 docs, 3 per class)
- `.refinery/extractions/` — ExtractedDocument JSON
- `.refinery/extraction_ledger.jsonl` — strategy, confidence, cost
- `.refinery/ldus/` — LDU JSON per doc
- `.refinery/pageindex/` — PageIndex tree per doc
- `.refinery/facts.db` — SQLite fact table
- `.refinery/chroma/` — ChromaDB vector store
