// API response shapes. These mirror backend/medibridge_api/services/clinical_views.py.

export type Role = 'doctor' | 'nurse' | 'admin';

export interface Me {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  role_label: string;
  permissions: string[];
  session_idle_minutes: number;
}

export type SourceKind =
  | 'patient_reported'
  | 'clinician_documented'
  | 'record_retrieved'
  | 'device'
  | 'unconfirmed'
  | 'conflicting';

export interface Provenance {
  source_kind: SourceKind;
  source_ref: string;
  source_phrase: string;
  certainty: 'stated' | 'uncertain' | 'inferred';
  status: 'draft' | 'confirmed' | 'refuted' | 'entered_in_error';
  recorded_at: string;
}

export interface PatientSummary {
  id: string;
  display_name: string;
  mrn: string;
  birth_date: string;
  age: number;
  sex_at_birth: string;
  in_care_team: boolean;
  is_synthetic: boolean;
}

export interface PatientProfile extends PatientSummary {
  counts: { encounters: number; documents: number; active_conditions: number; allergies: number };
}

export interface EncounterView {
  id: string;
  patient_id: string;
  encounter_class: string;
  status: string;
  chief_complaint: string;
  location: string;
  attending_user_id: string | null;
  attending_name: string | null;
  started_at: string;
  ended_at: string | null;
  identity_verified: boolean;
  identity_method: string | null;
  identity_verified_at: string | null;
  version: number;
}

export interface TimelineItem extends EncounterView {
  note_count: number;
  document_count: number;
}

export interface ConsentEntry {
  status: 'granted' | 'declined' | 'withdrawn';
  method: string;
  recorded_at: string;
  statement_version: string;
  id: string;
}

export interface EncounterDetail extends EncounterView {
  patient: PatientSummary;
  consent: Partial<Record<'audio_recording' | 'ai_processing', ConsentEntry>>;
  latest_run: { id: string; status: string; current_node: string | null; waiting_gate: string | null; error_code: string | null } | null;
  handoff: { id: string; status: string; current_version: number; approved_version: number | null } | null;
}

export interface ConditionView extends Provenance {
  id: string;
  encounter_id: string | null;
  name: string;
  code: string | null;
  clinical_status: string;
  verification_status: string;
  onset_date: string | null;
  abatement_date: string | null;
}

export interface MedicationView extends Provenance {
  id: string;
  medication_text: string;
  medication_id: string | null;
  dose_text: string | null;
  route: string | null;
  frequency_text: string | null;
  med_status: string;
  category: 'current' | 'historical' | 'status_unknown';
  effective_start: string | null;
  effective_end: string | null;
  last_reconciled_at: string | null;
  reconciliation: 'recent' | 'stale' | 'never_reconciled';
}

export interface MedicationSummary {
  current: number;
  historical: number;
  status_unknown: number;
  uncertain: number;
  reconciliation: 'recent' | 'stale' | 'never_reconciled';
}

export interface AllergyView extends Provenance {
  id: string;
  substance_text: string;
  category: string;
  reaction_text: string | null;
  reaction_severity: string | null;
  criticality: string;
  clinical_status: string;
  verification_status: string;
}

export interface NoteView {
  id: string;
  encounter_id: string;
  note_type: string;
  title: string;
  status: string;
  current_version: number;
  author_name: string | null;
  finalized_at: string | null;
  created_at: string;
  source_ref: string;
  body?: string;
  versions?: { version: number; author_kind: string; created_by: string | null; created_at: string; content_sha256: string; change_reason: string }[];
}

export interface DocumentView {
  id: string;
  encounter_id: string | null;
  document_type: string;
  title: string;
  clinical_date: string | null;
  source_name: string;
  status: string;
  current_version: number;
  created_at: string;
  version?: number;
  mime_type?: string;
  size_bytes?: number;
  page_count?: number | null;
  content_sha256?: string;
  ingestion_status?: string;
  ingestion_error?: string | null;
  embedding_model?: string | null;
  indexed_at?: string | null;
  phi_scan?: { kind: string; count: number }[];
  source_ref?: string;
}

export interface VitalView {
  id: string;
  encounter_id: string;
  kind: string;
  value: number;
  unit: string;
  measured_at: string;
  recorded_by: string;
}

export interface WorkflowEventView {
  seq: number;
  event_id?: string;
  type: string;
  run_id: string | null;
  encounter_id: string | null;
  patient_id?: string | null;
  agent_id?: string | null;
  node: string | null;
  tool_name?: string | null;
  status: string | null;
  attrs?: Record<string, unknown>;
  created_at: string;
}

