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

- **Strategy C (Vision)**: `OPENROUTER_API_KEY` (and optionally `OPENROUTER_MODEL`).
- **Optional LLM summaries for PageIndex**: `OPENAI_API_KEY` or `OPENROUTER_API_KEY`; use `py -m refinery.cli index --llm`.

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
