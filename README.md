 ## The Document Intelligence Refinery (Week 3)
 
 Production-style, multi-stage document intelligence pipeline that ingests heterogeneous documents and emits **structured, queryable, provenance-preserving** artifacts.
 
 ### What you get (interim submission focus)
 
 - **Stage 1 (Triage Agent)**: builds `DocumentProfile` JSON per document in `.refinery/profiles/`
 - **Stage 2 (Extraction Router)**: runs a strategy (A/B/C) with **confidence-gated escalation**
 - **Artifacts**:
   - `.refinery/extraction_ledger.jsonl` (strategy, confidence, timing, cost estimate)
   - `.refinery/extractions/<doc_id>.json` (normalized `ExtractedDocument`)
 
 ### Setup
 
 This repo uses the Windows Python launcher (`py`).
 
 Install dependencies:
 
 ```bash
 py -m pip install -e .[dev]
 ```
 
 ### Run (single PDF)
 
 ```bash
 py -m refinery.cli triage --pdf "path/to/doc.pdf"
 py -m refinery.cli extract --pdf "path/to/doc.pdf"
 ```
 
 ### Run (folder)
 
 ```bash
 py -m refinery.cli triage --input "data/pdfs"
 py -m refinery.cli extract --input "data/pdfs"
 ```
 
 ### Configuration
 
 - `rubric/extraction_rules.yaml` controls thresholds and routing defaults.
 - Outputs are written under `.refinery/`.
 
 ### Environment (for Strategy C / Vision)
 
 Set:
 
 - `OPENROUTER_API_KEY`
 - optionally `OPENROUTER_MODEL` (default is a cheap multimodal model)
 
 Strategy C is implemented but will error if the API key is missing.
 