export interface Overview {
  scope: 'care_team' | 'all';
  encounters_today: number;
  awaiting_intake: number;
  in_progress: number;
  awaiting_review: number;
  draft_handoffs: number;
  pending_approvals: number;
  approvals_today: number;
  runs_by_status: Record<string, number>;
  recent_events: WorkflowEventView[];
}

export interface HealthComponent {
  id: string;
  label: string;
  status: 'pass' | 'warn' | 'fail';
  summary: string;
  latency_ms: number | null;
  details: Record<string, unknown>;
}

export interface Health {
  status: 'pass' | 'warn' | 'fail';
  checked_at: string;
  components: HealthComponent[];
}

export interface StaffMember {
  id: string;
  display_name: string;
  role: Role;
  specialty: string | null;
}

// ---------------------------------------------------------------- workflow
export interface ExecutionView {
  id: string;
  parent_id: string | null;
  kind: 'agent' | 'tool' | 'llm' | 'retrieval' | 'guardrail' | 'gate' | 'a2a';
  agent_id: string;
  node: string;
  tool_name: string | null;
  attempt: number;
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  load_ms: number | null;
  error_code: string | null;
  error_class: string | null;
  metrics: Record<string, unknown>;
}

export interface RunView {
  id: string;
  encounter_id: string;
  patient_id: string;
  status: string;
  current_node: string | null;
  waiting_gate: string | null;
  error_code: string | null;
  error_summary: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  executions?: ExecutionView[];
  events?: WorkflowEventView[];
}

export interface Word { w: string; start: number; end: number; p: number; uncertain: boolean; suspect_drug?: string }

export type Speaker = 'patient' | 'clinician' | 'unattributed';

export interface Segment {
  id: string;
  start: number | null;
  end: number | null;
  text: string;
  speaker: Speaker;
  words?: Word[];
  uncertain?: boolean;
  uncertain_reasons?: string[];
  original_text?: string | null;
}

export interface Transcription {
  id: string;
  encounter_id: string;
  status: string;
  model: string | null;
  audio_duration_s: number | null;
  raw_segments: Segment[];
  reviewed_segments: Segment[] | null;
  reviewed_at: string | null;
  error_code: string | null;
  has_audio: boolean;
}

export interface Finding {
  evidence_id: string;
  name?: string;
  substance?: string;
  source_phrase: string;
  source_kind: string;
  source_ref: string;
  segment_id: string | null;
  negated?: boolean;
  duration?: string | null;
  dose?: string | null;
  frequency?: string | null;
  certainty?: string;
  taking_now?: string;
  reaction?: string | null;
  normalized?: {
    name: string | null;
    rxcui?: string | null;
    match_type: string;
    certainty?: string | null;
    candidates?: { name: string; score: number; basis: string }[];
  };
}

export interface Findings {
  symptoms: Finding[];
  medications: Finding[];
  allergies: Finding[];
  conditions: Finding[];
  unverified: { kind: string; item: Record<string, unknown>; problems: string[] }[];
  chief_complaint: string | null;
  allergy_statement: string;
  allergy_denial?: string;
  allergy_denial_phrase?: string;
  model: string;
  prompt_version: string;
  attempts: number;
}

export interface Escalation {
  rule_id: string;
  level: 'urgent' | 'review';
  label: string;
  message: string;
  matched_terms: string[];
  evidence_ids: string[];
  rules_version: string;
  status: 'open' | 'acknowledged';
}

export interface Citation {
  title?: string;
  url?: string;
  section?: string;
  quote?: string;
  version?: string;
  effective_time?: string;
  basis?: string;
}

export interface SafetyAlert {
  kind: 'interaction' | 'allergy' | 'allergy_caution' | 'condition_contraindication';
  id: string;
  severity: string;
  summary: string;
  drugs: string[];
  patient_facts: { text: string; ref?: string; source_kind?: string }[];
  evidence: Citation[];
  status: string;
  confidence: 'established' | 'possible';
  depends_on: string[];
}

export interface Coverage {
  documents_on_file: number;
  indexed: number;
  failed: { document_id: string; title: string; clinical_date: string | null; error: string | null }[];
  not_yet_indexed: number;
}

