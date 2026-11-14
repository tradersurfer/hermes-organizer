"""Taxonomy ruleset for Hermes Organizer v0.1.

Categories, subcategories, weighted keyword signals, and target folder map.
Mirrors taxonomy_rules.json (the JSON is the human-readable ruleset; this
module is the runtime copy used by the classifier).
"""
from __future__ import annotations

CONFIDENCE_THRESHOLD = 0.60  # below this -> Requires_Human_Review

# subcategory -> (category, folder template, {keyword: weight})
# Folder template uses {year} from the file date.
RULES: dict[str, dict] = {
    "Receipt": {
        "category": "Financial",
        "folder": "/Finance/Receipts/{year}/",
        "keywords": {
            "receipt": 3, "total": 2, "subtotal": 2, "tax": 1, "paid": 1,
            "purchase": 2, "store": 1, "cashier": 2, "change due": 2,
            "amount due": 2, "thank you": 1, "visa": 1, "mastercard": 1,
            "amex": 1, "card ending": 2, "item": 1, "qty": 2,
        },
    },
    "Invoice": {
        "category": "Financial",
        "folder": "/Finance/Invoices/{year}/",
        "keywords": {
            "invoice": 3, "invoice #": 3, "inv #": 2, "bill to": 2,
            "due date": 2, "balance due": 2, "payment terms": 2,
            "amount due": 2, "net 30": 2, "net 15": 2, "purchase order": 2,
            "remit": 2, "billing": 1,
        },
    },
    "Tax Document": {
        "category": "Financial",
        "folder": "/Finance/Tax Documents/{year}/",
        "keywords": {
            "w-2": 3, "1099": 3, "1040": 3, "tax return": 3, "irs": 2,
            "federal": 1, "withholding": 2, "deduction": 2, "ein": 2,
            "schedule c": 3, "taxable": 2, "refund": 1,
        },
    },
    "Bank Statement": {
        "category": "Financial",
        "folder": "/Finance/Bank Statements/{year}/",
        "keywords": {
            "statement": 2, "account summary": 3, "beginning balance": 3,
            "ending balance": 3, "account number": 2, "routing": 2,
            "deposits": 2, "withdrawals": 2, "available balance": 2,
            "statement period": 3, "checking": 1, "savings": 1,
        },
    },
    "Credit Report": {
        "category": "Financial",
        "folder": "/Finance/Credit Reports/{year}/",
        "keywords": {
            "credit score": 3, "credit report": 3, "equifax": 2,
            "transunion": 2, "experian": 2, "fico": 3, "credithero": 2,
            "credit bureau": 2, "credit history": 2,
        },
    },
    "Crypto Transaction": {
        "category": "Financial",
        "folder": "/Finance/Crypto Transactions/{year}/",
        "keywords": {
            "crypto": 3, "bitcoin": 3, "btc": 2, "order filled": 3,
            "cryptocurrency": 2, "ethereum": 2, "wallet": 2, "exchange": 2,
            "coinbase": 3, "cash app": 2, "satoshi": 2, "blockchain": 2,
            "average price": 2,
        },
    },
    "Contract": {
        "category": "Legal & Personal Identity",
        "folder": "/Legal/Contracts/{year}/",
        "keywords": {
            "contract": 3, "agreement": 3, "parties agree": 3, "signature": 2,
            "hereby": 2, "terms and conditions": 3, "whereas": 2,
            "non-disclosure": 3, "nda": 2, "lease agreement": 3,
            "effective date": 2, "governing law": 2,
        },
    },
    "ID Documentation": {
        "category": "Legal & Personal Identity",
        "folder": "/Legal/ID Documentation/{year}/",
        "keywords": {
            "driver": 1, "license": 2, "passport": 3, "date of birth": 2,
            "identification": 2, "id number": 2, "expires": 1,
            "department of motor": 3, "social security": 3,
        },
    },
    "Property Record": {
        "category": "Legal & Personal Identity",
        "folder": "/Legal/Property Records/{year}/",
        "keywords": {
            "deed": 3, "property": 2, "mortgage": 3, "parcel": 3,
            "lot": 1, "title": 1, "closing": 2, "escrow": 2,
            "homeowner": 2, "assessment": 2,
        },
    },
    "Architecture Diagram": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Architecture Diagrams/{year}/",
        "keywords": {
            "architecture": 3, "diagram": 2, "system": 1, "database": 1,
            "api": 1, "server": 1, "flow": 1, "component": 2,
            "microservice": 3, "infrastructure": 2,
        },
    },
    "Whiteboard Brainstorm": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Whiteboard Brainstorms/{year}/",
        "keywords": {
            "whiteboard": 3, "brainstorm": 3, "idea": 1, "sketch": 2,
            "roadmap": 2, "sticky": 2, "kanban": 2, "sprint": 2,
        },
    },
    "Code Screenshot": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Code Screenshots/{year}/",
        "keywords": {
            "def ": 2, "function": 1, "import ": 2, "class ": 1,
            "return": 1, "const ": 2, "terminal": 2, "console": 1,
            "error": 1, "traceback": 3, "git": 1, "python": 1,
            "javascript": 1, "stack trace": 3,
            # v0.4: IDE / hosted-editor screenshots (Replit) — "replit"
            # matches inside "replit.com" (the "." after it is a word
            # boundary); "tsx"/"jsx"/"src" match after the "." in ".tsx".
            "replit": 3, "code editor": 3, "ide": 2, "editor": 2,
            "github": 2, "vscode": 2, "visual studio": 2,
            "workspace": 2, "repository": 2, "tsx": 2, "jsx": 2,
            "src": 2, "shell": 2,
        },
    },
    "Domain Research": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Domain Research/{year}/",
        "keywords": {
            "domain": 3, "domain search": 3, "registrar": 3,
            "suggested results": 2, "renew": 2, "per year": 2,
            "yr": 1, "year": 1, "premium": 1, "ssl": 1, "dns": 2,
            "whois": 2, "add to cart": 2, "retail": 1,
        },
    },
    "Brand Assets": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Brand Assets/{year}/",
        "keywords": {
            "logo": 3, "brand": 2, "wordmark": 3, "emblem": 2,
            "logotype": 3, "brand mark": 2, "trademark": 2,
        },
    },
    "Product Photo": {
        "category": "Technical & Work Assets",
        "folder": "/Work/Product Photos/{year}/",
        # v0.5: apparel / product photography (beyond-vision folder test).
        # Vision describes these files well ("product mockup ... leggings
        # ... plain white background") but no subcategory existed for the
        # description to land in — 0/18 classified in the v0.4 folder test.
        # Weights verified against the 5 real vision texts in
        # sample_reports/beyond-vision-scan.json: a typical mockup scores
        # 13-16 (0.81-0.84), the t-shirt-only file scores 5 (0.625), the
        # "mock-up" spelling scores 9 (0.75). "plain white background" is
        # deliberately NOT a keyword: "white background" already matches
        # inside it as a substring, and adding both would double-count one
        # span and let a bare logo on a white background (4 pts) outscore
        # Brand Assets (3 pts). "print" (5 chars) and "dress" (4 chars)
        # are word-boundary keywords: "print" must not match "blueprint",
        # "dress" must not match "address".
        "keywords": {
            "product": 3, "product photo": 3, "product image": 3,
            "mockup": 3, "mock-up": 3, "leggings": 2, "garment": 2,
            "apparel": 2, "photograph": 2, "isolated against": 2,
            "white background": 2, "sports bra": 2, "waistband": 2,
            "t-shirt": 2, "clothing": 2, "fashion": 2, "outfit": 2,
            "camouflage": 1, "print": 1, "pattern": 1, "dress": 1,
        },
    },
}

