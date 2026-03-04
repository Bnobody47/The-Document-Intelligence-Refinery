from __future__ import annotations

import argparse
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