export interface WorkflowState {
  run: RunView | null;
  transcription_id?: string;
  findings?: Findings | null;
  escalations?: Escalation[];
  conflicts?: { kind: string; field: string; subject: string; summary: string; evidence_ids: string[]; resolution: string }[];
  missing_documents?: { what: string; referenced_in: string; date: string; summary: string }[];
  medication_list_state?: string;
  prior_encounters?: { evidence_id: string; date: string; reason: string; class: string; attending: string | null }[];
  documents?: {
    status: string | null;
    coverage: Coverage | null;
    queries: string[];
    unavailable_reason: string | null;
    evidence: { evidence_id: string; title: string; clinical_date: string | null; section: string; score: number; document_id: string; text: string; age_days: number | null }[];
  };
  medication_safety?: {
    status: string | null;
    alerts: SafetyAlert[];
    completeness: { status: string; reasons: string[] } | null;
    statement: string | null;
    release: string | null;
  };
  outcome?: Record<string, unknown> | null;
}

export interface HandoffItem {
  text: string;
  evidence_ids: string[];
  author: 'ai' | 'system' | 'doctor';
  source_kind?: string;
  certainty?: string;
  negated?: boolean;
  severity?: string;
  confidence?: string;
  kind?: string;
  warning?: string;
  suggested?: boolean;
  depends_on?: string[];
  citations?: Citation[];
  check?: string;
  status?: 'pass' | 'warn' | 'fail';
  category?: string;
}

export type SbarGroup = 'situation' | 'background' | 'assessment' | 'recommendation' | 'governance';
export interface HandoffSection { key: string; title: string; group?: SbarGroup; items: HandoffItem[]; note?: string | null }
export interface PreflightCheck { key: string; label: string; status: 'pass' | 'warn' | 'fail'; detail: string }

export interface EvidenceItem {
  kind: string;
  label: string;
  text: string;
  date?: string | null;
  source_kind?: string;
  ref?: string;
  section?: string;
  page?: number | null;
}

export interface Handoff {
  handoff_id: string;
  status: string;
  current_version: number;
  approved_version: number | null;
  approved_at: string | null;
  version: number;
  content_sha256: string;
  content: {
    schema: string;
    labels: Record<string, boolean | string>;
    generated_at: string;
    escalations: Escalation[];
    sections: HandoffSection[];
    preflight?: PreflightCheck[];
    format?: string;
    specialty?: string;
    generation: Record<string, unknown>;
    notice?: string;
    edited_by_doctor?: boolean;
  };
  evidence_bundle: Record<string, EvidenceItem>;
  author_kind: string;
  model: string | null;
  prompt_version: string | null;
  validation: { privacy_removed?: { kind: string; count: number }[]; generation?: Record<string, unknown>; statements?: number };
  created_at: string;
  versions: { version: number; author_kind: string; created_at: string; content_sha256: string; change_summary: string }[];
}

export interface OrderLine {
  medication_text: string; dose_text: string; route: string; frequency_text: string; duration_text?: string;
  instructions?: string; refills?: number; quantity_text?: string;
}

export interface OrdersScreen {
  status: 'clear' | 'alerts';
  blocking: number;
  alerts: SafetyAlert[];
  statement?: string;
  release?: string;
  content_sha256?: string;
}

export interface Changeset {
  id: string; status: 'prepared' | 'committed' | 'superseded'; content_sha256: string;
  content: { note?: { title: string; body: string; plan?: string }; prescriptions?: OrderLine[]; facts?: unknown[] };
  safety_screen: OrdersScreen | null;
  safety_decision: { action: 'override' | 'revise'; reason?: string; acknowledged?: string[]; at?: string } | null;
  created_at: string; committed_at: string | null;
}

export interface DocumentationView {
  encounter_status: string;
  draft: { subjective: string; subjective_source: string; objective: string; suggested_findings: string[]; handoff_version: number } | null;
  run_waiting_gate: string | null;
  run_status: string | null;
  run_current_node: string | null;
  changeset: Changeset | null;
  history: Changeset[];
}

export interface AvsMedicine { name: string; dose: string; route: string | null; how_often: string | null; duration: string | null; instructions: string | null; plain: string }

export interface AfterVisitSummary {
  id: string; status: 'draft' | 'released' | 'superseded'; content_sha256: string; created_at: string; released_at: string | null;
  validation: { next_steps_source?: string };
  content: {
    patient: { name: string; birth_date: string; mrn: string };
    visit: { date: string; clinician: string | null; location: string; reason_recorded: string };
    reason_for_visit: { text: string; source: string };
    medicines: AvsMedicine[];
    no_new_medicines: boolean;
    next_steps: { text: string; source: string };
    when_to_get_help: { text: string; source: string };
    labels: { audience: string; synthetic_data: boolean; ai_assisted: boolean };
  };
}

export interface RegistryPatient { patient_id: string; mrn: string; given_name: string; family_name: string; birth_date: string; sex_at_birth: string; score?: number }
export interface RegistryResult {
  status: 'matched' | 'candidates' | 'no_match' | 'identity_mismatch' | 'registered' | 'possible_duplicates' | 'invalid' | 'denied';
  patient?: RegistryPatient; candidates?: RegistryPatient[]; method?: string; reason?: string;
}

