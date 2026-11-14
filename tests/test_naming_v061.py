"""Tests for the v0.6.1 filename-collision fix.

The v0.6.1 Beyond Vision re-scan classified 18/18 files as Product
Photo, but propose_filename collapsed them to 7 distinct names — the
topic picker took the first two non-skipped words of the vision text
("The image presents..." -> image_presents), so most files got the
same name. Executing those renames would have overwritten files in
the same folder. Two fixes, both tested here:

1. Richer topic: generic vision verbs (image/presents/depicts/...)
   and prompt-echo words are skipped, and garment / print / colour /
   material words are preferred, so the topic reads like the item.
2. Guaranteed uniqueness: deduplicate_filenames() appends _2, _3, ...
   when a (filename, folder) pair repeats within a scan.

The 18 vision texts live in tests/fixtures/beyond_vision_v061_texts.json,
extracted verbatim from sample_reports/beyond-vision-scan-v061.json
(no hand transcription). A guard test below re-checks them against the
scan JSON when it is present, so a transcription slip fails loudly.
"""
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(
    REPO_ROOT, "tests", "fixtures", "beyond_vision_v061_texts.json")
SCAN_JSON = os.path.join(
    REPO_ROOT, "sample_reports", "beyond-vision-scan-v061.json")

from hermes_organizer.naming import (  # noqa: E402
    deduplicate_filenames,
    propose_filename,
)

# The 7 names the old picker collapsed all 18 files into. None of them
# may appear in the fixed output.
OLD_COLLIDING_TOPICS = {
    "image_presents", "image_depicts", "image_appears", "image_product",
    "image_displays", "Transcribe_visible", "Quicksilver_www",
}


def _load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def test_fixture_matches_scan_json_when_present():
    """Guard: the fixture texts are verbatim copies of the scan JSON."""
    if not os.path.exists(SCAN_JSON):
        return
    with open(SCAN_JSON, encoding="utf-8") as fh:
        scan = json.load(fh)
    by_name = {r["metadata"]["original_name"]: r["extracted_text"]
               for r in scan["results"]}
    assert len(by_name) == 18
    for entry in _load_fixture():
        assert by_name[entry["original_name"]] == entry["extracted_text"], \
            entry["original_name"]


def test_v061_texts_produce_18_distinct_filenames():
    """(a) The 18 v0.6.1 vision texts -> 18 distinct destinations."""
    entries = _load_fixture()
    assert len(entries) == 18
    pairs = []
    for entry in entries:
        # The real scan passed each file's modified time, so the date
        # is pinned (2026-11-12) instead of depending on today.
        mtime = datetime.fromisoformat(
            entry["modified_time"].replace("Z", "+00:00"))
        name, folder = propose_filename(
            entry["original_name"], entry["subcategory"],
            entry["extracted_text"], mtime)
        pairs.append((name, folder))
    fixed = deduplicate_filenames(pairs)
    assert len(set(fixed)) == 18
    # No file keeps one of the old sentence-starter topics, and every
    # name keeps the documented date_Subcategory_topic.ext format.
    for name, folder in fixed:
        assert folder == "/Work/Product Photos/2026/"
        assert name.startswith("2026-11-12_ProductPhoto_")
        assert name.endswith(".jpg")
        topic = name[len("2026-11-12_ProductPhoto_"):-len(".jpg")]
        assert topic not in OLD_COLLIDING_TOPICS, name
        assert not topic.startswith("image_"), name


def test_counter_suffix_on_exact_duplicates():
    """(b) Same (filename, folder) three times -> base, _2, _3."""
    folder = "/Work/Product Photos/2026/"
    pairs = [
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
    ]
    fixed = deduplicate_filenames(pairs)
    assert fixed == [
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract_2.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract_3.jpg", folder),
    ]


def test_same_filename_different_folders_untouched():
    same = "2026-11-13_ProductPhoto_shirt_abstract.jpg"
    pairs = [
        (same, "/Work/Product Photos/2026/"),
        (same, "/Work/Product Photos/2025/"),
        (same, "/Work/Brand Assets/2026/"),
    ]
    assert deduplicate_filenames(pairs) == pairs


def test_preexisting_counter_suffix_counts_up():
    """A file legitimately named _2 plus a generated _2 -> _3, not a clash."""
    folder = "/Work/Product Photos/2026/"
    pairs = [
        ("2026-11-13_ProductPhoto_shirt_abstract_2.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
    ]
    fixed = deduplicate_filenames(pairs)
    assert fixed == [
        ("2026-11-13_ProductPhoto_shirt_abstract_2.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg", folder),
        ("2026-11-13_ProductPhoto_shirt_abstract_3.jpg", folder),
    ]


def test_dedup_passes_through_review_results():
    pairs = [
        (None, "/Review/Needs Human Review/"),
        (None, "/Review/Needs Human Review/"),
        ("2026-11-13_ProductPhoto_shirt_abstract.jpg",
         "/Work/Product Photos/2026/"),
    ]
    assert deduplicate_filenames(pairs) == pairs


def test_dedup_without_extension():
    pairs = [("notes", "/Work/"), ("notes", "/Work/")]
    assert deduplicate_filenames(pairs) == [
        ("notes", "/Work/"), ("notes_2", "/Work/")]


def test_scan_loop_deduplicates_identical_files(tmp_path):
    """Wiring: two copies of the same image in one scan get distinct
    proposed filenames (the _2 suffix), via scan_local_folder."""
    from hermes_organizer.watcher import scan_local_folder

    src = os.path.join(REPO_ROOT, "mock_samples", "IMG_0103_product.png")
    shutil.copy(src, tmp_path / "a.png")
    shutil.copy(src, tmp_path / "b.png")
    results = scan_local_folder(str(tmp_path))
    assert len(results) == 2
    names = [r.proposed_filename for r in results]
    assert names[0] is not None and names[1] is not None
    assert len(set(names)) == 2
    assert names[1].endswith("_2.png"), names
    assert results[0].target_folder_path == results[1].target_folder_path


def test_non_product_topic_unchanged():
    """Files that already got distinctive names keep their old topics:
    a receipt/invoice text still yields its own words, not 'Document'."""
    name, _ = propose_filename(
        "IMG_0042.png", "Receipt", "Acme Corp Invoice #123 Total $500")
    assert name.startswith("20") and "Receipt" in name
    assert "Acme_Corp" in name
