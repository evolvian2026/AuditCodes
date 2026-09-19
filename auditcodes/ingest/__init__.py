"""PDF ingestion: layout-aware text recovery and template parsing into canonical questions."""

from __future__ import annotations

from pathlib import Path

from . import layout, template
from .template import ExtractionResult


def extract(pdf_path: str | Path, assets_dir: Path | None = None) -> ExtractionResult:
    """Parse a question-bank PDF into canonical questions, saving embedded images to ``assets_dir``."""
    doc = layout.load(pdf_path, assets_dir)
    return template.parse(doc, assets_dir)


__all__ = ["extract", "ExtractionResult", "layout", "template"]
