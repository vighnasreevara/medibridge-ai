"""Instruction-like text inside clinical content (guardrail 8).

Content is always treated as data; this detector only makes the attempt visible (pre-generation checks, audit),
it never decides what a model may do.
"""
from __future__ import annotations

import re

INSTRUCTION_RE = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above) instructions|disregard (all |the )?(previous|prior|above)|"
    r"^\s*(system|assistant)\s*:|approve (the|this) (handoff|brief|note|order)|you are now|new instructions\s*:",
    re.I | re.M)


def looks_like_instruction(text: str) -> bool:
    return bool(INSTRUCTION_RE.search(text or ""))
