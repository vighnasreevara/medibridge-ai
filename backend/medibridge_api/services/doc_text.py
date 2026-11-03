"""PDF text extraction and clinical-section-aware chunking.

Chunks keep character offsets into the extracted text and page numbers, so every citation can point to the
exact passage. Section headings common in clinical reports start a new chunk; long sections are split at
sentence boundaries with a small overlap.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

SECTION_HEADINGS = (
    "findings", "impression", "diagnosis", "assessment", "plan", "assessment and plan", "history", "allergies",
    "medications", "comment", "clinical summary", "reason for referral", "request", "note", "contact",
    "results", "conclusion", "recommendation", "recommendations",
)
HEADING_RE = re.compile(r"^(?:" + "|".join(re.escape(h) for h in SECTION_HEADINGS) + r")\s*:?\s*$", re.I | re.M)
MAX_CHARS = 900
OVERLAP = 120
BANNER = "SYNTHETIC TEST RECORD - FICTIONAL PATIENT - NOT FOR CLINICAL USE"


class ExtractionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Extracted:
    text: str
    page_count: int
    page_starts: list[int]  # char offset where each page begins


@dataclass
class Chunk:
    index: int
    section: str
    page_from: int
    page_to: int
    char_start: int
    char_end: int
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def extract_pdf(data: bytes) -> Extracted:
    if not data.startswith(b"%PDF"):
        raise ExtractionError("not_a_pdf")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("encrypted_pdf")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except ExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise ExtractionError(f"unreadable_pdf:{type(exc).__name__}") from exc
    except Exception as exc:  # noqa: BLE001 - malformed files raise many types inside pypdf
        raise ExtractionError(f"unreadable_pdf:{type(exc).__name__}") from exc
    text, starts = "", []
    for page in pages:
        starts.append(len(text))
        text += page.replace(BANNER, "").strip() + "\n\n"
    if len(text.strip()) < 20:
        raise ExtractionError("no_text_layer")  # scanned image without OCR: report it, never guess
    return Extracted(text=text, page_count=len(pages), page_starts=starts)


def _page_of(offset: int, starts: list[int]) -> int:
    page = 1
    for i, s in enumerate(starts, 1):
        if offset >= s:
            page = i
    return page


def chunk(ex: Extracted) -> list[Chunk]:
    text = ex.text
    bounds = [m.start() for m in HEADING_RE.finditer(text)]
    sections: list[tuple[int, int, str]] = []
    starts = [0, *bounds]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        if end - start < 1:
            continue
        head = HEADING_RE.match(text, start)
        name = head.group(0).strip(" :\n").title() if head else "Header"
        sections.append((start, end, name))
    chunks: list[Chunk] = []
    for start, end, name in sections:
        pos = start
        while pos < end:
            stop = min(pos + MAX_CHARS, end)
            if stop < end:
                cut = text.rfind(". ", pos + MAX_CHARS // 2, stop)
                stop = cut + 1 if cut != -1 else stop
            piece = text[pos:stop].strip()
            if len(piece) >= 15:
                chunks.append(Chunk(len(chunks), name, _page_of(pos, ex.page_starts), _page_of(stop - 1, ex.page_starts),
                                    pos, stop, piece))
            if stop >= end:
                break
            pos = max(stop - OVERLAP, pos + 1)
    return chunks
