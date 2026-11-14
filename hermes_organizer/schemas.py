"""Pydantic schemas for Hermes Organizer v0.1.

Every classification result is validated against these models before it is
written to a report or the audit log. Structured output, no free-form guesses.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class VisualClassification(BaseModel):
    """Category assignment for one file."""

    category: str = Field(description="Top-level taxonomy category")
    subcategory: str = Field(description="Taxonomy subcategory, or Requires_Human_Review")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0-1.0; < 0.60 goes to human review")
    signals: list[str] = Field(default_factory=list, description="Keyword/filename signals that fired")
    best_guess: str | None = Field(
        default=None,
        description="Top-scoring subcategory when confidence < 0.60 (review); None when classified or when nothing scored",
    )


class FileMetadata(BaseModel):
    """Drive/local metadata for one scanned file."""

    file_id: str = Field(description="Drive file id, or local path for local scans")
    original_name: str
    mime_type: str
    size_bytes: int | None = None
    modified_time: str | None = None
    content_hash: str | None = Field(default=None, description="SHA-256 of the file bytes, for duplicate detection")


class OrganizerResult(BaseModel):
    """Full per-file result: metadata + OCR + classification + proposal."""

    metadata: FileMetadata
    extracted_text: str = Field(default="", description="OCR text, truncated to 2000 chars")
    ocr_engine: str = Field(default="rapidocr-local", description="Which OCR engine produced the text")
    visual_classification: VisualClassification
    proposed_filename: str | None = Field(
        default=None, description="None when confidence < 0.60 (human review)"
    )
    target_folder_path: str = Field(description="e.g. /Finance/Receipts/2026/")
    target_folder_id: str | None = Field(default=None, description="Resolved later, when writes are enabled")
    duplicate_of: str | None = Field(
        default=None,
        description="Original name of the first file with identical content, if this is a duplicate (flag only — never deleted or skipped)",
    )
    vision_used: bool = Field(
        default=False,
        description="True when the Gemini vision bridge improved this result (v0.3); False for OCR-only results",
    )
    action: str = Field(default="propose", description="v0.1 is always 'propose'; never 'execute'")
