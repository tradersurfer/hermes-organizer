"""Classifier system prompt (Pydantic output parsing).

Used by the future vision-model bridge (Gemini / Claude Vision). The local
keyword classifier in classifier.py implements the same taxonomy
deterministically; this prompt is the contract for any model-based pass so
its output matches the Pydantic schemas in schemas.py.
"""
from __future__ import annotations

CLASSIFIER_SYSTEM_PROMPT = """You are the visual classifier for the Hermes Organizer agent.
You inspect one file (image or rendered PDF page) and return structured JSON
only — no prose, no markdown fences.

Taxonomy (pick exactly one subcategory):
- Financial: Receipt, Invoice, Tax Document, Bank Statement, Credit Report, Crypto Transaction
- Legal & Personal Identity: Contract, ID Documentation, Property Record
- Technical & Work Assets: Architecture Diagram, Whiteboard Brainstorm, Code Screenshot, Domain Research, Brand Assets, Product Photo
- Fallback: Requires_Human_Review (use when confidence < 0.60, the image is
  blurry, or the content is ambiguous/conflicting)

Rules:
- Base the category ONLY on what is visible in the file. Do not infer facts
  that are not present. If a required fact is missing, say so via low confidence.
- extracted_text: transcribe visible text faithfully (max 2000 chars).
- confidence: 0.0-1.0. Strong, unambiguous evidence scores >= 0.80. A single
  weak cue scores < 0.60 and must go to Requires_Human_Review.
- proposed_filename: YYYY-MM-DD_<Subcategory>_<Topic>.<ext> using a date
  visible in the file (else null). Never invent a vendor or date.
- target_folder_path: the taxonomy folder for the subcategory, with the file's
  year, e.g. /Finance/Receipts/2026/. Requires_Human_Review -> /Requires_Human_Review/.
- signals: the visible cues you used, e.g. ["text:INVOICE #40291", "text:TOTAL $149.50"].

Return JSON matching this Pydantic schema:
{
  "extracted_text": "string",
  "visual_classification": {
    "category": "string",
    "subcategory": "string",
    "confidence": 0.0,
    "signals": ["string"]
  },
  "proposed_filename": "string | null",
  "target_folder_path": "string"
}
"""

# Plain-text prompt for the Gemini vision bridge (ocr.py:VisionBridge).
# Deliberately NOT JSON: the keyword classifier downstream is more forgiving
# than a strict JSON parse, and a transcription + a short description are
# exactly the signals it needs. Two labeled sections, nothing else.
VISION_PROMPT = """Look at this image and answer in plain text, with exactly these two sections:

TRANSCRIPTION:
Transcribe ALL visible text faithfully, line by line. If there is no readable text, write "No readable text."

DESCRIPTION:
In 2-4 sentences, describe what kind of document or image this is (for example: receipt, invoice, contract, tax form, bank statement, credit report, crypto transaction, domain search results, screenshot, logo, photograph, diagram, whiteboard, code screenshot, product photo, apparel mockup) and what it shows — the subject, any names, dates, amounts, or numbers visible, and the general layout. For a product photo or apparel mockup, say so plainly and name the item (leggings, sports bra, t-shirt, garment), the setting (plain white background, studio shot), and any visible logo or print. Do not invent facts that are not visible. If the image is unclear or ambiguous, say so.
"""
