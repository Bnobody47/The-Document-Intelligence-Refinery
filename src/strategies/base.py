from __future__ import annotations

from dataclasses import dataclass

from models.schemas import DocumentProfile, ExtractedDocument
from refinery.config import RefineryConfig


@dataclass(frozen=True)
class ExtractionResult:
    extracted: ExtractedDocument
    confidence: float
    cost_estimate_usd: float = 0.0
    notes: list[str] | None = None


class BaseExtractor:
    name: str

    def extract(self, profile: DocumentProfile, config: RefineryConfig) -> ExtractionResult:  # pragma: no cover
        raise NotImplementedError

