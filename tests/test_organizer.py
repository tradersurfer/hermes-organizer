"""Tests for taxonomy scoring, naming, and the classifier."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.classifier import classify, is_unorganized
from hermes_organizer.naming import extract_date, propose_filename
from hermes_organizer.taxonomy import CONFIDENCE_THRESHOLD


def test_receipt_classified():
    c = classify("IMG_0042.png", "RECEIPT #8812 TOTAL $17.36 VISA **** 4242 THANK YOU")
    assert c.subcategory == "Receipt"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_invoice_classified():
    c = classify("scan_3.pdf", "INVOICE #40291 BILL TO Acme BALANCE DUE $1,200 Net 30 remit")
    assert c.subcategory == "Invoice"


def test_contract_classified():
    c = classify("IMG_0099.png", "SERVICE AGREEMENT parties agree terms and conditions signature governing law")
    assert c.subcategory == "Contract"


def test_credit_report_classified():
    c = classify("credit_client_results.jpg",
                 "YourCreditScores CreditHeroScore EQUIFAX TransUnion experian 763 VeryGood")
    assert c.subcategory == "Credit Report"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_low_confidence_goes_to_review():
    c = classify("IMG_5157.png", "")
    assert c.subcategory == "Requires_Human_Review"
    assert c.confidence < CONFIDENCE_THRESHOLD


def test_filename_only_hint_stays_under_threshold():
    # A bare "receipt" in the filename alone must not clear the bar.
    c = classify("receipt.png", "")
    assert c.subcategory == "Requires_Human_Review"


def test_unorganized_filter():
    assert is_unorganized("IMG_0042.jpg")
    assert is_unorganized("Screenshot 2026-11-10.png")
    assert not is_unorganized("2026-10-12_Receipt_Vendor.png")


def test_date_extraction():
    assert extract_date("DATE: 10/12/2026").strftime("%Y-%m-%d") == "2026-10-12"
    assert extract_date("2026-04-15 invoice").strftime("%Y-%m-%d") == "2026-04-15"


def test_propose_filename_format():
    name, folder = propose_filename(
        "IMG_0042.png", "Receipt", "RECEIPT TOTAL $17.36 DATE: 10/12/2026"
    )
    assert name.startswith("2026-10-12_Receipt_")
    assert name.endswith(".png")
    assert folder == "/Finance/Receipts/2026/"


# --- v0.2 tests ---

def test_false_positives_go_to_review():
    # Boundary-aware matching: "tax" must not match "taxi", "lot" not "pilot",
    # "api" not "capital", "git" not "digital".
    c = classify("IMG_0001.png", "taxi ride to the pilot capital digital meeting")
    assert c.subcategory == "Requires_Human_Review"


def test_unpaid_does_not_count_as_paid():
    c = classify("x.png", "unpaid balance")
    assert c.subcategory == "Requires_Human_Review"


def test_camelcase_credit_scores():
    c = classify("scores.jpg", "YourCreditScores Equifax 763")
    assert c.subcategory == "Credit Report"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_plural_error_matches():
    c = classify("Screenshot_code.png", "Traceback ValueError errors in terminal python")
    assert c.subcategory == "Code Screenshot"


def test_dob_safe_date_extraction():
    assert extract_date(
        "Date of birth: 01/15/1990 Document date: 03/02/2026"
    ).strftime("%Y-%m-%d") == "2026-03-02"


def test_best_guess_on_review():
    # "receipt" alone: 3 pts -> 3/6 = 0.50, under threshold, best guess kept.
    c = classify("IMG_5157.png", "receipt")
    assert c.subcategory == "Requires_Human_Review"
    assert c.best_guess == "Receipt"


def test_duplicate_detection(tmp_path):
    import shutil

    from hermes_organizer.watcher import scan_local_folder

    src = os.path.join(REPO_ROOT, "mock_samples", "IMG_0042_receipt.png")
    shutil.copy(src, tmp_path / "copy_a.png")
    shutil.copy(src, tmp_path / "copy_b.png")
    results = scan_local_folder(str(tmp_path), audit_log=None)
    assert len(results) == 2
    assert results[0].duplicate_of is None
    assert results[1].duplicate_of == "copy_a.png"
    assert results[0].metadata.content_hash == results[1].metadata.content_hash


def test_html_report_smoke():
    import json

    from hermes_organizer.report import build_image_lookup, render_html

    with open(os.path.join(REPO_ROOT, "sample_reports", "mock-scan-fresh.json"),
              encoding="utf-8") as fh:
        payload = json.load(fh)
    lookup = build_image_lookup([os.path.join(REPO_ROOT, "mock_samples")])
    page = render_html([payload], lookup)
    assert "IMG_0042_receipt" in page
    assert "0.80" in page
