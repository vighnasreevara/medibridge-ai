"""Document text extraction, chunking, identifier redaction and retrieval re-validation."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from medibridge_api.guardrails import phi
from medibridge_api.models import DocumentChunk, DocumentVersion, MedicalDocument
from medibridge_api.services import doc_text, retrieval, vector_index
from medibridge_seed import pdfs

PATIENT = {"name": "Testa Synthetic", "mrn": "MB999001", "dob": date(1980, 1, 1)}


@pytest.mark.parametrize("text,kind", [
    ("Call +1-555-0110 today", "phone"), ("Call (555) 123-4567 today", "phone"),
    ("Mail amara.x@example.test now", "email"), ("Lives at 14 Heron Lane, Fictionville ZZ 00000", "address"),
    ("Member SYN-INS-448812", "insurance"), ("SSN 123-45-6789", "national_id"),
])
def test_identifiers_are_removed(text, kind):
    result = phi.scan(text)
    assert kind in {f["kind"] for f in result.findings}
    assert not phi.contains_identifiers(result.text)


@pytest.mark.parametrize("text", [
    "INR 2.6 within target 2.0-3.0.", "Potassium 4.1 mmol/L (3.5 - 5.1).", "Ejection fraction 55-60%.",
    "Acetaminophen 500-1000 mg every 6 hours.", "MRN MB100237, seen 2026-04-03.", "BP 132/84 mmHg, HR 72.",
])
def test_clinical_values_are_kept(text):
    assert phi.scan(text).text == text


def test_findings_never_contain_values():
    result = phi.scan("Phone +1-555-0110 and email a@example.test")
    assert "555" not in str(result.findings) and "example" not in str(result.findings)


def test_pdf_extraction_and_chunk_offsets():
    data = pdfs.narrative_report(PATIENT, date(2026, 1, 2), "Imaging report - Chest X-ray", "Radiology", "Dr. Test",
                                 [("Findings", "No focal consolidation. " * 60), ("Impression", "No acute abnormality.")])
    ex = doc_text.extract_pdf(data)
    chunks = doc_text.chunk(ex)
    assert {"Findings", "Impression"} <= {c.section for c in chunks}
    for c in chunks:
        assert c.text in ex.text[c.char_start:c.char_end]
    assert all(len(c.text) <= doc_text.MAX_CHARS for c in chunks)
    assert "SYNTHETIC TEST RECORD" not in ex.text  # page banner stripped from the text layer


@pytest.mark.parametrize("data,code", [(b"not a pdf", "not_a_pdf"), (b"%PDF-1.4\n%truncated\n", "unreadable_pdf")])
def test_unreadable_documents_fail_with_a_typed_error(data, code):
    with pytest.raises(doc_text.ExtractionError) as exc:
        doc_text.extract_pdf(data)
    assert exc.value.code.startswith(code)


def test_search_requires_a_server_resolved_patient_uuid():
    with pytest.raises(TypeError):
        vector_index.search(patient_id="any", query="x", query_vector=[0.0])  # type: ignore[arg-type]


def test_retrieval_drops_chunks_from_other_patients(db, patient_factory, monkeypatch):
    me, other = patient_factory(), patient_factory()
    doc = MedicalDocument(id=uuid.uuid4(), patient_id=other.id, document_type="lab_report", title="Other patient report")
    db.add(doc)
    db.flush()
    v = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, version=1, storage_key="x/x.pdf", content_sha256="0" * 64,
                        mime_type="application/pdf", size_bytes=1, ingestion_status="indexed")
    db.add(v)
    db.flush()
    chunk = DocumentChunk(id=uuid.uuid4(), document_version_id=v.id, patient_id=other.id, chunk_index=0,
                          char_start=0, char_end=10, text="secret finding", text_sha256="0" * 64)
    db.add(chunk)
    db.commit()

    class FakeLLM:
        def get_embeddings(self, texts):
            return [[0.0]], None

    # Pretend the index is corrupted and returns the other patient's chunk with forged metadata.
    monkeypatch.setattr(retrieval, "coverage", lambda db, pid: {"documents_on_file": 1, "indexed": 1, "failed": [],
                                                                "not_yet_indexed": 0})
    monkeypatch.setattr(retrieval, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(vector_index, "search",
                        lambda **kw: [vector_index.Hit(str(chunk.id), 0.9, {"patient_id": str(me.id)})])
    res = retrieval.retrieve(db, patient_id=me.id, query="finding")
    assert res.evidence == []
    assert res.dropped == {"other_patient": 1}


# ------------------------------------------------------------------ hybrid retrieval: BM25 + dense, RRF, re-rank

def test_medical_tokenisation_expands_cardiology_abbreviations():
    from medibridge_api.services import lexical

    toks = lexical.tokens("LVEF 35% after PCI; NT-proBNP 2400")
    assert {"ejection", "fraction", "percutaneou", "coronary", "35", "2400", "ntprobnp", "natriuretic"} <= set(toks)


def test_bm25_ranks_the_matching_report_first():
    from medibridge_api.services import lexical

    bm = lexical.BM25({"echo": "Transthoracic echocardiogram. Left ventricular ejection fraction 35 %.",
                       "lipids": "Lipid panel. LDL cholesterol 142 mg/dL.", "inr": "Coagulation. INR 3.4."})
    assert bm.search("echo EF")[0].key == "echo"
    assert bm.search("warfarin INR monitoring")[0].key == "inr"
    assert bm.search("nothing in common xyz") == []


def test_reciprocal_rank_fusion_rewards_agreement():
    from medibridge_api.services import lexical

    fused = [k for k, _ in lexical.rrf(["a", "b", "c"], ["c", "a"], ["a"])]
    assert fused[0] == "a" and fused.index("c") < fused.index("b")


def _chunk(db, patient, text: str, title: str = "Report") -> DocumentChunk:
    doc = MedicalDocument(id=uuid.uuid4(), patient_id=patient.id, document_type="lab_report", title=title)
    db.add(doc)
    db.flush()
    v = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, version=1, storage_key=f"x/{doc.id}.pdf",
                        content_sha256="0" * 64, mime_type="application/pdf", size_bytes=1, ingestion_status="indexed")
    db.add(v)
    db.flush()
    c = DocumentChunk(id=uuid.uuid4(), document_version_id=v.id, patient_id=patient.id, chunk_index=0, char_start=0,
                      char_end=len(text), text=text, text_sha256="0" * 64)
    db.add(c)
    db.commit()
    return c


def test_retrieval_continues_lexically_when_the_dense_index_is_down(db, patient_factory, monkeypatch):
    me = patient_factory()
    echo = _chunk(db, me, "Transthoracic echocardiogram. Left ventricular ejection fraction 35 %.", "Echo")
    _chunk(db, me, "Lipid panel. LDL cholesterol 142 mg/dL.", "Lipids")

    def down(**kw):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(vector_index, "search", down)
    monkeypatch.setattr(retrieval, "get_llm", lambda: type("L", (), {"get_embeddings": lambda self, t: ([[0.0]], None)})())
    res = retrieval.retrieve(db, patient_id=me.id, query="ejection fraction")
    assert res.mode == "lexical_only"
    assert res.evidence[0].document_id == str(db.get(DocumentVersion, echo.document_version_id).document_id)
    assert res.evidence[0].ranks["bm25"] == 1 and res.evidence[0].ranks["dense"] is None


def test_hybrid_ranks_record_each_ranker(db, patient_factory, monkeypatch):
    me = patient_factory()
    echo = _chunk(db, me, "Transthoracic echocardiogram. Left ventricular ejection fraction 35 %.", "Echo")
    lipids = _chunk(db, me, "Lipid panel. LDL cholesterol 142 mg/dL.", "Lipids")
    monkeypatch.setattr(retrieval, "get_llm", lambda: type("L", (), {"get_embeddings": lambda self, t: ([[0.0]], None)})())
    monkeypatch.setattr(vector_index, "search", lambda **kw: [vector_index.Hit(str(lipids.id), 0.8, {}),
                                                              vector_index.Hit(str(echo.id), 0.7, {})])
    res = retrieval.retrieve(db, patient_id=me.id, query="ejection fraction")
    assert res.mode == "hybrid"
    by_doc = {e.title: e.ranks for e in res.evidence}
    assert by_doc["Echo"]["bm25"] == 1 and by_doc["Echo"]["dense"] == 2 and by_doc["Lipids"]["dense"] == 1
    assert res.evidence[0].title == "Echo"  # lexical + dense agreement beats a single dense first place