export interface ResultSeries {
  name: string; code: string | null; unit: string | null; category: string; count: number;
  points: { date: string; value: number | null }[];
  latest: { date: string; value: number | null }; previous: { date: string; value: number | null } | null;
}

export interface DocumentDetail extends DocumentView {
  patient_id: string;
  versions: {
    version: number; content_sha256: string; size_bytes: number; mime_type: string; ingestion_status: string;
    ingestion_error: string | null; created_at: string; created_by: string | null; change_reason: string;
    embedding_model: string | null; indexed_at: string | null;
  }[];
}

export interface DocumentText {
  document_id: string;
  version: number;
  ingestion_status: string;
  ingestion_error: string | null;
  phi_scan: { kind: string; count: number }[];
  redacted: boolean;
  chunks: { index: number; section: string; page_from: number | null; page_to: number | null; text: string; evidence_id: string }[];
}

export interface SearchEvidence {
  evidence_id: string; document_id: string; document_version: number; title: string; document_type: string;
  clinical_date: string | null; source_name: string; section: string; page: number | null; text: string; score: number;
  age_days: number | null;
}

export interface MedicationScreen {
  release: string;
  sources: { id: string; name: string; version: string; license: string; url: string }[];
  medications: {
    text: string; ref?: string; source_kind?: string; certainty?: string; category?: string;
    resolution: { match_type: string; rxcui: string | null; name: string | null; candidates: { name: string; score: number; basis: string }[]; certainty: string };
  }[];
  alerts: SafetyAlert[];
  completeness: { status: string; reasons: string[] };
  statement: string;
  graph: { nodes: { id: string; label: string; kind: 'drug' | 'class' }[]; edges: { source: string; target: string; kind: string; severity?: string; id?: string }[] };
}

export interface Topology {
  graph: {
    nodes: { id: string; agent: string; gate: boolean; remote: { skill: string; url: string } | null }[];
    edges: { source: string; target: string; conditional: boolean }[];
  };
  agents: Record<string, { label: string; kind: string; role?: string }>;
  tools: { name: string; description: string; kind: string; permission: string; llm_callable: boolean }[];
  transport: 'a2a' | 'inprocess';
}

export interface AgentServiceStatus {
  id: string; label: string; skills: string[];
  status: 'up' | 'down' | 'mismatch' | 'in_process';
  url?: string; rpc_url?: string; card_url?: string; port?: number; latency_ms?: number | null; problem?: string | null;
  card?: { name: string; version: string; skills: string[]; protocol: string[] };
}

export interface A2AStatus { transport: 'a2a' | 'inprocess'; agents: AgentServiceStatus[] }

export interface RunSummary {
  id: string; encounter_id: string; status: string; current_node: string | null; waiting_gate: string | null;
  error_code: string | null; created_at: string; started_at: string | null; ended_at: string | null; executions: number;
}

export interface LatencyRow {
  name: string; runs: number; failed: number; blocked: number; retries: number; prompt_tokens: number;
  completion_tokens: number; p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}

export interface ObservabilitySummary {
  window_hours: number;
  agents: LatencyRow[];
  tools: LatencyRow[];
  a2a: (LatencyRow & { errors?: Record<string, number> })[];
  transport: 'a2a' | 'inprocess';
  llm: {
    calls: number; p50_ms: number | null; p95_ms: number | null; prompt_tokens: number; completion_tokens: number;
    by_model: Record<string, number>;
    recent: { agent: string; model: string; ms: number; load_ms: number | null; prompt_tokens: number | null; completion_tokens: number | null }[];
  };
  retrieval: { calls: number; p50_ms: number | null; p95_ms: number | null };
  runs_by_status: Record<string, number>;
  guardrail_events: Record<string, number>;
  access_denials: Record<string, number>;
  timeline: { t: string; agent: string; ms: number; status: string }[];
  models: Record<string, string>;
  tracing: { langsmith_enabled: boolean; endpoint_configured: boolean };
}

export interface AuditRow {
  seq: number; occurred_at: string; action: string; category: string; outcome: string; actor: string;
  actor_role: string | null; patient_mrn: string | null; encounter_id: string | null; resource_type: string | null;
  resource_id: string | null; ip: string | null; details: Record<string, unknown>; row_hash: string;
}

export interface AdminUser {
  id: string; email: string; display_name: string; role: Role; status: string; last_login_at: string | null;
  created_at: string; active_sessions: number; is_synthetic: boolean; temporary_password?: string;
}
