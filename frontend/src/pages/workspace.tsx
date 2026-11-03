import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from '@tanstack/react-router';
import { AlertOctagon, Siren } from 'lucide-react';
import { toast } from 'sonner';

import { RunStatusLine } from '@/components/clinical';
import { EmptyState, ErrorState, Loading, Panel, RolePill, SourceBadge } from '@/components/common';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useWorkflow } from '@/lib/queries';
import type { Escalation, Finding, WorkflowState } from '@/lib/types';

function EscalationBanner({ encounterId, flags }: { encounterId: string; flags: Escalation[] }) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const ack = useMutation({
    mutationFn: (rule_id: string) => api.post(`/api/encounters/${encounterId}/escalations/ack`, { rule_id }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['encounter', encounterId] }); toast.success('Acknowledged and logged'); },
  });
  if (!flags.length) return null;
  return (
    <div className="flex flex-col gap-2" role="alert">
      {flags.map((f) => (
        <div key={f.rule_id} className={`flex flex-wrap items-start gap-3 rounded-lg border p-3 ${f.level === 'urgent' ? 'border-fail bg-fail-tint' : 'border-warn bg-warn-tint'}`}>
          <Siren className={`mt-0.5 size-5 ${f.level === 'urgent' ? 'text-fail' : 'text-warn'}`} aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{f.level === 'urgent' ? 'Urgent clinician review' : 'Clinician review'}: {f.label}</p>
            <p className="text-sm">{f.message}</p>
            <p className="mt-1 text-xs text-fg-muted">Matched: {f.matched_terms.join(', ')} - rules {f.rules_version} (demo rules, not clinically validated). The system cannot clear this flag.</p>
          </div>
          {f.status === 'acknowledged' ? <RolePill role="pass">Acknowledged</RolePill> : (me.role !== 'admin' &&
            <Button size="sm" variant="outline" onClick={() => ack.mutate(f.rule_id)} disabled={ack.isPending}>I have reviewed this</Button>)}
        </div>
      ))}
    </div>
  );
}

function FindingRow({ f, kind }: { f: Finding; kind: 'symptom' | 'medication' | 'allergy' | 'condition' }) {
  const label = f.name ?? f.substance ?? '';
  return (
    <li className="flex flex-col gap-1 rounded-md border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`font-medium ${f.negated ? 'line-through decoration-fg-subtle' : ''}`}>{label}</span>
        {f.negated && <RolePill role="neutral">denied</RolePill>}
        {f.duration && <span className="text-sm text-fg-muted">{f.duration}</span>}
        {kind === 'medication' && (f.dose || f.frequency) && <span className="text-sm text-fg-muted">{[f.dose, f.frequency].filter(Boolean).join(', ')}</span>}
        {f.reaction && <span className="text-sm text-fg-muted">reaction: {f.reaction}</span>}
        <SourceBadge kind={f.source_kind} certainty={f.certainty} />
        {kind === 'medication' && f.normalized?.match_type === 'suggestion' && (
          <RolePill role="tool">possibly {f.normalized.name} (similarity - confirm)</RolePill>
        )}
        {kind === 'medication' && f.normalized?.match_type === 'unresolved' && <RolePill role="tool">not identified</RolePill>}
      </div>
      <q className="text-xs text-fg-muted">{f.source_phrase}</q>
    </li>
  );
}

function MissingInfo({ wf }: { wf: WorkflowState }) {
  const items: { role: 'fail' | 'warn' | 'tool' | 'human'; text: string }[] = [];
  for (const c of wf.conflicts ?? []) items.push({ role: 'fail', text: `Conflict - ${c.summary}` });
  for (const m of wf.missing_documents ?? []) items.push({ role: 'warn', text: m.summary });
  for (const d of wf.documents?.coverage?.failed ?? []) items.push({ role: 'warn', text: `"${d.title}" could not be read (${d.error ?? 'ingestion failed'}); its content was not reviewed.` });
  if (wf.documents?.status === 'unavailable') items.push({ role: 'fail', text: 'Document search was unavailable; historical reports were not reviewed.' });
  if (wf.documents?.status === 'none_on_file') items.push({ role: 'tool', text: 'No documents are on file (this is not a normal finding).' });
  if (wf.medication_safety?.status === 'unavailable') items.push({ role: 'fail', text: 'Medication screening was not performed (knowledge graph unavailable).' });
  for (const r of wf.medication_safety?.completeness?.reasons ?? []) items.push({ role: 'tool', text: r });
  for (const u of wf.findings?.unverified ?? []) items.push({ role: 'human', text: `Unverified ${u.kind}: ${String(u.item.name ?? u.item.substance ?? '')} - ${u.problems[0]}` });
  if (!items.length) return <EmptyState title="Nothing flagged" body="No conflicts, missing reports or screening gaps were detected. This does not replace clinical review." />;
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((i, n) => <li key={n} className="flex items-start gap-2 text-sm"><RolePill role={i.role}>{i.role === 'fail' ? 'Conflict / gap' : i.role === 'human' ? 'Check' : 'Note'}</RolePill><span>{i.text}</span></li>)}
    </ul>
  );
}

