#!/usr/bin/env python3
"""Generate generic mock samples (receipt, whiteboard, code screenshot,
invoice, contract) as PNG images with rendered text, for local testing.
No real user data — all content is invented and generic.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def _font(size: int = 28):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make(name: str, lines: list[str], size=(900, 600)) -> str:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    f = _font(28)
    y = 30
    for line in lines:
        d.text((30, y), line, fill="black", font=f)
        y += 42
    path = os.path.join(OUT, name)
    img.save(path)
    return path


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    make("IMG_0042_receipt.png", [
        "CORNER MARKET", "RECEIPT #8812", "DATE: 10/12/2026",
        "Milk 2%  $4.29", "Bread  $3.10", "Coffee  $8.99",
        "SUBTOTAL  $16.38", "TAX  $0.98", "TOTAL  $17.36",
        "VISA **** 4242", "THANK YOU",
    ])
    make("IMG_0043_invoice.png", [
        "INVOICE #40291", "BILL TO: Acme Studio LLC",
        "DATE: 10/28/2026", "DUE DATE: 11/27/2026",
        "Design services  $1,200.00", "BALANCE DUE  $1,200.00",
        "Payment terms: Net 30", "Please remit to accounts@acme.example",
    ])
    make("scan_whiteboard.png", [
        "WHITEBOARD — Q4 BRAINSTORM", "Roadmap: launch, onboard, scale",
        "Sprint 12: auth, billing, search", "Idea: referral loop",
        "Kanban: todo / doing / done",
    ])
    make("Screenshot_code.png", [
        "terminal — python run_scan.py",
        "Traceback (most recent call last):",
        '  File "run_scan.py", line 42, in main',
        "    raise SystemExit(main())",
        "ValueError: invalid literal for int()",
        "def classify(text): return score",
    ])
    make("IMG_0099_contract.png", [
        "SERVICE AGREEMENT", "This Contract is entered into on",
        "January 5, 2026, by and between Northside LLC and",
        "the Contractor. The parties agree to the following",
        "terms and conditions. Signature: ______________",
        "Governing law: District of Columbia.",
    ])
    # v0.4: mocks for the new subcategories (Crypto Transaction,
    # Domain Research, Brand Assets) and a Replit code-editor shot.
    make("IMG_0101_crypto.png", [
        "CRYPTO ORDER FILLED",
        "Your order to buy $25.00 worth of BTC",
        "has been filled at an average price",
        "of $74,500.00. Coinbase Wallet",
        "Transaction ID: 9F2C41AA",
    ])
    make("IMG_0102_domain.png", [
        "DOMAIN SEARCH — example.com",
        "example.com  $12.99/yr",
        "example.net  $14.99/yr",
        "Suggested Results",
        "Premium domain  $1,499.00",
        "Renews at $12.99/yr  SSL included",
        "Add to cart",
    ])
    make("logo_brand.png", [
        "ACME WORDMARK",
        "LOGO — BRAND ASSETS",
        "Primary logo, dark background",
        "Trademark 2026",
    ])
    make("Screenshot_code_editor.png", [
        "replit — my-project",
        "Files: main.py, utils.py",
        "Code editor: def main():",
        "    import os",
        "terminal: $ python main.py",
        "Console output: Hello",
    ])
    # v0.5: mock for the new Product Photo subcategory (beyond-vision
    # folder test). Renders the product-photo keywords as visible text so
    # OCR picks them up, mirroring the real vision descriptions
    # ("product mockup ... leggings ... plain white background"). The
    # logo line mirrors the real files too: they carry a small logo and
    # still land Product Photo, not Brand Assets.
    make("IMG_0103_product.png", [
        "PRODUCT MOCKUP — LEGGINGS",
        "High-waisted leggings, sports bra set",
        "Plain white background",
        "Crescent logo on waistband",
    ])
    print("Mock samples written to", OUT)


if __name__ == "__main__":
    main()
