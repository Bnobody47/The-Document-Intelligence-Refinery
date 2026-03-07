from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agents.chunker import ChunkingEngine
from agents.extractor import extract_with_router
from agents.fact_table import extract_fact_table
from agents.query_agent import run_query
from agents.indexer import build_page_index
from models.schemas import ExtractedDocument
from refinery.config import load_config
from refinery.utils import doc_id_from_path
from refinery.vector_store import ingest_ldus


load_dotenv()

app = FastAPI(title="Document Intelligence Refinery Demo")


class PipelineRequest(BaseModel):
    pdf_path: str


class QueryRequest(BaseModel):
    doc_id: str
    question: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Document Intelligence Refinery</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    :root {
      --bg: #0f1419;
      --bg-card: #1a2332;
      --bg-input: #252f3d;
      --border: #2d3a4d;
      --accent: #3b82f6;
      --accent-hover: #60a5fa;
      --text: #e6edf3;
      --text-muted: #8b9eb5;
      --success: #22c55e;
      --radius: 12px;
    }
    * { box-sizing: border-box; }
    body {
      font-family: 'DM Sans', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      margin: 0;
      padding: 2rem 1.5rem;
      line-height: 1.6;
    }
    .container { max-width: 720px; margin: 0 auto; }
    h1 {
      font-size: 1.75rem;
      font-weight: 700;
      margin: 0 0 0.25rem 0;
      letter-spacing: -0.02em;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--accent);
      display: inline-block;
    }
    .subtitle { color: var(--text-muted); font-size: 0.95rem; margin-bottom: 1.5rem; }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1.25rem 1.5rem;
      margin-bottom: 1rem;
    }
    .card-title { font-weight: 600; font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; }
    #drop-zone {
      border: 2px dashed var(--border);
      border-radius: var(--radius);
      padding: 2.5rem;
      text-align: center;
      color: var(--text-muted);
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
    }
    #drop-zone:hover { border-color: var(--accent); background: rgba(59, 130, 246, 0.05); }
    #drop-zone.dragover { border-color: var(--accent); background: rgba(59, 130, 246, 0.1); }
    .btn {
      font-family: inherit;
      font-weight: 600;
      padding: 0.6rem 1.25rem;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      transition: background 0.2s, transform 0.1s;
    }
    .btn-primary { background: var(--accent); color: white; }
    .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .input {
      font-family: inherit;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.6rem 0.9rem;
      color: var(--text);
      font-size: 0.95rem;
      width: 100%;
      margin-bottom: 0.75rem;
    }
    .input:focus { outline: none; border-color: var(--accent); }
    .input::placeholder { color: var(--text-muted); }
    .flex { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
    #status { font-size: 0.9rem; color: var(--text-muted); }
    #status.success { color: var(--success); }
    .answer-box, .prov-box {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      background: #0d1117;
      color: #e6edf3;
      padding: 1rem 1.25rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .profile-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.5rem; margin-top: 0.5rem; }
    .profile-item { font-size: 0.85rem; }
    .profile-item span { color: var(--text-muted); }
    .profile-item strong { color: var(--accent); }
    .answer-pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: inherit; line-height: 1.5; }
    .citation { padding: 0.35rem 0; border-bottom: 1px solid var(--border); font-size: 0.8rem; }
    .citation:last-child { border-bottom: none; }
    .citation-num { color: var(--accent); font-weight: 600; margin-right: 0.5rem; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Document Intelligence Refinery</h1>
    <p class="subtitle">Drag & drop a PDF, run the extraction pipeline, then ask questions with provenance.</p>

    <div id="drop-zone" class="card">Drop PDF here or click to select</div>
    <input type="file" id="file-input" accept="application/pdf" style="display:none" />

    <div class="card">
      <div class="card-title">1. Run Extraction</div>
      <div class="flex">
        <button id="run-btn" class="btn btn-primary" disabled>Run Pipeline</button>
        <span id="status"></span>
      </div>
      <div id="profile-summary"></div>
    </div>

    <div class="card">
      <div class="card-title">2. Ask a Question</div>
      <input id="question" class="input" type="text" placeholder="e.g., What is the vision? What was total revenue?" />
      <button id="ask-btn" class="btn btn-primary" disabled>Ask with LLM Tools</button>
    </div>

    <div class="card">
      <div class="card-title">Answer</div>
      <div id="answer" class="answer-box" style="min-height: 4rem;">—</div>
    </div>

    <div class="card">
      <div class="card-title">Provenance</div>
      <div id="provenance" class="prov-box" style="min-height: 3rem; font-size: 0.8rem;">—</div>
    </div>
  </div>

  <script>
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const runBtn = document.getElementById('run-btn');
    const askBtn = document.getElementById('ask-btn');
    const statusEl = document.getElementById('status');
    const profileEl = document.getElementById('profile-summary');
    const answerEl = document.getElementById('answer');
    const provEl = document.getElementById('provenance');
    const questionEl = document.getElementById('question');

    let currentPdfPath = null;
    let currentDocId = null;

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        handleFile(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) {
        handleFile(e.target.files[0]);
      }
    });

    function handleFile(file) {
      if (file.type !== 'application/pdf') {
        alert('Please upload a PDF file.');
        return;
      }
      const formData = new FormData();
      formData.append('file', file);
      statusEl.textContent = 'Uploading...';
      statusEl.classList.remove('success');
      profileEl.innerHTML = '';
      answerEl.textContent = '—';
      provEl.textContent = '—';
      fetch('/upload', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(data => {
          currentPdfPath = data.pdf_path;
          currentDocId = data.doc_id;
          statusEl.textContent = 'Upload complete. Doc ID: ' + currentDocId;
          runBtn.disabled = false;
          askBtn.disabled = true;
        })
        .catch(err => {
          console.error(err);
          statusEl.textContent = 'Upload failed.';
        });
    }

    runBtn.addEventListener('click', () => {
      if (!currentPdfPath) return;
      statusEl.textContent = 'Running triage + extraction + ingest...';
      runBtn.disabled = true;
      askBtn.disabled = true;
      fetch('/run_pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pdf_path: currentPdfPath })
      })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            statusEl.textContent = 'Pipeline error: ' + data.error;
            runBtn.disabled = false;
            return;
          }
          currentDocId = data.doc_id;
          statusEl.textContent = 'Pipeline complete';
          statusEl.classList.add('success');
          const p = data.profile || {};
          profileEl.innerHTML = '<div class="profile-grid">' +
            '<div class="profile-item"><span>doc_id</span><br><strong>' + (p.doc_id || '') + '</strong></div>' +
            '<div class="profile-item"><span>origin</span><br><strong>' + (p.origin_type || '') + '</strong></div>' +
            '<div class="profile-item"><span>layout</span><br><strong>' + (p.layout_complexity || '') + '</strong></div>' +
            '<div class="profile-item"><span>pages</span><br><strong>' + (p.page_count || '') + '</strong></div>' +
            '<div class="profile-item"><span>domain</span><br><strong>' + (p.domain_hint || '') + '</strong></div>' +
            '<div class="profile-item"><span>strategy</span><br><strong>' + (p.strategy_used || '') + '</strong></div>' +
            '<div class="profile-item"><span>LDUs</span><br><strong>' + (p.ldu_count || '') + '</strong></div>' +
            '</div>';
          askBtn.disabled = false;
        })
        .catch(err => {
          console.error(err);
          statusEl.textContent = 'Pipeline failed.';
          runBtn.disabled = false;
        });
    });

    askBtn.addEventListener('click', () => {
      const q = questionEl.value.trim();
      if (!q) {
        alert('Please enter a question.');
        return;
      }
      statusEl.textContent = 'Running query...';
      answerEl.innerHTML = '<span style="color: var(--text-muted);">…</span>';
      provEl.innerHTML = '<span style="color: var(--text-muted);">…</span>';
      fetch('/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: currentDocId, question: q })
      })
        .then(r => r.json())
        .then(data => {
          statusEl.textContent = 'Query complete.';
          const ans = (data.answer || '').trim();
          answerEl.innerHTML = ans ? formatAnswer(ans) : '<span style="color: var(--text-muted);">—</span>';
          const prov = data.provenance || {};
          provEl.innerHTML = prov.citations && prov.citations.length
            ? formatProvenance(prov.citations)
            : '<span style="color: var(--text-muted);">—</span>';
        })
        .catch(err => {
          console.error(err);
          statusEl.textContent = 'Query failed.';
          answerEl.innerHTML = '<span style="color: #ef4444;">Error</span>';
          provEl.innerHTML = '—';
        });
    });

    function formatAnswer(text) {
      return '<pre class="answer-pre">' + escapeHtml(text) + '</pre>';
    }

    function formatProvenance(citations) {
      return citations.map((c, i) => {
        const page = c.page_number != null ? 'p.' + c.page_number : '';
        const bbox = c.bbox ? ' [bbox]' : '';
        return '<div class="citation"><span class="citation-num">' + (i + 1) + '</span> ' +
          escapeHtml(c.document_name || '') + ' ' + page + bbox + '</div>';
      }).join('');
    }

    function escapeHtml(s) {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }
  </script>
