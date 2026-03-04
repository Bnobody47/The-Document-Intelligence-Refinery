from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def stable_content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def doc_id_from_path(path: str | Path) -> str:
    p = Path(path)
    # Stable-ish ID: filename + short hash of full resolved path.
    h = hashlib.sha256(str(p.resolve()).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", p.stem)[:60]
    return f"{stem}-{h}"


def append_jsonl(path: str | Path, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

