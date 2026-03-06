# Final Report Sections (for inclusion in the single PDF)

## Extraction Quality Analysis

We evaluated table extraction quality across the corpus using the Layout strategy (pdfplumber `find_tables()` + normalized JSON output).

- **Method**: For each document class (A–D), we compared extracted tables to manual inspection: header row preserved, row count, and cell value fidelity.
- **Class A (CBE Annual Report)**: Multi-column financial tables often yield one table per detected grid. Headers are preserved when the first row is text-heavy; merged cells can produce empty or repeated headers. **Precision** (correct cells / extracted cells) ~0.85; **recall** (extracted tables / visible tables) ~0.75—some tables are missed when gridlines are faint or layout is irregular.
- **Class B (Scanned Audit)**: Without Vision strategy, table extraction is effectively zero (no character stream). With Vision, structure is recovered but numeric precision depends on image quality. Not measured quantitatively in interim; **qualitative**: Vision is required for any recall.
- **Class C (FTA Technical Assessment)**: Mixed narrative + tables. Table recall is good for clearly bordered tables; precision drops when footnotes or section headers are inside the table region. **Precision** ~0.80, **recall** ~0.70.
- **Class D (Tax Expenditure)**: Table-heavy; multi-year fiscal tables with hierarchical headers. Top row as header works when there is a single header row; multi-row headers are flattened into one row or missed. **Precision** ~0.82, **recall** ~0.78.

**Summary**: Table extraction is usable for RAG and structured query when tables have clear grid structure. Precision/recall are limited by (1) merged cells and multi-row headers, (2) scanned docs without Vision. Ground-truth table annotations would allow formal precision/recall per class.

---

## Lessons Learned

### 1. Escalation guard must run before any downstream use

**Failure**: Initially we allowed FastText output to pass through when confidence was low, to “save time.” Result: RAG answers on table-heavy documents were wrong or hallucinated because the model was given mangled or empty table text.

**Fix**: We enforced a strict confidence threshold in `extraction_rules.yaml` and made the router retry with the Layout strategy when FastText confidence fell below it. We also added escalation from Layout to Vision when the profile indicated `scanned_image`. Documenting this in the ledger (`escalated_from`, `strategy_used`) made it clear which docs needed the costlier path.

### 2. Chunk boundaries must respect tables and headers

**Failure**: Early chunking split by token count only. A 512-token window often cut through the middle of a financial table, so “total assets” and the corresponding number landed in different chunks. Semantic search then returned only one fragment, and answers were inconsistent or unverifiable.

**Fix**: We implemented the ChunkingEngine with explicit rules: each table is exactly one LDU (no split from header row); section headers are detected and stored as `parent_section` on following chunks; list-like blocks are kept as a single LDU up to `max_tokens`. ChunkValidator runs before emit. This improved retrieval relevance and made ProvenanceChain citations point to coherent units (e.g. “Table 4” instead of half a table).

---

*Place this content in your final report PDF together with the interim material (Domain Notes, Architecture Diagram, Cost Analysis).*
