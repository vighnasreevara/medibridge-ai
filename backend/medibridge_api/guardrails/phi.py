"""Deterministic identifier scanner/redactor (guardrail 3).

Runs before document text is displayed, exported, indexed for display, or placed in a handoff. It removes
identifiers a clinical handoff does not need: phone numbers, email addresses, street addresses, government and
insurance identifiers, SSN-like numbers and URLs with query strings. Clinically relevant content, the patient's
display name, MRN and dates are kept (the handoff is a *named* clinical document, not an anonymized one).
Findings report kinds and counts only, never the matched values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STREET_SUFFIX = (r"(?:street|st|road|rd|lane|ln|avenue|ave|drive|dr|boulevard|blvd|court|ct|way|place|pl|terrace|"
                 r"close|crescent|parkway|pkwy|highway|hwy)")

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("insurance", re.compile(r"\b(?:SYN-INS-\d{4,}|(?:member|policy|insurance)\s*(?:id|no\.?|number|#)?\s*[:#]?\s*(?=[A-Z0-9-]*\d)[A-Z0-9-]{6,})\b", re.I)),
    ("national_id", re.compile(r"\bSYN-NID-[\d-]{4,}\b|\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<![\w-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b|\+1-555-\d{4}\b")),
    ("address", re.compile(rf"\b\d{{1,5}}\s+(?:[A-Z][a-z]+\s+){{1,3}}{STREET_SUFFIX}\b\.?(?:,?\s+[A-Z][a-z]+)?(?:,?\s+[A-Z]{{2}}\s+\d{{5}})?",
                           re.I)),
    ("postal_code", re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")),
    ("url_query", re.compile(r"https?://\S+\?\S+")),
]

LABELS = {"email": "[EMAIL REMOVED]", "insurance": "[INSURANCE ID REMOVED]", "national_id": "[GOVERNMENT ID REMOVED]",
          "phone": "[PHONE REMOVED]", "address": "[ADDRESS REMOVED]", "postal_code": "[POSTCODE REMOVED]",
          "url_query": "[LINK REMOVED]"}


@dataclass
class ScanResult:
    text: str
    findings: list[dict]  # [{"kind": str, "count": int}]

    @property
    def found(self) -> bool:
        return bool(self.findings)


def scan(text: str) -> ScanResult:
    counts: dict[str, int] = {}
    out = text
    for kind, pattern in PATTERNS:
        out, n = pattern.subn(LABELS[kind], out)
        if n:
            counts[kind] = counts.get(kind, 0) + n
    return ScanResult(out, [{"kind": k, "count": v} for k, v in counts.items()])


def contains_identifiers(text: str) -> list[str]:
    """Kinds of identifiers present (used as a final assertion on outgoing handoff/export payloads)."""
    return [kind for kind, pattern in PATTERNS if pattern.search(text)]
