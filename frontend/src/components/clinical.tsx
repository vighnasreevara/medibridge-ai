// Shared clinical UI: patient picker (care team only), run progress (from backend execution rows only),
// evidence drawer (the exact source behind a cited statement) and safety-alert rendering.
import { Link } from '@tanstack/react-router';
import { AlertTriangle, ExternalLink, FileText, ShieldAlert } from 'lucide-react';
import type { ReactNode } from 'react';

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { statusDef } from '@/domain/status';
import { usePatientSearch } from '@/lib/queries';
import type { EvidenceItem, RunView, SafetyAlert } from '@/lib/types';
import { cn } from '@/lib/utils';

import { GlyphSvg, Loading, RolePill, SourceBadge, StatusBadge, fmtDate } from './common';

export function PatientPicker({ value, onChange }: { value: string | null; onChange: (id: string) => void }) {
  const q = usePatientSearch('', 'mine');
  return (
    <Select value={value ?? undefined} onValueChange={onChange}>
      <SelectTrigger className="w-full sm:w-80" aria-label="Patient"><SelectValue placeholder="Choose a patient in your care team" /></SelectTrigger>
      <SelectContent>
        {q.data?.items.map((p) => <SelectItem key={p.id} value={p.id}>{p.display_name} - {p.mrn}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

// Order and labels of the visible pipeline. Status comes only from execution rows returned by the API.
export const PIPELINE: { node: string; label: string; gate?: boolean }[] = [
  { node: 'registry_check', label: 'Patient Registry: identity and consent' },
  { node: 'scribe_transcribe', label: 'Ambient Scribe: transcription' },
  { node: 'transcript_review', label: 'Transcript review', gate: true },
  { node: 'scribe_extract', label: 'Ambient Scribe: clinical extraction' },
  { node: 'chart_review', label: 'Chart Agent: record (EHR)' },
  { node: 'evidence_retrieval', label: 'Evidence Retrieval: prior reports' },
  { node: 'medication_screen', label: 'Medication Safety: current medicines' },
  { node: 'handoff_brief', label: 'Clinical Handoff Brief' },
  { node: 'handoff_review', label: 'Doctor review', gate: true },
  { node: 'order_draft', label: 'Order Entry: note draft' },
  { node: 'orders_review', label: 'Doctor: note and orders', gate: true },
  { node: 'order_safety_check', label: 'Order Entry + Medication Safety: order check' },
  { node: 'safety_review', label: 'Doctor: safety review', gate: true },
  { node: 'chart_commit', label: 'Chart Agent: record update' },
  { node: 'after_visit_summary', label: 'After-Visit Summary for the patient' },
];

export function nodeStatus(run: RunView | undefined, node: string): string {
  if (!run) return 'pending';
  if (run.waiting_gate === node) return 'waiting_human';
  const agentEx = (run.executions ?? []).filter((e) => e.kind === 'agent' && e.node === node);
  // An agent that could not be reached leaves no execution of its own; the supervisor's A2A call shows what happened.
  const ex = agentEx.length ? agentEx : (run.executions ?? []).filter((e) => e.kind === 'a2a' && e.node === node);
  const gateEvents = (run.events ?? []).filter((e) => e.node === node && e.type.startsWith('gate.'));
  if (gateEvents.some((e) => e.type === 'gate.resolved')) return 'succeeded';
  if (!ex.length) return run.status === 'failed' || run.status === 'blocked' ? 'skipped' : 'pending';
  const last = ex[ex.length - 1];
  return last.status === 'skipped' ? 'waiting_human' : last.status;
}

export function AgentProgress({ run }: { run: RunView | undefined }) {
  if (!run) return <Loading />;
  return (
    <ol className="flex flex-col gap-1.5" aria-label="Workflow progress">
      {PIPELINE.map((p) => {
        const st = nodeStatus(run, p.node);
        const ex = (run.executions ?? []).filter((e) => e.kind === 'agent' && e.node === p.node).at(-1);
        return (
          <li key={p.node} className="flex items-center justify-between gap-3 text-sm">
            <span className={cn('flex items-center gap-2', st === 'pending' && 'text-fg-subtle')}>
              {p.label}{p.gate && <RolePill role="human" className="text-[10px]">person</RolePill>}
            </span>
            <span className="flex items-center gap-2">
              {ex?.duration_ms != null && st !== 'waiting_human' && <span className="tnum text-xs text-fg-muted">{(ex.duration_ms / 1000).toFixed(1)} s</span>}
              {ex?.error_code && <span className="text-xs text-fail">{ex.error_code}</span>}
              <StatusBadge status={st === 'pending' ? 'queued' : st} label={st === 'pending' ? 'Not started' : undefined} />
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function EvidenceDrawer({ id, item, onClose }: { id: string | null; item?: EvidenceItem; onClose: () => void }) {
  const docId = id?.startsWith('document:') ? id.slice(9).split('@')[0] : null;
  return (
    <Sheet open={!!id} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{item?.label ?? 'Evidence'}</SheetTitle>
          <SheetDescription>{item ? `${item.kind.replace(/_/g, ' ')}${item.date ? ` - ${item.date}` : ''}` : 'Not part of this handoff\'s evidence bundle.'}</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-3 px-4 pb-6">
          {item?.source_kind && <SourceBadge kind={item.source_kind} />}
          {item && <blockquote className="rounded-lg border bg-surface-2 p-3 text-sm leading-6 whitespace-pre-wrap">{item.text}</blockquote>}
          {item?.section && <p className="text-xs text-fg-muted">Section: {item.section}{item.page ? `, page ${item.page}` : ''}</p>}
          <p className="font-mono text-[11px] break-all text-fg-subtle">{id}</p>
          {docId && <Link to="/records/$documentId" params={{ documentId: docId }} className="inline-flex items-center gap-1 text-sm"><FileText className="size-4" /> Open the document</Link>}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function EvidenceChips({ ids, onOpen }: { ids: string[]; onOpen: (id: string) => void }) {
  if (!ids.length) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {ids.map((id, i) => (
        <button key={id} type="button" onClick={() => onOpen(id)} title={id}
          className="rounded border border-line-strong px-1 font-mono text-[10px] leading-4 text-fg-muted hover:border-brand hover:text-brand">
          [{i + 1}]
        </button>
      ))}
    </span>
  );
}

const SEVERITY_ROLE = { contraindicated: 'fail', major: 'fail', moderate: 'warn', minor: 'neutral', caution: 'warn' } as const;

export function AlertCard({ alert, children }: { alert: SafetyAlert; children?: ReactNode }) {
  const role = SEVERITY_ROLE[alert.severity as keyof typeof SEVERITY_ROLE] ?? 'warn';
  return (
    <article className={cn('rounded-lg border p-3', role === 'fail' ? 'border-fail bg-fail-tint' : 'border-warn bg-warn-tint')}>
      <div className="flex flex-wrap items-center gap-2">
        {role === 'fail' ? <ShieldAlert className="size-4 text-fail" /> : <AlertTriangle className="size-4 text-warn" />}
        <RolePill role={role}>{alert.severity}</RolePill>
        <RolePill role="neutral">{alert.kind.replace(/_/g, ' ')}</RolePill>
        <RolePill role={alert.confidence === 'established' ? 'pass' : 'tool'}>
          {alert.confidence === 'established' ? 'Based on confirmed records' : 'Depends on unconfirmed information'}
        </RolePill>
      </div>
      <p className="mt-2 text-sm font-medium">{alert.summary}</p>
      {alert.depends_on.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-fg-muted">{alert.depends_on.map((d) => <li key={d}>{d}</li>)}</ul>
      )}
      {alert.evidence.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-fg-muted">Evidence ({alert.evidence.length})</summary>
          <ul className="mt-1 flex flex-col gap-1.5">
            {alert.evidence.map((e, i) => (
              <li key={i} className="rounded border bg-surface p-2">
                {e.quote && <q className="block italic">{e.quote}</q>}
                <span className="mt-1 flex flex-wrap items-center gap-2 text-fg-muted">
                  {e.title}{e.version ? ` (v${e.version}, ${e.effective_time ?? ''})` : ''}{e.basis ? ` - ${e.basis}` : ''}
                  {e.url && <a href={e.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5">source <ExternalLink className="size-3" /></a>}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {children}
      <p className="mt-2 text-[11px] text-fg-subtle">Status: {alert.status.replace(/_/g, ' ')}. Reference information for clinician judgment; not a prescribing decision.</p>
    </article>
  );
}

export function RunStatusLine({ run }: { run: RunView | null | undefined }) {
  if (!run) return <span className="text-sm text-fg-muted">No workflow has run for this encounter.</span>;
  const def = statusDef(run.status);
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <GlyphSvg glyph={def.glyph} /> {def.label}
      {run.waiting_gate && <span className="text-fg-muted">at {run.waiting_gate.replace(/_/g, ' ')}</span>}
      {run.error_code && <span className="text-fail">({run.error_code})</span>}
      <span className="text-xs text-fg-subtle">started {fmtDate(run.created_at, true)}</span>
    </span>
  );
}
