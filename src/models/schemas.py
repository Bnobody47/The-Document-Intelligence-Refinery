from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OriginType(str, Enum):
    native_digital = "native_digital"
    scanned_image = "scanned_image"
    mixed = "mixed"
    form_fillable = "form_fillable"


class LayoutComplexity(str, Enum):
    single_column = "single_column"
    multi_column = "multi_column"
    table_heavy = "table_heavy"
    figure_heavy = "figure_heavy"
    mixed = "mixed"


class ExtractionCostTier(str, Enum):
    fast_text_sufficient = "fast_text_sufficient"
    needs_layout_model = "needs_layout_model"
    needs_vision_model = "needs_vision_model"


class DomainHint(str, Enum):
    financial = "financial"
    legal = "legal"
    technical = "technical"
    medical = "medical"
    general = "general"


class BoundingBox(BaseModel):
    x0: float
    top: float
    x1: float
    bottom: float


class PageRef(BaseModel):
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None


class TriageSignals(BaseModel):
    page_count: int = Field(..., ge=1)
    mean_chars_per_page: float = Field(..., ge=0)
    mean_char_density: float = Field(..., ge=0)
    mean_image_area_ratio: float = Field(..., ge=0, le=1)
    estimated_tables_per_10_pages: float = Field(..., ge=0)
    multi_column_score: float = Field(..., ge=0, le=1)
    has_form_fields: bool = False


class DocumentProfile(BaseModel):
    doc_id: str
    source_path: str
    page_count: int

    origin_type: OriginType
    layout_complexity: LayoutComplexity

    language: str = "unknown"
    language_confidence: float = Field(0.0, ge=0, le=1)

    domain_hint: DomainHint = DomainHint.general
    estimated_extraction_cost: ExtractionCostTier

    triage_signals: TriageSignals
    notes: list[str] = Field(default_factory=list)


class TextBlock(BaseModel):
    text: str
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None


class Table(BaseModel):
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None
    headers: list[str]
    rows: list[list[str]]


class Figure(BaseModel):
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None
    caption: str | None = None


class ExtractedDocument(BaseModel):
    doc_id: str
    source_path: str
    strategy_used: Literal["fast_text", "layout", "vision"]
    page_count: int

    text_blocks: list[TextBlock] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)

    # Ordered list of (page_number, index into text_blocks) for rough reading order.
    reading_order: list[tuple[int, int]] = Field(default_factory=list)

    # Arbitrary vendor-native payload for debugging (kept optional to avoid bloat).
    raw: dict[str, Any] | None = None


class LDUType(str, Enum):
    paragraph = "paragraph"
    table = "table"
    figure = "figure"
    list_item = "list_item"
    header = "header"


class LDU(BaseModel):
    ldu_id: str
    content: str
    chunk_type: LDUType
    page_refs: list[PageRef]
    bounding_box: BoundingBox | None = None
    parent_section: str | None = None
    token_count: int = Field(..., ge=0)
    content_hash: str
    relationships: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageIndexNode(BaseModel):
    title: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    child_sections: list["PageIndexNode"] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    summary: str | None = None
    data_types_present: list[str] = Field(default_factory=list)


class PageIndex(BaseModel):
    doc_id: str
    root: PageIndexNode


class ProvenanceCitation(BaseModel):
    document_name: str
    page_number: int = Field(..., ge=1)
    bbox: BoundingBox | None = None
    content_hash: str | None = None


class ProvenanceChain(BaseModel):
    citations: list[ProvenanceCitation] = Field(default_factory=list)

