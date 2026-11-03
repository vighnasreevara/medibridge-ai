// Query hooks. Keys here are the ones live events invalidate (see keysForEvent in live.ts).
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { api, qs } from './api';
import type {
  A2AStatus, AdminUser, AfterVisitSummary, ResultSeries, AllergyView, AuditRow, ConditionView, DocumentationView, DocumentDetail, DocumentText, DocumentView,
  EncounterDetail, Handoff, Health, MedicationScreen, MedicationSummary, MedicationView, NoteView,
  ObservabilitySummary, Overview, PatientProfile, PatientSummary, RunSummary, RunView, StaffMember, TimelineItem,
  Topology, Transcription, VitalView, WorkflowState,
} from './types';

export const useOverview = () =>
  useQuery({ queryKey: ['overview'], queryFn: ({ signal }) => api.get<Overview>('/api/overview', signal), refetchInterval: 30_000 });

export const useHealth = () =>
  useQuery({ queryKey: ['health'], queryFn: ({ signal }) => api.get<Health>('/api/health', signal), refetchInterval: 60_000 });

export const usePatientSearch = (q: string, scope: 'all' | 'mine') =>
  useQuery({
    queryKey: ['patients', 'search', q, scope],
    queryFn: ({ signal }) => api.get<{ items: PatientSummary[] }>(`/api/patients${qs({ q, scope })}`, signal),
    placeholderData: keepPreviousData,
    enabled: q.length === 0 || q.length >= 2,
  });

const patientKey = (id: string, part: string) => ['patient', id, part] as const;

export const usePatient = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'profile'), queryFn: ({ signal }) => api.get<PatientProfile>(`/api/patients/${id}`, signal) });

export const useTimeline = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'timeline'), queryFn: ({ signal }) => api.get<{ items: TimelineItem[] }>(`/api/patients/${id}/timeline`, signal) });

export const useConditions = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'conditions'), queryFn: ({ signal }) => api.get<{ items: ConditionView[] }>(`/api/patients/${id}/conditions`, signal) });

export const useMedications = (id: string) =>
  useQuery({
    queryKey: patientKey(id, 'medications'),
    queryFn: ({ signal }) => api.get<{ items: MedicationView[]; summary: MedicationSummary }>(`/api/patients/${id}/medications`, signal),
  });

export const useAllergies = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'allergies'), queryFn: ({ signal }) => api.get<{ items: AllergyView[] }>(`/api/patients/${id}/allergies`, signal) });

export const useNotes = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'notes'), queryFn: ({ signal }) => api.get<{ items: NoteView[] }>(`/api/patients/${id}/notes`, signal) });

export const useNote = (patientId: string, noteId: string | null) =>
  useQuery({
    queryKey: ['patient', patientId, 'note', noteId],
    queryFn: ({ signal }) => api.get<NoteView>(`/api/patients/${patientId}/notes/${noteId}`, signal),
    enabled: !!noteId,
  });

export const useDocuments = (id: string) =>
  useQuery({
    queryKey: [...patientKey(id, 'documents')],
    queryFn: ({ signal }) => api.get<{ items: DocumentView[] }>(`/api/patients/${id}/documents`, signal),
    enabled: !!id,
  });

export const useVitals = (id: string) =>
  useQuery({ queryKey: patientKey(id, 'vitals'), queryFn: ({ signal }) => api.get<{ items: VitalView[] }>(`/api/patients/${id}/vitals`, signal) });

export const useWorklist = (params: { day?: string; status?: string; scope?: 'mine' | 'all' }) =>
  useQuery({
    queryKey: ['worklist', params],
    queryFn: ({ signal }) => api.get<{ items: EncounterDetail[] }>(`/api/encounters${qs(params)}`, signal),
  });

export const useEncounter = (id: string) =>
  useQuery({ queryKey: ['encounter', id], queryFn: ({ signal }) => api.get<EncounterDetail>(`/api/encounters/${id}`, signal) });

export const useStaff = (role?: 'doctor' | 'nurse' | 'admin') =>
  useQuery({ queryKey: ['staff', role], queryFn: ({ signal }) => api.get<{ items: StaffMember[] }>(`/api/staff${qs({ role })}`, signal), staleTime: 300_000 });

// ---------------------------------------------------------------- workflow, records, agents, governance

export const useWorkflow = (encounterId: string) =>
  useQuery({
    queryKey: ['encounter', encounterId, 'workflow'],
    queryFn: ({ signal }) => api.get<WorkflowState>(`/api/encounters/${encounterId}/workflow`, signal),
  });

export const useTranscription = (id: string | null | undefined) =>
  useQuery({
    queryKey: ['transcription', id],
    queryFn: ({ signal }) => api.get<Transcription>(`/api/transcriptions/${id}`, signal),
    enabled: !!id,
  });

