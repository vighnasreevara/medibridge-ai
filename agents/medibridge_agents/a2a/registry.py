"""The remote agents: who owns which skill, where it listens, and what state it may see and return.

Most skills are one graph node, called by the supervisor. ``inputs`` is a data-minimisation allow-list: the
caller sends only these state keys (plus the run identifiers, which the agent re-checks against its capability
token). ``outputs`` is what the agent may write back; anything else in a response is a protocol violation.

- ``run_scoped=False``: called by the API outside a workflow run (patient registration); only the acting user
  is bound into the capability.
- ``delegates``: peer skills this skill may call agent-to-agent; the supervisor mints one capability per peer
  call, naming the calling agent.
- ``peer_from``: the skill only accepts capabilities delegated to that agent (never a direct supervisor call).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

from medibridge_api.config import get_settings

# Identifiers every run-scoped call carries. They come from the caller's state and must equal the token's claims.
ID_KEYS = ("run_id", "encounter_id", "patient_id", "actor_user_id", "actor_role")
ACTOR_KEYS = ("actor_user_id", "actor_role")
CONTROL_OUTPUTS = ("steps", "outcome")


@dataclass(frozen=True)
class Skill:
    id: str
    agent_id: str
    node: str | None
    name: str
    description: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    degrade_key: str | None = None  # non-critical: an unreachable agent degrades instead of stopping the run
    tags: tuple[str, ...] = ()
    run_scoped: bool = True
    delegates: tuple[str, ...] = ()  # "agent/skill" peer calls this skill may make
    peer_from: str | None = None     # only callable by this agent, through a delegated capability

    @cached_property
    def fn(self) -> Callable[[dict], dict]:
        """The implementation (``agent_node``-wrapped for run-scoped skills). Only agent services call this."""
        from ..nodes import avs, context, documentation, handoff, ner, orders, registry

        return {"verify_encounter_identity": context.identity_node,
                "match_or_register_patient": registry.match_or_register,
                "transcribe_intake_audio": context.speech_node,
                "extract_clinical_entities": ner.clinical_ner_node,
                "assemble_patient_chart": context.history_node,
                "commit_encounter_record": orders.chart_commit_node,
                "retrieve_clinical_evidence": context.documents_node,
                "screen_current_medications": context.medication_node,
                "screen_proposed_orders": orders.screen_proposed_orders,
                "generate_handoff_brief": handoff.handoff_node,
                "draft_clinical_note": documentation.documentation_draft_node,
                "verify_proposed_orders": orders.order_safety_check_node,
                "generate_after_visit_summary": avs.after_visit_summary_node}[self.id]

    @property
    def allowed_outputs(self) -> set[str]:
        return {*self.outputs, *CONTROL_OUTPUTS}

    @property
    def id_keys(self) -> tuple[str, ...]:
        return ID_KEYS if self.run_scoped else ACTOR_KEYS


@dataclass(frozen=True)
class RemoteAgent:
    id: str
    label: str
    port_offset: int
    description: str
    skills: tuple[Skill, ...]

    @property
    def port(self) -> int:
        return get_settings().a2a_base_port + self.port_offset

    @property
    def url(self) -> str:
        return f"http://{get_settings().a2a_host}:{self.port}"

    def skill(self, skill_id: str) -> Skill | None:
        return next((s for s in self.skills if s.id == skill_id), None)


def _agent(agent_id: str, label: str, offset: int, description: str, *skills: dict) -> RemoteAgent:
    return RemoteAgent(agent_id, label, offset, description,
                       tuple(Skill(agent_id=agent_id, **s) for s in skills))


REMOTE_AGENTS: dict[str, RemoteAgent] = {a.id: a for a in (
    _agent("patient_registry", "Patient Registry (EMPI)", 0,
           "Master patient index: finds a returning patient by MRN and date of birth (or name and date of birth), "
           "registers a new patient with a duplicate check, and confirms identity and consent before intake.",
           {"id": "match_or_register_patient", "node": None, "name": "Match or register patient",
            "description": "Search the registry or register a new patient (duplicate-checked). Called by the API.",
            "inputs": ("registration",), "outputs": ("registry",), "run_scoped": False, "tags": ("empi",)},
           {"id": "verify_encounter_identity", "node": "registry_check", "name": "Verify identity and consent",
            "description": "Blocks the run unless identity is verified and both consents are granted.",
            "inputs": (), "outputs": ("identity",), "tags": ("identity", "consent")}),
    _agent("ambient_scribe", "Ambient Scribe", 1,
           "Ambient clinical documentation: transcribes the intake locally (faster-whisper) and extracts symptoms, "
           "medicines, allergies and conditions from the reviewed transcript with verbatim grounding.",
           {"id": "transcribe_intake_audio", "node": "scribe_transcribe", "name": "Transcribe intake audio",
            "description": "Writes raw segments for human review; flags uncertain words and misheard drug names.",
            "inputs": ("transcription_id",), "outputs": (), "tags": ("asr",)},
           {"id": "extract_clinical_entities", "node": "scribe_extract", "name": "Extract clinical entities",
            "description": "Validated findings from the reviewed transcript; ungrounded items are quarantined.",
            "inputs": ("transcript", "transcription_id"), "outputs": ("findings",), "tags": ("llm", "ner")}),
    _agent("chart", "Chart Agent (EHR)", 2,
           "The structured record (PostgreSQL): reads conditions, medicines with doses, allergies, results, "
           "procedures and visits; writes the approved encounter back in one transaction with its audit entries.",
           {"id": "assemble_patient_chart", "node": "chart_review", "name": "Assemble patient chart",
            "description": "Reads the record through the bounded tool layer, with conflicts against today's intake.",
            "inputs": ("findings",), "outputs": ("history",), "tags": ("record",)},
           {"id": "commit_encounter_record", "node": "chart_commit", "name": "Commit encounter record",
            "description": "Writes the doctor-signed, safety-screened change set and finalizes the encounter.",
            "inputs": ("documentation",), "outputs": ("documentation",), "tags": ("write",)}),
    _agent("evidence_retrieval", "Evidence Retrieval", 3,
           "Hybrid search over this patient's reports: BM25 (medical tokenisation) and Qdrant dense vectors, fused by "
           "reciprocal rank fusion and re-ranked; every hit is re-validated in Postgres.",
           {"id": "retrieve_clinical_evidence", "node": "evidence_retrieval", "name": "Retrieve clinical evidence",
            "description": "Cited evidence and coverage; never another patient's documents.",
            "inputs": ("findings", "history"), "outputs": ("documents",), "degrade_key": "documents",
            "tags": ("retrieval", "bm25", "rrf")}),
    _agent("medication_safety", "Medication Safety CDS", 4,
           "Clinical decision support over the cardiology drug knowledge graph (Neo4j, FDA/NLM sources): interactions, "
           "label dose limits, allergies and conditions. No model decides a contraindication.",
           {"id": "screen_current_medications", "node": "medication_screen", "name": "Screen current medications",
            "description": "Screens recorded (with doses) and reported medicines; states its completeness.",
            "inputs": ("findings", "history"), "outputs": ("medication_safety",), "degrade_key": "medication_safety",
            "tags": ("knowledge-graph",)},
           {"id": "screen_proposed_orders", "node": None, "name": "Screen proposed orders",
            "description": "Screens the doctor's proposed orders. Only Order Entry may call it, by delegation.",
            "inputs": ("documentation",), "outputs": ("order_screen",), "peer_from": "order_entry",
            "tags": ("knowledge-graph", "orders")}),
    _agent("handoff_brief", "Clinical Handoff Brief", 5,
           "Builds the evidence bundle and a structured cardiology brief (SBAR) with pre-generation checks; every "
           "statement cites evidence, and unsupported or identifying statements are removed.",
           {"id": "generate_handoff_brief", "node": "handoff_brief", "name": "Generate handoff brief",
            "description": "Saves a new brief version for doctor review.",
            "inputs": ("findings", "history", "documents", "medication_safety", "escalations", "review"),
            "outputs": ("handoff", "review"), "tags": ("llm", "handoff")}),
    _agent("order_entry", "Order Entry (CPOE)", 6,
           "Drafts the note from the approved brief and verifies the doctor's signed orders, asking Medication Safety "
           "to screen them before anything is written.",
           {"id": "draft_clinical_note", "node": "order_draft", "name": "Draft clinical note",
            "description": "Prepares the Subjective/Objective draft from the approved brief.",
            "inputs": (), "outputs": ("documentation",), "tags": ("llm",)},
           {"id": "verify_proposed_orders", "node": "order_safety_check", "name": "Verify proposed orders",
            "description": "Checks the signed change set and has Medication Safety screen the orders.",
            "inputs": ("documentation",), "outputs": ("documentation",),
            "delegates": ("medication_safety/screen_proposed_orders",), "tags": ("orders", "peer-call")}),
    _agent("after_visit_summary", "After-Visit Summary", 7,
           "Writes the patient's plain-language summary of this visit - reason, medicines and how to take them, "
           "next steps - from the committed record only.",
           {"id": "generate_after_visit_summary", "node": "after_visit_summary", "name": "Generate after-visit summary",
            "description": "A draft the doctor releases to the patient.",
            "inputs": ("documentation", "findings"), "outputs": ("avs",), "degrade_key": "avs",
            "tags": ("llm", "patient-facing")}),
)}

SKILL_BY_NODE: dict[str, Skill] = {s.node: s for a in REMOTE_AGENTS.values() for s in a.skills if s.node}
SKILL_BY_ID: dict[str, Skill] = {f"{s.agent_id}/{s.id}": s for a in REMOTE_AGENTS.values() for s in a.skills}
