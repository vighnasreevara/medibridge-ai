"""NegEx-style negation and uncertainty detection (Chapman et al. 2001 approach, simplified).

Used to cross-check LLM extraction: if the source sentence negates a finding but the model did not (or vice
versa), the extraction is rejected and retried with the problem stated.
"""
from __future__ import annotations

import re

PRE_NEGATION = ("no", "not", "denies", "denied", "deny", "without", "negative for", "free of", "never had",
                "do not have", "don't have", "does not have", "has not had", "haven't had", "have not had",
                "no history of", "absence of", "there is no", "there are no", "i do not have", "i don't have",
                "i have no", "nor", "never", "haven't", "hasn't", "didn't", "did not", "have not", "has not")
POST_NEGATION = ("is absent", "are absent", "ruled out", "has resolved", "not present")
TERMINATION = ("but", "however", "although", "except", "apart from", "though", "aside from")
UNCERTAINTY = ("i think", "maybe", "possibly", "not sure", "might", "probably", "i guess", "something for",
               "-something", " something", "can't remember", "cannot remember", "don't remember", "i believe",
               "not certain", "perhaps")
WINDOW = 6  # tokens

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+|[-]", text.lower())


def is_negated(sentence: str, term: str) -> bool:
    s = " " + " ".join(_tokens(sentence)) + " "
    t = " ".join(_tokens(term))
    idx = s.find(" " + t + " ") if t else -1
    if idx == -1:
        # Fall back to the term's content words by stem, so "shortness of breath" is found in "not short of breath"
        # and "fainting" in "never fainted".
        hits = [s.find(" " + w[:5]) for w in t.split() if len(w) > 3]
        hits = [h for h in hits if h != -1]  # in the term's word order: the head word decides
        if not hits:
            return False
        idx = hits[0]
    before = s[:idx].split()[-WINDOW:]
    after = s[idx + len(t) + 1:].split()[:4]
    window = " ".join(before)
    # A termination word between trigger and term ends the negation scope.
    for term_word in TERMINATION:
        if f" {term_word} " in f" {window} ":
            window = window.split(term_word)[-1]
    if any(f" {trig} " in f" {window} " for trig in PRE_NEGATION):
        return True
    tail = " ".join(after)
    return any(tail.startswith(trig) for trig in POST_NEGATION)


def is_uncertain(text: str) -> bool:
    low = " " + text.lower()
    return any(trig in low for trig in UNCERTAINTY)


def sentence_containing(text: str, phrase: str) -> str | None:
    p = phrase.lower()
    for s in sentences(text):
        if p in s.lower():
            return s
    return None