</body>
</html>
    """


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)) -> JSONResponse:
    if file.content_type != "application/pdf":
        return JSONResponse({"error": "Only PDF files are supported."}, status_code=400)
    upload_dir = Path("data") / "web_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / file.filename
    contents = await file.read()
    target.write_bytes(contents)
    doc_id = doc_id_from_path(target)
    return JSONResponse({"pdf_path": str(target), "doc_id": doc_id})


@app.post("/run_pipeline")
def run_pipeline(req: PipelineRequest) -> JSONResponse:
    pdf_path = Path(req.pdf_path)
    if not pdf_path.exists():
        return JSONResponse({"error": f"PDF not found: {pdf_path}"}, status_code=400)

    try:
        config = load_config()
        outcome = extract_with_router(pdf_path, config, strategy="auto")
        profile = outcome.profile
        extracted: ExtractedDocument = outcome.extracted

        # Stage 3: chunking into LDUs
        engine = ChunkingEngine(config)
        ldus = engine.run(extracted)
        ldu_dir = Path(".refinery") / "ldus"
        ldu_dir.mkdir(parents=True, exist_ok=True)
        ldu_path = ldu_dir / f"{extracted.doc_id}_ldus.json"
        ldu_path.write_text(
            json.dumps([u.model_dump(mode="json") for u in ldus], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Stage 4: PageIndex
        index = build_page_index(extracted, config, use_llm_summaries=False)
        pageindex_dir = Path(".refinery") / "pageindex"
        pageindex_dir.mkdir(parents=True, exist_ok=True)
        (pageindex_dir / f"{extracted.doc_id}.json").write_text(
            index.model_dump_json(indent=2), encoding="utf-8"
        )

        # Stage 4b: Fact table
        extract_fact_table(extracted, Path(".refinery") / "facts.db")

        # Stage 4c: Vector store ingestion
        ingest_ldus(ldus, extracted.doc_id, Path(".refinery") / "chroma")

        profile_summary = {
            "doc_id": profile.doc_id,
            "origin_type": profile.origin_type.value,
            "layout_complexity": profile.layout_complexity.value,
            "page_count": profile.page_count,
            "domain_hint": profile.domain_hint.value,
            "strategy_used": extracted.strategy_used,
            "ldu_count": len(ldus),
        }
        return JSONResponse({"doc_id": extracted.doc_id, "profile": profile_summary})
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.post("/query")
def query(req: QueryRequest) -> JSONResponse:
    answer, chain = run_query(req.question, doc_id=req.doc_id)
    return JSONResponse(
        {
            "answer": answer,
            "provenance": chain.model_dump(mode="json"),
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

