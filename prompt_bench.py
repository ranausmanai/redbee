#!/usr/bin/env python3
"""Benchmark for prompt evolution. Tests a data-extraction prompt against messy real-world text samples."""

import json
import os
import subprocess
import sys

# --- Test cases: messy real-world text → expected extracted fields ---

TESTS = [
    {
        "input": """
Hey John, thanks for the call yesterday. Just confirming — we'll invoice
Acme Corp $4,250.00 for the consulting work done in February. Please send
payment by March 15, 2025 to our account. My email is sarah@designstudio.io
and phone is (415) 555-0192.

Cheers,
Sarah Mitchell
Design Studio LLC
742 Elm Street, Suite 3B, San Francisco, CA 94102
""",
        "expected": {
            "name": "Sarah Mitchell",
            "email": "sarah@designstudio.io",
            "phone": "(415) 555-0192",
            "date": "March 15, 2025",
            "amount": 4250.00,
            "currency": "USD",
            "items": "consulting work",
            "company": "Design Studio LLC",
            "address": "742 Elm Street, Suite 3B, San Francisco, CA 94102"
        }
    },
    {
        "input": """
RECEIPT #8834
Taqueria El Sol — 2891 Mission St, SF
03/07/25 7:42PM

2x Burrito Supreme     $14.50
1x Horchata             $3.75
1x Chips & Guac         $5.25
Subtotal               $23.50
Tax                     $2.12
TOTAL                  $25.62

Card ending 4421
Thank you! Come again!
""",
        "expected": {
            "name": None,
            "email": None,
            "phone": None,
            "date": "03/07/25",
            "amount": 25.62,
            "currency": "USD",
            "items": ["Burrito Supreme", "Horchata", "Chips & Guac"],
            "company": "Taqueria El Sol",
            "address": "2891 Mission St, SF"
        }
    },
    {
        "input": """
Von: Klaus Bremer <k.bremer@autohaus-bremer.de>
Datum: 12. Januar 2025
Betreff: Rechnung KFZ-Reparatur

Sehr geehrter Herr Tanaka,

anbei die Rechnung für die Reparatur Ihres Fahrzeugs:
- Bremsbeläge wechseln: 180,00 €
- Ölwechsel: 89,50 €
- Diagnose: 45,00 €
Gesamtbetrag: 314,50 €

Bitte überweisen Sie bis zum 26.01.2025.

Tel: +49 30 5557 2234

Mit freundlichen Grüßen,
Klaus Bremer
Autohaus Bremer GmbH
Berliner Str. 44, 10715 Berlin
""",
        "expected": {
            "name": "Klaus Bremer",
            "email": "k.bremer@autohaus-bremer.de",
            "phone": "+49 30 5557 2234",
            "date": "26.01.2025",
            "amount": 314.50,
            "currency": "EUR",
            "items": ["Bremsbeläge wechseln", "Ölwechsel", "Diagnose"],
            "company": "Autohaus Bremer GmbH",
            "address": "Berliner Str. 44, 10715 Berlin"
        }
    },
    {
        "input": """
Subject: Your Subscription Confirmation
From: noreply@streamflix.com

Hi Alex!

You're all set. Here's your plan:

Plan: Premium Annual
Amount: $149.99/year
Next billing date: February 1, 2026
Account email: alex.rivera92@gmail.com

If you have questions, call us at 1-800-FLIX-NOW (1-800-354-9669).

StreamFlix Inc.
100 Content Ave, Los Angeles, CA 90001
""",
        "expected": {
            "name": "Alex",
            "email": "alex.rivera92@gmail.com",
            "phone": "1-800-354-9669",
            "date": "February 1, 2026",
            "amount": 149.99,
            "currency": "USD",
            "items": "Premium Annual",
            "company": "StreamFlix Inc.",
            "address": "100 Content Ave, Los Angeles, CA 90001"
        }
    },
    {
        "input": """
hey can u send me the $$ for last nights dinner? it was like 83 bucks
total, we split it 3 ways so u owe me 27.67. my venmo is @tina-chen-99.
thx!! oh and remind me tmrw about the dentist appt on the 20th
""",
        "expected": {
            "name": "tina-chen-99",
            "email": None,
            "phone": None,
            "date": "the 20th",
            "amount": 27.67,
            "currency": "USD",
            "items": "dinner",
            "company": None,
            "address": None
        }
    }
]

# --- Scoring logic ---

def normalize(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, list):
        return [str(v).lower().strip() for v in val]
    return str(val).lower().strip()

def field_score(extracted, expected, field):
    """Score a single field. Returns 0.0 to 1.0."""
    got = extracted.get(field)
    want = expected.get(field)

    got_n = normalize(got)
    want_n = normalize(want)

    # both null = correct
    if want_n is None and got_n is None:
        return 1.0
    # expected null but got something = hallucination penalty
    if want_n is None and got_n is not None:
        return 0.0
    # expected value but got null = miss
    if want_n is not None and got_n is None:
        return 0.0

    # amount: check numeric closeness
    if field == "amount":
        try:
            return 1.0 if abs(float(got) - float(want)) < 0.02 else 0.0
        except (ValueError, TypeError):
            return 0.0

    # list fields: check overlap
    if isinstance(want_n, list):
        if isinstance(got_n, str):
            got_n = [got_n]
        if not isinstance(got_n, list):
            return 0.0
        matches = sum(1 for w in want_n if any(w in g for g in got_n))
        return matches / len(want_n) if want_n else 1.0

    # string containment (flexible matching)
    if isinstance(want_n, str) and isinstance(got_n, str):
        if want_n == got_n:
            return 1.0
        if want_n in got_n or got_n in want_n:
            return 0.8
        return 0.0

    return 0.0

def score_extraction(extracted, expected):
    """Score an extraction result against expected. Returns 0-10."""
    fields = ["name", "email", "phone", "date", "amount", "currency", "items", "company", "address"]
    scores = [field_score(extracted, expected, f) for f in fields]
    return (sum(scores) / len(scores)) * 10

def run_prompt_against_text(prompt_text, input_text):
    """Send prompt + input to codex, get JSON back."""
    full_prompt = f"{prompt_text}\n\nTEXT:\n{input_text}"
    try:
        result = subprocess.run(
            ["codex", "exec", full_prompt],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        # try to extract JSON
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find('{')
            end = raw.rfind('}') + 1
            if start != -1 and end > start:
                return json.loads(raw[start:end])
            return None
    except Exception:
        return None

def main():
    prompt_path = sys.argv[1]
    prompt_text = open(prompt_path).read().strip()

    total_score = 0
    n_tests = len(TESTS)

    for i, test in enumerate(TESTS):
        extracted = run_prompt_against_text(prompt_text, test["input"])
        if extracted is None:
            label = "FAIL (no JSON)"
            sc = 0
        else:
            sc = score_extraction(extracted, test["expected"])
            label = f"{sc:.1f}/10"
        total_score += sc

        names = ["invoice_email", "receipt", "german_invoice", "subscription", "casual_text"]
        print(f"TEST {names[i]}: {label}")

    avg = total_score / n_tests
    print(f"AVERAGE: {avg:.1f}/10")

if __name__ == "__main__":
    main()