# Filename signals: (regex-ish substring, subcategory boost, weight)
FILENAME_SIGNALS: list[tuple[str, str, float]] = [
    ("receipt", "Receipt", 2.0),
    ("invoice", "Invoice", 2.0),
    ("inv_", "Invoice", 1.5),
    ("1099", "Tax Document", 2.0),
    ("w-2", "Tax Document", 2.0),
    ("w2", "Tax Document", 1.5),
    ("1040", "Tax Document", 2.0),
    ("statement", "Bank Statement", 2.0),
    ("contract", "Contract", 2.0),
    ("agreement", "Contract", 1.5),
    ("nda", "Contract", 1.5),
    ("passport", "ID Documentation", 2.0),
    ("license", "ID Documentation", 1.0),
    ("deed", "Property Record", 2.0),
    ("mortgage", "Property Record", 2.0),
    ("diagram", "Architecture Diagram", 2.0),
    ("whiteboard", "Whiteboard Brainstorm", 2.0),
    ("screenshot", "Code Screenshot", 0.5),
    ("screen shot", "Code Screenshot", 0.5),
    ("crypto", "Crypto Transaction", 2.0),
    ("btc", "Crypto Transaction", 2.0),
    ("domain", "Domain Research", 2.0),
    ("logo", "Brand Assets", 2.0),
    ("replit", "Code Screenshot", 2.0),
    # v0.5: a filename that says "product" is a product-photo hint.
    # No signal for "attachment" — every file in the beyond-vision folder
    # is named that, so it carries no information.
    ("product", "Product Photo", 1.5),
]

UNORGANIZED_PATTERNS = ("img_", "screenshot", "scan_", "image", "photo", "dsc_", "pxl_")

REVIEW_FOLDER = "/Requires_Human_Review/"
