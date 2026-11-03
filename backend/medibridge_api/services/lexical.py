"""Okapi BM25 over one patient's document chunks, with medical tokenisation.

The lexical half of hybrid retrieval. The corpus is the patient's current, active chunks read from Postgres at
query time (a patient has tens of chunks, so an exact in-memory BM25 is cheap and never stale).

Medical tokenisation: lower-case; numbers kept with their decimals ("0.125", "5.1"); hyphenated terms split and
kept whole ("nt-probnp" -> "nt", "probnp", "ntprobnp"); common cardiology abbreviations expanded to the words
reports use (MI, AF, HF, LVEF, PCI, CABG, BNP, INR ...) so a query for "AF" finds "atrial fibrillation" and vice
versa; lay phrases mapped to the clinical terms reports use (consumer-health vocabulary: "blood sugar" -> HbA1c,
glucose; "heart muscle injury" -> troponin; "how well the heart pumps" -> ejection fraction ...); a light suffix
strip so "palpitations" matches "palpitation". The same rules apply to reports and queries.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
STOP = set("the a an and or of to in on for with without is was were be been are at by from as this that it its "
           "patient report reported per no not any".split())
ABBREVIATIONS = {
    "mi": "myocardial infarction", "stemi": "st elevation myocardial infarction",
    "nstemi": "non st elevation myocardial infarction", "af": "atrial fibrillation", "afib": "atrial fibrillation",
    "hf": "heart failure", "chf": "congestive heart failure", "hfref": "heart failure reduced ejection fraction",
    "ef": "ejection fraction", "lvef": "left ventricular ejection fraction", "lv": "left ventricular",
    "pci": "percutaneous coronary intervention", "cabg": "coronary artery bypass grafting",
    "cad": "coronary artery disease", "ihd": "ischemic heart disease", "acs": "acute coronary syndrome",
    "ecg": "electrocardiogram", "ekg": "electrocardiogram", "echo": "echocardiogram", "tte": "transthoracic echocardiogram",
    "bnp": "natriuretic peptide", "probnp": "natriuretic peptide", "ntprobnp": "natriuretic peptide",
    "inr": "international normalized ratio inr", "ldl": "ldl cholesterol", "hdl": "hdl cholesterol",
    "egfr": "glomerular filtration rate", "bp": "blood pressure", "hr": "heart rate", "hba1c": "hemoglobin a1c",
    "a1c": "hemoglobin a1c", "gtn": "nitroglycerin", "ntg": "nitroglycerin", "k": "potassium",
    "tavi": "transcatheter aortic valve", "doac": "anticoagulant", "noac": "anticoagulant",
}
# Lay phrase -> clinical terms (matched as whole words, before tokenising).
LAY_TERMS = {
    "blood sugar": "glucose hba1c a1c", "sugar control": "hba1c glucose", "diabetes control": "hba1c",
    "heart muscle": "troponin cardiac biomarker", "heart attack": "myocardial infarction troponin",
    "heart damage": "troponin", "chest pain marker": "troponin", "injury marker": "troponin biomarker",
    "heart strain": "natriuretic peptide", "heart failure marker": "natriuretic peptide",
    "pumping": "ejection fraction echocardiogram", "pumps": "ejection fraction echocardiogram",
    "heart function": "ejection fraction echocardiogram", "kidney": "renal creatinine glomerular",
    "kidneys": "renal creatinine glomerular", "blood thinner": "anticoagulant inr coagulation",
    "anticoagulation": "anticoagulant inr coagulation", "clotting": "inr coagulation prothrombin",
    "bleeding risk": "inr coagulation", "cholesterol": "lipid ldl hdl", "stent": "percutaneous coronary intervention",
    "bypass": "coronary artery bypass grafting", "hospital admission": "discharge inpatient admission",
    "hospital stay": "discharge inpatient admission", "anaemia": "hemoglobin", "anemia": "hemoglobin",
    "blood count": "hemoglobin", "salt": "sodium", "water tablet": "diuretic", "water pill": "diuretic",
}
_LAY = [(re.compile(rf"\b{re.escape(k)}\b"), v) for k, v in LAY_TERMS.items()]
SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "es", "s")


def _stem(word: str) -> str:
    if word[0].isdigit() or len(word) <= 4:
        return word
    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)] + ("y" if suf == "ies" else "")
    return word


def tokens(text: str) -> list[str]:
    out: list[str] = []
    low = text.lower()
    low += "".join(f" {terms}" for pattern, terms in _LAY if pattern.search(low))
    for raw in TOKEN.findall(low):
        parts = [raw] if "-" not in raw else [*raw.split("-"), raw.replace("-", "")]
        for part in parts:
            if not part or part in STOP:
                continue
            out.append(_stem(part))
            expansion = ABBREVIATIONS.get(part)
            if expansion:
                out += [_stem(w) for w in expansion.split() if w not in STOP]
    return out


@dataclass
class Scored:
    key: str
    score: float


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) with the usual non-negative IDF."""

    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {key: Counter(tokens(text)) for key, text in docs.items()}
        self.len = {key: sum(c.values()) for key, c in self.tf.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        n = len(self.tf)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, limit: int = 20) -> list[Scored]:
        q = Counter(tokens(query))
        scores = []
        for key, tf in self.tf.items():
            s = 0.0
            for term in q:
                f = tf.get(term)
                if f:
                    s += self.idf[term] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[key] / (self.avg or 1)))
            if s > 0:
                scores.append(Scored(key, s))
        return sorted(scores, key=lambda x: -x.score)[:limit]


def rrf(*rankings: list[str], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: sum of 1 / (k + rank) over the rankings each key appears in."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])
