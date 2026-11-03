import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { toast } from 'sonner';

import { EmptyState, ErrorState, Loading, PageHeader, Panel, StatusBadge, Time } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api, qs } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useRuns, useWorklist } from '@/lib/queries';
import type { EncounterDetail, WorkflowEventView } from '@/lib/types';

const GATES = [
  { gate: 'transcript_review', title: 'Transcript review', who: 'Nurse or doctor', to: '/intake/$encounterId' as const },
  { gate: 'handoff_review', title: 'Handoff review', who: 'Doctor', to: '/handoffs/$encounterId' as const },
  { gate: 'orders_review', title: 'Note and orders', who: 'Doctor', to: '/handoffs/$encounterId' as const },
  { gate: 'safety_review', title: 'Medication safety review', who: 'Doctor', to: '/handoffs/$encounterId' as const },
];

const DECISION_EVENTS = ['gate.resolved', 'handoff.edited', 'documentation.committed', 'run.cancelled', 'run.failed', 'run.blocked', 'escalation.acknowledged'];
const EVENT_LABEL: Record<string, string> = {
  'gate.resolved': 'Gate resolved', 'handoff.edited': 'Handoff edited by doctor', 'documentation.committed': 'Documentation written to record',
  'run.cancelled': 'Run cancelled', 'run.failed': 'Run failed', 'run.blocked': 'Run blocked by guardrail', 'escalation.acknowledged': 'Escalation acknowledged',
};

function RunAction({ runId, kind }: { runId: string; kind: 'resume' | 'cancel' }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const act = useMutation({
    mutationFn: () => kind === 'resume' ? api.post(`/api/runs/${runId}/resume`) : api.post(`/api/runs/${runId}/cancel`, { reason }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['runs'] }); toast.success(kind === 'resume' ? 'Resuming from the last checkpoint' : 'Run cancelled'); },
    onError: (e) => toast.error((e as Error).message),
  });
  if (kind === 'resume') return <Button size="sm" variant="outline" onClick={() => act.mutate()} disabled={act.isPending}>Resume</Button>;
  return (
    <span className="flex gap-1">
      <Input className="h-8 w-40" placeholder="Reason to cancel" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Cancel reason" />
      <Button size="sm" variant="ghost" disabled={reason.trim().length < 5 || act.isPending} onClick={() => act.mutate()}>Cancel</Button>
    </span>
  );
}

export function ApprovalsPage() {
  const { can } = useAuth();
  const work = useWorklist({ scope: 'all', status: 'planned,in_progress,awaiting_review' });
  const problems = useRuns('failed,blocked,interrupted');
  const recent = useQuery({
    queryKey: ['events', 'decisions'],
    queryFn: ({ signal }) => api.get<{ events: WorkflowEventView[] }>(`/api/events${qs({ latest: 60, types: DECISION_EVENTS.join(',') })}`, signal),
  });
  const byGate = (gate: string): EncounterDetail[] => (work.data?.items ?? []).filter((e) => e.latest_run?.waiting_gate === gate);
  return (
    <>
      <PageHeader title="Workflow & approvals" subtitle="Human gates waiting for a decision, problem runs, and recent decisions." />
      {work.isError && <ErrorState error={work.error} />}
      <div className="grid gap-4 lg:grid-cols-3">
        {GATES.map((g) => (
          <Panel key={g.gate} title={g.title} subtitle={`Decided by: ${g.who}`} tone={byGate(g.gate).length ? 'human' : undefined}>
            {work.isPending ? <Loading /> : !byGate(g.gate).length ? <EmptyState title="Nothing waiting" /> : (
              <ul className="flex flex-col gap-2">{byGate(g.gate).map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-2 text-sm">
                  <Link to={g.to} params={{ encounterId: e.id }} className="font-medium">{e.patient.display_name}</Link>
                  <span className="text-xs text-fg-muted">{e.chief_complaint}</span>
                </li>))}</ul>
            )}
          </Panel>
        ))}
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Failed, blocked or interrupted runs" subtitle="Interrupted runs resume from their last checkpoint.">
          {problems.isPending ? <Loading /> : !problems.data?.items.length ? <EmptyState title="No problem runs" /> : (
            <ul className="flex flex-col gap-2">{problems.data.items.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-sm">
                <span className="flex flex-col"><span className="flex items-center gap-2"><StatusBadge status={r.status} />
                  <Link to="/encounters/$encounterId" params={{ encounterId: r.encounter_id }} className="font-mono text-xs">{r.id.slice(0, 8)}</Link></span>
                  <span className="text-xs text-fg-muted">{r.current_node} - {r.error_code ?? 'no error code'} - <Time iso={r.created_at} mode="rel" /></span></span>
                {can('workflow.start') && r.status === 'interrupted' && <span className="flex gap-2"><RunAction runId={r.id} kind="resume" /><RunAction runId={r.id} kind="cancel" /></span>}
              </li>))}</ul>
          )}
        </Panel>
        <Panel title="Recent decisions and record writes">
          {recent.isPending ? <Loading /> : !recent.data?.events.length ? <EmptyState title="No decisions yet" /> : (
            <ol className="flex flex-col gap-1.5">{recent.data.events.map((ev) => (
              <li key={ev.seq} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>{EVENT_LABEL[ev.type] ?? ev.type}{ev.attrs?.gate ? `: ${String(ev.attrs.gate).replace(/_/g, ' ')}` : ''}{ev.attrs?.decision ? ` (${String(ev.attrs.decision)})` : ''}</span>
                <span className="flex items-center gap-2 text-xs text-fg-muted">
                  {ev.encounter_id && <Link to="/encounters/$encounterId" params={{ encounterId: ev.encounter_id }}>encounter</Link>}
                  <Time iso={ev.created_at} mode="rel" /></span>
              </li>))}</ol>
          )}
        </Panel>
      </div>
    </>
  );
}