export const useHandoff = (encounterId: string, enabled = true) =>
  useQuery({
    queryKey: ['encounter', encounterId, 'handoff'],
    queryFn: ({ signal }) => api.get<Handoff>(`/api/encounters/${encounterId}/handoff`, signal),
    enabled,
    retry: false,
  });

export const useDocumentation = (encounterId: string, enabled = true) =>
  useQuery({
    queryKey: ['encounter', encounterId, 'documentation'],
    queryFn: ({ signal }) => api.get<DocumentationView>(`/api/encounters/${encounterId}/documentation`, signal),
    enabled,
    // While the agents screen or commit, poll as a fallback to the live feed.
    refetchInterval: (q) => (q.state.data?.run_status === 'running' ? 3000 : false),
  });

export const useAfterVisitSummary = (encounterId: string, enabled = true) =>
  useQuery({
    queryKey: ['encounter', encounterId, 'avs'],
    queryFn: ({ signal }) => api.get<{ avs: AfterVisitSummary | null; encounter_status: string }>(`/api/encounters/${encounterId}/after-visit-summary`, signal),
    enabled,
    refetchInterval: (q) => (q.state.data && !q.state.data.avs && q.state.data.encounter_status === 'finalized' ? 4000 : false),
  });

export const usePatientResults = (patientId: string, enabled = true) =>
  useQuery({
    queryKey: ['patient', patientId, 'results'],
    queryFn: ({ signal }) => api.get<{ items: ResultSeries[] }>(`/api/patients/${patientId}/results`, signal),
    enabled,
  });

export const useRun = (runId: string | null | undefined) =>
  useQuery({ queryKey: ['run', runId], queryFn: ({ signal }) => api.get<RunView>(`/api/runs/${runId}`, signal), enabled: !!runId });

export const useRuns = (status?: string) =>
  useQuery({
    queryKey: ['runs', status],
    queryFn: ({ signal }) => api.get<{ items: RunSummary[] }>(`/api/agents/runs${qs({ status, limit: 60 })}`, signal),
  });

export const useA2AStatus = () =>
  useQuery({ queryKey: ['a2a-status'], queryFn: ({ signal }) => api.get<A2AStatus>('/api/agents/a2a', signal), refetchInterval: 15_000 });

export const useTopology = () =>
  useQuery({ queryKey: ['topology'], queryFn: ({ signal }) => api.get<Topology>('/api/agents/topology', signal), staleTime: Infinity });

export const useObservability = (hours: number) =>
  useQuery({
    queryKey: ['observability', hours],
    queryFn: ({ signal }) => api.get<ObservabilitySummary>(`/api/observability/summary${qs({ hours })}`, signal),
    refetchInterval: 30_000,
  });

export const useDocument = (id: string) =>
  useQuery({ queryKey: ['documents', id], queryFn: ({ signal }) => api.get<DocumentDetail>(`/api/documents/${id}`, signal) });

export const useDocumentText = (id: string, version?: number) =>
  useQuery({
    queryKey: ['documents', id, 'text', version],
    queryFn: ({ signal }) => api.get<DocumentText>(`/api/documents/${id}/text${qs({ version })}`, signal),
  });

export const useMedicationScreen = (patientId: string | null) =>
  useQuery({
    queryKey: ['patient', patientId, 'medication-safety'],
    queryFn: ({ signal }) => api.get<MedicationScreen>(`/api/patients/${patientId}/medication-safety`, signal),
    enabled: !!patientId,
    retry: false,
  });

export const useAudit = (params: Record<string, string | number | undefined>) =>
  useQuery({
    queryKey: ['audit', params],
    queryFn: ({ signal }) => api.get<{ items: AuditRow[]; next_before_seq: number | null }>(`/api/audit${qs(params)}`, signal),
    placeholderData: keepPreviousData,
  });

export const useAuditVerify = () =>
  useQuery({
    queryKey: ['audit', 'verify'],
    queryFn: ({ signal }) => api.get<{ intact: boolean; last_seq: number; head_hash: string | null; problems: { seq: number; problem: string }[] }>('/api/audit/verify', signal),
  });

export const useAuditSummary = (hours: number) =>
  useQuery({
    queryKey: ['audit', 'summary', hours],
    queryFn: ({ signal }) => api.get<{
      by_category: { category: string; outcome: string; count: number }[];
      top_actions: { action: string; count: number }[];
      daily: { day: string; outcome: string; count: number }[];
    }>(`/api/audit/summary${qs({ since_hours: hours })}`, signal),
  });

export const useAdminUsers = () =>
  useQuery({
    queryKey: ['admin', 'users'],
    queryFn: ({ signal }) => api.get<{ items: AdminUser[]; roles: { key: string; label: string; description: string; permissions: string[] }[] }>('/api/admin/users', signal),
  });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const useAdminConfig = () => useQuery({ queryKey: ['admin', 'config'], queryFn: ({ signal }) => api.get<Record<string, any>>('/api/admin/config', signal) });

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
