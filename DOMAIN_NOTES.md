## DOMAIN_NOTES (Phase 0)

This project is not “OCR a PDF”. It is an **escalation-gated, provenance-preserving** extraction pipeline that turns enterprise documents into structured, auditable data.

### Core failure modes observed / expected

- **Structure collapse**: plain OCR/text extraction flattens multi-column layouts; tables become unreadable strings.
- **Context poverty**: naive chunking splits tables/captions/clauses and makes downstream RAG hallucinate.
- **Provenance blindness**: without page + bbox provenance, you cannot audit claims.

### Decision tree (extraction strategy routing)

We route based on a `DocumentProfile` built in Stage 1.

- **If `origin_type=scanned_image`** → Strategy C (vision)  
  Rationale: no reliable character stream exists; non-vision pipelines produce empty/garbled text.

- **Else if `origin_type=native_digital` AND `layout_complexity=single_column`** → Strategy A (fast text)  
  Rationale: lowest cost, typically high fidelity when layout is simple.

- **Else** → Strategy B (layout-aware)  
  Rationale: multi-column and table-heavy documents require bounding boxes + reading-order reconstruction.

Mandatory escalation guard:

- Strategy A must compute a confidence score; if document confidence < threshold → automatically re-run with Strategy B.

### Current heuristics (initial thresholds)

These live in `rubric/extraction_rules.yaml` and are expected to be tuned empirically on the provided corpus.

- **Scanned-like** (sampled pages):
  - mean char density ≤ `scanned_char_density_max`
  - OR mean chars/page ≤ `scanned_char_count_max`
  - AND mean image area ratio ≥ `scanned_image_area_ratio_min`

- **Multi-column heuristic**:
  - histogram modes in character x-positions show 2 strong peaks separated by ≥ `multi_column_min_separation_pts`

- **Table-heavy heuristic**:
  - `pdfplumber.find_tables()` tables per 10 sampled pages ≥ `table_heavy_min_tables_per_10_pages`

### Provenance model (interim)

Interim output (`ExtractedDocument`) preserves:

- **page_number** on every `TextBlock`/`Table`
- **bbox** where available
  - Strategy A uses **full-page bbox** as a coarse provenance anchor
  - Strategy B uses word/table bboxes from `pdfplumber`

Full provenance chains (fact-level bbox + `content_hash`) are built in later phases, but the schema is already in `src/models/schemas.py`.

### Pipeline diagram (current interim implementation)

```mermaid
flowchart LR
  A[PDF] --> B[Triage Agent\n(pdfplumber signals)]
  B -->|DocumentProfile| C[Extraction Router]
  C -->|Strategy A| D[FastTextExtractor\n(pdfplumber extract_text)]
  C -->|Strategy B| E[LayoutExtractor\n(pdfplumber words + find_tables)]
  C -->|Strategy C| F[VisionExtractor\n(OpenRouter VLM)]
  D --> G[ExtractedDocument JSON]
  E --> G
  F --> G
  C --> H[extraction_ledger.jsonl]
  B --> I[profiles/*.json]
  G --> J[extractions/*.json]
```

### Next empirical work (when corpus is present)

- Tune scanned-vs-digital thresholds on 12 docs (3 per class) and record failure cases.
- Validate one table extraction per class and log table fidelity issues.
- Add cost estimation using model pricing (per selected OpenRouter model) and enforce budget cap.

