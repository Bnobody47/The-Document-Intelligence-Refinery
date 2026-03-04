from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RefineryConfig:
    rules_path: Path
    rules: dict[str, Any]


def load_config(rules_path: str | Path = Path("rubric") / "extraction_rules.yaml") -> RefineryConfig:
    rules_path = Path(rules_path)
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("Invalid YAML rules file (expected mapping at root).")
    return RefineryConfig(rules_path=rules_path, rules=rules)