export function EncounterWorkspace() {
  const { encounterId } = useParams({ from: '/encounters/$encounterId' });
  const q = useWorkflow(encounterId);
  if (q.isPending) return <Loading label="Loading workflow" />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const wf = q.data;
  if (!wf.run) {
    return (
      <Panel title="Intake">
        <EmptyState title="No intake has been processed" action={<Link to="/intake/$encounterId" params={{ encounterId }}>Start intake</Link>} />
      </Panel>
    );
  }
  const f = wf.findings;
  return (
    <>
      <EscalationBanner encounterId={encounterId} flags={wf.escalations ?? []} />
      <Panel title="Workflow" subtitle={<RunStatusLine run={wf.run} />}
        actions={<>
          <Link to="/intake/$encounterId" params={{ encounterId }} className="text-sm">Intake console</Link>
          {(wf.run.waiting_gate === 'handoff_review' || wf.run.waiting_gate === 'documentation_review' || wf.run.status === 'completed') &&
            <Link to="/handoffs/$encounterId" params={{ encounterId }} className="text-sm">Handoff</Link>}
        </>}>
        {wf.run.status === 'interrupted' && (
          <p className="flex items-center gap-2 text-sm text-tool"><AlertOctagon className="size-4" /> This run stopped unexpectedly. Resume it from Workflow &amp; approvals.</p>
        )}
        {f ? (
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="label mb-2">Current complaint (patient's words)</p>
              <p className="mb-3 text-sm">{f.chief_complaint ?? <span className="text-fg-muted">Not stated</span>}</p>
              <p className="label mb-2">Symptoms and timeline</p>
              <ul className="flex flex-col gap-1.5">{f.symptoms.map((s) => <FindingRow key={s.evidence_id} f={s} kind="symptom" />)}</ul>
              {f.conditions.length > 0 && <>
                <p className="label mt-3 mb-2">Conditions the patient mentioned</p>
                <ul className="flex flex-col gap-1.5">{f.conditions.map((s) => <FindingRow key={s.evidence_id} f={s} kind="condition" />)}</ul>
              </>}
            </div>
            <div>
              <p className="label mb-2">Medications mentioned today</p>
              {f.medications.length ? <ul className="flex flex-col gap-1.5">{f.medications.map((s) => <FindingRow key={s.evidence_id} f={s} kind="medication" />)}</ul>
                : <p className="text-sm text-fg-muted">None mentioned.</p>}
              <p className="label mt-3 mb-2">Allergies mentioned today</p>
              {f.allergies.length ? <ul className="flex flex-col gap-1.5">{f.allergies.map((s) => <FindingRow key={s.evidence_id} f={s} kind="allergy" />)}</ul>
                : <p className="text-sm text-fg-muted">{f.allergy_denial ? `Patient ${f.allergy_denial === 'denied' ? 'denied allergies' : 'was unsure'}: "${f.allergy_denial_phrase}"` : 'Not discussed.'}</p>}
              <p className="mt-3 text-xs text-fg-subtle">Extracted by {f.model} ({f.prompt_version}, {f.attempts} attempt{f.attempts === 1 ? '' : 's'}); every item is quoted from the reviewed transcript.</p>
            </div>
          </div>
        ) : <p className="text-sm text-fg-muted">Findings appear after the transcript is reviewed and extraction completes.</p>}
      </Panel>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Missing information and conflicts"><MissingInfo wf={wf} /></Panel>
        <Panel title="Relevant prior encounters">
          {wf.prior_encounters?.length ? (
            <ul className="flex flex-col gap-1.5 text-sm">
              {wf.prior_encounters.map((p) => <li key={p.evidence_id} className="flex justify-between gap-2"><span>{p.reason}</span><span className="tnum text-fg-muted">{p.date}</span></li>)}
            </ul>
          ) : <p className="text-sm text-fg-muted">No prior encounters, or history not yet retrieved.</p>}
        </Panel>
      </div>
    </>
  );
}
