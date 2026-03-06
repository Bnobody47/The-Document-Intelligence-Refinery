from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class DemoDoc:
    document_class: str  # A/B/C/D
    path: Path


def _make_png_bytes(text: str, w: int = 1400, h: int = 1800) -> bytes:
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.multiline_text((60, 60), text, fill=(0, 0, 0), font=font, spacing=6)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_simple_table(page: fitz.Page, x0: float, y0: float, col_w: float, row_h: float, headers: list[str], rows: list[list[str]]) -> None:
    ncols = max(1, len(headers))
    nrows = 1 + len(rows)
    # Grid
    for r in range(nrows + 1):
        y = y0 + r * row_h
        page.draw_line((x0, y), (x0 + ncols * col_w, y), color=(0, 0, 0), width=0.7)
    for c in range(ncols + 1):
        x = x0 + c * col_w
        page.draw_line((x, y0), (x, y0 + nrows * row_h), color=(0, 0, 0), width=0.7)
    # Text
    for c, h in enumerate(headers):
        page.insert_text((x0 + 6 + c * col_w, y0 + 14), str(h), fontsize=10)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            page.insert_text((x0 + 6 + c * col_w, y0 + (r + 1) * row_h + 14), str(v), fontsize=10)


def generate_demo_corpus(out_dir: str | Path = "data/demo_corpus") -> list[DemoDoc]:
    """
    Create 12 small PDFs (3 per class) so the pipeline can generate final artifacts
    even when the real corpus PDFs are not present in the repo.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs: list[DemoDoc] = []

    # Class A: native digital, multi-column-ish + financial tables
    for i in range(1, 4):
        p = out_dir / f"classA_annual_report_{i}.pdf"
        doc = fitz.open()
        try:
            page = doc.new_page()
            page.insert_text((72, 72), f"1. Executive Summary\nDemo Annual Report {i}\nRevenue: ETB 4.2B\n", fontsize=12)
            page.insert_text((72, 120), "2. Financial Statements\nIncome Statement (ETB)\n", fontsize=12)
            _draw_simple_table(
                page,
                x0=72,
                y0=170,
                col_w=150,
                row_h=26,
                headers=["Line Item", "FY 2023/24", "FY 2022/23"],
                rows=[
                    ["Total revenue", "4.2B", "3.8B"],
                    ["Net profit", "0.6B", "0.5B"],
                    ["Total assets", "12.1B", "11.5B"],
                ],
            )
            doc.save(str(p))
        finally:
            doc.close()
        docs.append(DemoDoc("A", p))

    # Class B: scanned (image-only) “audit report”
    for i in range(1, 4):
        p = out_dir / f"classB_scanned_audit_{i}.pdf"
        doc = fitz.open()
        try:
            page = doc.new_page()
            png = _make_png_bytes(
                f"INDEPENDENT AUDITOR'S REPORT\nDate: 2023-06-30\nOpinion: Unqualified\nDemo Scan {i}\n"
            )
            page.insert_image(page.rect, stream=png)
            doc.save(str(p))
        finally:
            doc.close()
        docs.append(DemoDoc("B", p))

    # Class C: mixed narrative + small table
    for i in range(1, 4):
        p = out_dir / f"classC_technical_assessment_{i}.pdf"
        doc = fitz.open()
        try:
            page = doc.new_page()
            page.insert_text((72, 72), f"1. Introduction\nAssessment Report {i}\nMethodology: survey + interviews\n", fontsize=12)
            page.insert_text((72, 130), "1.1 Key Findings\n- Weak internal controls\n- Limited transparency\n", fontsize=11)
            page.insert_text((72, 210), "2. Findings Table\n", fontsize=12)
            _draw_simple_table(
                page,
                x0=72,
                y0=240,
                col_w=180,
                row_h=26,
                headers=["Finding", "Severity", "Notes"],
                rows=[
                    ["Control gaps", "High", "Approval workflow missing"],
                    ["Reporting delays", "Medium", "Quarterly lag observed"],
                ],
            )
            doc.save(str(p))
        finally:
            doc.close()
        docs.append(DemoDoc("C", p))

    # Class D: table-heavy fiscal data
    for i in range(1, 4):
        p = out_dir / f"classD_tax_expenditure_{i}.pdf"
        doc = fitz.open()
        try:
            page = doc.new_page()
            page.insert_text((72, 72), f"1. Import Tax Expenditure Report\nFY 2019/20 – FY 2020/21\nDemo {i}\n", fontsize=12)
            page.insert_text((72, 120), "2. Summary Table\n", fontsize=12)
            _draw_simple_table(
                page,
                x0=72,
                y0=150,
                col_w=150,
                row_h=26,
                headers=["Category", "FY 2019/20", "FY 2020/21"],
                rows=[
                    ["Customs duty", "120M", "135M"],
                    ["VAT on imports", "210M", "225M"],
                    ["Excise tax", "55M", "60M"],
                ],
            )
            doc.save(str(p))
        finally:
            doc.close()
        docs.append(DemoDoc("D", p))

    return docs


def build_demo_qa_template(docs: list[DemoDoc]) -> list[dict]:
    """
    Create 12 QA prompts (3 per class) matching the assignment shape.
    This returns unanswered entries; the CLI demo command will fill answers + provenance.
    """
    by_class: dict[str, list[DemoDoc]] = {"A": [], "B": [], "C": [], "D": []}
    for d in docs:
        by_class[d.document_class].append(d)

    qa: list[dict] = []
    # A
    qa += [
        {"document_class": "A", "doc_path": str(by_class["A"][0].path), "question": "What was total revenue?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "A", "doc_path": str(by_class["A"][1].path), "question": "What were total assets?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "A", "doc_path": str(by_class["A"][2].path), "question": "What was net profit?", "answer": "", "provenance_chain": {"citations": []}},
    ]
    # B
    qa += [
        {"document_class": "B", "doc_path": str(by_class["B"][0].path), "question": "What is the auditor's opinion?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "B", "doc_path": str(by_class["B"][1].path), "question": "What is the date of the auditor's report?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "B", "doc_path": str(by_class["B"][2].path), "question": "Is the opinion qualified or unqualified?", "answer": "", "provenance_chain": {"citations": []}},
    ]
    # C
    qa += [
        {"document_class": "C", "doc_path": str(by_class["C"][0].path), "question": "What methodology was used?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "C", "doc_path": str(by_class["C"][1].path), "question": "List the key findings.", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "C", "doc_path": str(by_class["C"][2].path), "question": "What is the severity of 'Control gaps'?", "answer": "", "provenance_chain": {"citations": []}},
    ]
    # D
    qa += [
        {"document_class": "D", "doc_path": str(by_class["D"][0].path), "question": "What was VAT on imports for FY 2020/21?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "D", "doc_path": str(by_class["D"][1].path), "question": "Which category has the largest tax expenditure in FY 2020/21?", "answer": "", "provenance_chain": {"citations": []}},
        {"document_class": "D", "doc_path": str(by_class["D"][2].path), "question": "Summarize the trend in customs duty across the years.", "answer": "", "provenance_chain": {"citations": []}},
    ]

    return qa


def write_demo_qa_file(path: str | Path, qa: list[dict]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf-8")
    return p

