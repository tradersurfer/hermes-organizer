"""Tests for the v0.4 taxonomy: Crypto Transaction, Domain Research,
Brand Assets, and strengthened Code Screenshot keywords.

The long texts below are the REAL OCR / vision texts copied verbatim from
the v0.3 live-scan JSONs (sample_reports/drive-scan-v03.json and
drive-live-scan-v03.json), not invented. The one exception is the logo
vision description: the v0.3 scan did not keep vision text for logo.jpg
(it scored 0.0), so the test uses a representative description of the
kind the bridge produces — the live v0.4 re-scan is the real proof for
that file.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer.classifier import classify  # noqa: E402
from hermes_organizer.naming import propose_filename  # noqa: E402
from hermes_organizer.taxonomy import CONFIDENCE_THRESHOLD  # noqa: E402

BTC_TEXT = 'anaveragepriceof$74,122.28. 11:37 Crypto Order Filled Yourordertobuy$11.11worthof BTChasbeenfilledatan averagepriceof$75,021.95. OCL 4 Z3 INOV TRANSCRIPTION: an average price of $74,122.28. 11:37 Crypto Order Filled Your order to buy $11.11 worth of BTC has been filled at an average price of $75,021.95. Oct 14 23 Nov DESCRIPTION: This is a cropped screenshot from a cryptocurrency trading application displaying a dark mode interface. It shows a notification card titled "Crypto Order Filled" confirming a purchase of $11.11 worth of BTC at an average price of $75,021.95 at 11:37. Above the main message box, partial text from a previous filled order mentions an average price of $74,122.28, while a timeline axis with date labels ("Oct", "14", "23", "Nov") is partially visible at the bottom.'

DOMAIN_141325_TEXT = 'beyondthevision.com Explore more $29.98/yr beyondthevision.io 55% OFF ☆ Add to cart Retail S65.98/yr $2.98/yr beyondthevision.fit O %6 Add to cart Retail S33.98/yr $33.98/yr beyondthevision.fashion Add to cart Retail S33.98/yr $5.98/yr beyondthevision.chat %68 Add to cart Retail $53.48/yr $2.98/yr beyondthevision.life 93% OFF Add to cart Retail S45.48/yr $2.00/yr beyondthevision.xyz %6 Add to cart Retail S21.48/yr $2.98/yr beyondthevision.live %6 Add to cart Retail $41.48/yr $0.98/yr beyondthevision.online ONE PER CUSTOMER Add to cart Retail S2.88/yr $0.98/yr beyondthevision.site ONE PER CUSTOMER ☆ Add to cart Retail S1.78/yr $3.98/yr beyondthevision.art $25.98 FOR 2 YEARS Add to cart Retail $25.98/yr $7.68/vr'

DOMAIN_141348_TEXT = 'beyondthevision.com Suggested Results Hide $53.48/yr beyondthevision.vision Retail $53.48/yr beyondthevision.com $2,495.00 PREMIUM Renews at $19.98/yr $39.98/yr beyondthevision.to Retail $39.98/yr $89.98/yr beyondthevision.ai 14% OFF Retail S89.98/yr SSL Site security made simple $11.00/yr'

REPLIT_TEXT = "client/src/pag X Untitled docur X 三 ABOUT US PA Voice-Signupx Admin Conso sk The Success E sk 700 Credit Clu G 4-Pt Credit Ma → C replit.com/@jegroup66/Jeci-Interactive ☆ ☆ Ca v Jeci Interactive Design Preview Publishing Shell >_Console Home.tsx 十 α °+ Share Republish 0 G Improve mobile banne... Files 田田: 口 →C.replit.dev/ banner and make it in sync with the This app is in Design Mode which is best for rapid designs & websites. Click to convert to a fully functional application. × Search current home page. and enhance the HIUUIC.LSA design, make it full width and mobile s use-toast.ts JECI friendly SERVICES ACCOUNTING ABOUT PRICING CONTACT Book Consultatic 口ib GROUP pages 3 minutes ago services Solutons For'You Engine.tsx 2actions Foundation.tsx I have successfully enhanced and Growth.tsx synchronized the 7oo Credit Club mobile We empower individuals and businesses to navigate complex financial tools banner with the JECI Group's core identity. Financial Model.tsx landscapes and achieve sustainable growth through expert Retirement Calculator.tsx Improvements Made: accounting,tax services, and s Wealth Diversification.tsx ·Optimal Positioning:Moved the banner 700 Credit Club Weekly Tracker.tsx from an absolute overlay to a relative Fix Credit,Buildto800Fi CO,Unlock About.tsx flow position between the Hero section and the Accounting services. This ensures BTCLoans&Wealth Accounting Service Detail.tsx it fees like scrollt latest e page Book Free Consultation Consultation.tsx JOINNOW→ iournevrather than a floating element Contact.tsx the positioning of the homepage banner is Credit Club Details.tsx still off.its not correct.checck the Explore Services.tsx screenshot.add a subpage nav to the credit Founders Suite Detail.tsx club details pagel JECI Group Money Vibes Hub Home.tsx not-found.tsx Privacy.tsx"

# Representative vision description for logo.jpg (see module docstring).
LOGO_VISION_TEXT = """70O CREDITCLUB EXPERTS
TRANSCRIPTION: 700 CREDIT CLUB EXPERTS
DESCRIPTION: This is a logo for 700 Credit Club Experts, a credit repair
company. The image shows the company wordmark in bold letters on a plain
background."""


def test_crypto_transaction_classified():
    c = classify("Screenshot 2026-11-11 113832.png", BTC_TEXT)
    assert c.subcategory == "Crypto Transaction"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_crypto_beats_plain_receipt():
    # "purchase" scores Receipt 2, but the crypto signals must win.
    c = classify("Screenshot 2026-11-11 113832.png", BTC_TEXT)
    assert c.subcategory == "Crypto Transaction"
    assert "text:purchase" in c.signals  # the Receipt cue is present...
    assert c.subcategory != "Receipt"  # ...and still loses


def test_domain_research_141325():
    c = classify("Screenshot_2026-11-10_141325.png", DOMAIN_141325_TEXT)
    assert c.subcategory == "Domain Research"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_domain_research_141348():
    c = classify("Screenshot_2026-11-10_141348.png", DOMAIN_141348_TEXT)
    assert c.subcategory == "Domain Research"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_replit_code_screenshot():
    c = classify("Screenshot_2026-02-05.png", REPLIT_TEXT)
    assert c.subcategory == "Code Screenshot"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_logo_brand_assets_with_vision_text():
    c = classify("logo.jpg", LOGO_VISION_TEXT)
    assert c.subcategory == "Brand Assets"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_logo_ocr_only_stays_review_but_guesses_brand():
    # OCR alone has no "logo" word; only the filename signal fires.
    # It must stay in review (not misread as a Credit Report) with
    # Brand Assets as the best guess — vision is what rescues it.
    c = classify("logo.jpg", "70O CREDITCLUB EXPERTS")
    assert c.subcategory == "Requires_Human_Review"
    assert c.best_guess == "Brand Assets"


def test_no_cross_talk():
    # Each target text must land in its own category, not a neighbour's.
    assert classify("Screenshot_2026-02-05.png",
                    REPLIT_TEXT).subcategory == "Code Screenshot"
    assert classify("Screenshot_2026-11-10_141325.png",
                    DOMAIN_141325_TEXT).subcategory == "Domain Research"
    assert classify("Screenshot 2026-11-11 113832.png",
                    BTC_TEXT).subcategory == "Crypto Transaction"
    # A plain store receipt must NOT become crypto.
    c = classify("IMG_0042.png",
                 "RECEIPT #8812 TOTAL $17.36 VISA **** 4242 THANK YOU")
    assert c.subcategory == "Receipt"
    # A credit report must NOT become crypto or brand.
    c = classify("credit_client_results.jpg",
                 "YourCreditScores CreditHeroScore EQUIFAX TransUnion "
                 "experian 763 VeryGood")
    assert c.subcategory == "Credit Report"
    assert c.confidence >= CONFIDENCE_THRESHOLD


def test_naming_new_subcategories():
    _, folder = propose_filename("x.png", "Crypto Transaction",
                                 "DATE: 10/12/2026")
    assert folder == "/Finance/Crypto Transactions/2026/"
    _, folder = propose_filename("x.png", "Domain Research",
                                 "DATE: 10/12/2026")
    assert folder == "/Work/Domain Research/2026/"
    _, folder = propose_filename("x.png", "Brand Assets",
                                 "DATE: 10/12/2026")
    assert folder == "/Work/Brand Assets/2026/"
    name, _ = propose_filename("Screenshot 2026-11-11 113832.png",
                               "Crypto Transaction", BTC_TEXT)
    assert name.startswith("20") and "CryptoTransaction" in name


def test_mock_scan_v04():
    """Full mock scan: the 5 originals keep their exact confidences and
    the 4 new mocks land in the new subcategories. (v0.5: a 10th mock,
    IMG_0103_product.png, was added for Product Photo; the 9 originals
    below are unchanged.)"""
    from hermes_organizer.watcher import scan_local_folder

    results = scan_local_folder(
        os.path.join(REPO_ROOT, "mock_samples"), audit_log=None)
    by_name = {r.metadata.original_name: r for r in results}
    assert len(results) == 10
    expected_originals = {
        "IMG_0042_receipt.png": ("Receipt", 0.80),
        "IMG_0043_invoice.png": ("Invoice", 0.79),
        "IMG_0099_contract.png": ("Contract", 0.84),
        "Screenshot_code.png": ("Code Screenshot", 0.78),
        "scan_whiteboard.png": ("Whiteboard Brainstorm", 0.83),
    }
    for name, (sub, conf) in expected_originals.items():
        r = by_name[name]
        assert r.visual_classification.subcategory == sub, name
        # Confidences are stored at 3 decimals (e.g. Invoice 0.786); the
        # "0.79" in earlier reports is the 2-decimal display of that value.
        assert round(r.visual_classification.confidence, 2) == conf, name
    expected_new = {
        "IMG_0101_crypto.png": "Crypto Transaction",
        "IMG_0102_domain.png": "Domain Research",
        "logo_brand.png": "Brand Assets",
        "Screenshot_code_editor.png": "Code Screenshot",
        "IMG_0103_product.png": "Product Photo",  # v0.5
    }
    for name, sub in expected_new.items():
        r = by_name[name]
        assert r.visual_classification.subcategory == sub, name
        assert r.visual_classification.confidence >= CONFIDENCE_THRESHOLD, name
