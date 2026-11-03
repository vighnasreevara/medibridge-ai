import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { Bot, Check, FileText, Loader2, Pencil, Plus, Printer, ShieldAlert, ShieldCheck, Siren, Trash2, User, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { AlertCard, EvidenceChips, EvidenceDrawer } from '@/components/clinical';
import { EmptyState, ErrorState, Loading, PageHeader, Panel, RolePill, SourceBadge, StatusBadge, Time, fmtDate } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { api, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useAfterVisitSummary, useDocumentation, useEncounter, useHandoff, useWorklist } from '@/lib/queries';
import type { AfterVisitSummary, Handoff, HandoffItem, HandoffSection, OrderLine, SafetyAlert, SbarGroup } from '@/lib/types';
import { cn } from '@/lib/utils';

export function HandoffsPage() {
  const navigate = useNavigate();
  const q = useWorklist({ scope: 'all' });
  const items = (q.data?.items ?? []).filter((e) => e.handoff);
  const order: Record<string, number> = { in_review: 0, changes_requested: 1, draft: 2, generating: 3, approved: 4, rejected: 5 };
  items.sort((a, b) => (order[a.handoff!.status] ?? 9) - (order[b.handoff!.status] ?? 9));
  return (
    <>
      <PageHeader title="Clinical handoff briefs" subtitle="Structured cardiology briefs (SBAR) awaiting review, and reviewed briefs, for your care team." />
      <Panel>
        {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} /> : !items.length ? <EmptyState title="No handoffs yet" body="Handoffs appear after an intake has been processed." /> : (
          <ul className="divide-y">
            {items.map((e) => (
              <li key={e.id}>
                <button type="button" className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3 text-left hover:bg-surface-2"
                  onClick={() => navigate({ to: '/handoffs/$encounterId', params: { encounterId: e.id } })}>
                  <span className="min-w-48 flex-1"><span className="font-medium">{e.patient.display_name}</span>
                    <span className="block text-sm text-fg-muted">{e.chief_complaint}</span></span>
                  <span className="text-sm text-fg-muted">v{e.handoff!.current_version}</span>
                  <Time iso={e.started_at} withTime />
                  <StatusBadge status={e.handoff!.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

const AUTHOR_ICON = { ai: Bot, system: Check, doctor: User } as const;

function ItemView({ item, onOpen }: { item: HandoffItem; onOpen: (id: string) => void }) {
  const Icon = AUTHOR_ICON[item.author] ?? Check;
  return (
    <li className="flex gap-2 py-1">
      <Icon className={cn('mt-1 size-3.5 shrink-0', item.author === 'ai' ? 'text-run' : item.author === 'doctor' ? 'text-human' : 'text-fg-subtle')}
        aria-label={item.author === 'ai' ? 'AI-written' : item.author === 'doctor' ? 'Doctor-written' : 'Assembled from records'} />
      <div className="min-w-0 text-sm leading-6">
        <span className={cn(item.negated && 'text-fg-muted')}>{item.text}</span>
        {item.suggested && <RolePill role="run" className="ml-1">suggested question</RolePill>}
        {item.warning && <RolePill role="warn" className="ml-1">{item.warning.replace(/_/g, ' ')}</RolePill>}
        {item.certainty && item.certainty !== 'stated' && <RolePill role="tool" className="ml-1">{item.certainty}</RolePill>}
        {item.source_kind && item.author !== 'ai' && <span className="ml-1"><SourceBadge kind={item.source_kind} /></span>}
        <EvidenceChips ids={item.evidence_ids.filter((x) => !['intake'].includes(x))} onOpen={onOpen} />
      </div>
    </li>
  );
}

function ChecksView({ section }: { section: HandoffSection }) {
  return (
    <ul className="grid gap-1.5 sm:grid-cols-2">
      {section.items.map((it) => (
        <li key={it.check} className="flex items-start gap-2 rounded-md border px-2 py-1.5 text-sm">
          <StatusBadge status={it.status ?? 'pass'} />
          <span className="min-w-0 text-fg-muted">{it.text.replace(/^\[[A-Z]+\]\s*/, '')}</span>
        </li>
      ))}
    </ul>
  );
}

function SectionView({ section, onOpen, alerts }: { section: HandoffSection; onOpen: (id: string) => void; alerts: SafetyAlert[] }) {
  if (section.key === 'pre_generation_checks') return <ChecksView section={section} />;
  if (section.key === 'medication_alerts' && alerts.length) {
    return (
      <div className="flex flex-col gap-2">
        {alerts.map((a) => <AlertCard key={a.id} alert={a} />)}
        {section.note && <p className="text-xs text-fg-muted">{section.note}</p>}
      </div>
    );
  }
  return (
    <>
      {section.items.length ? <ul>{section.items.map((it, i) => <ItemView key={i} item={it} onOpen={onOpen} />)}</ul>
        : <p className="text-sm text-fg-muted">{section.note ? '' : 'Nothing to report from the available evidence.'}</p>}
      {section.note && <p className="mt-1 text-xs text-fg-muted">{section.note}</p>}
    </>
  );
}

function alertsFrom(h: Handoff): SafetyAlert[] {
  const sec = h.content.sections.find((s) => s.key === 'medication_alerts');
  return (sec?.items ?? []).filter((i) => i.severity).map((i, n) => ({
    kind: (i.kind ?? 'interaction') as SafetyAlert['kind'], id: String(n), severity: i.severity!, summary: i.text.replace(/^\[[A-Z]+\]\s*/, ''),
    drugs: [], patient_facts: [], evidence: i.citations ?? [], status: 'demo_curated_not_clinically_reviewed',
    confidence: (i.confidence ?? 'possible') as SafetyAlert['confidence'], depends_on: i.depends_on ?? [],
  }));
}

function EditForm({ h, onDone }: { h: Handoff; onDone: () => void }) {
  const qc = useQueryClient();
  const [sections, setSections] = useState(() => h.content.sections.map((s) => ({ ...s, items: s.items.map((i) => ({ ...i })) })));
  const [summary, setSummary] = useState('');
  const editable = new Set(['chief_complaint', 'current_symptoms', 'symptom_timeline', 'cardiac_history', 'historical_reports', 'unresolved_questions']);
  const submit = useMutation({
    mutationFn: (encounterId: string) => api.post<Handoff>(`/api/encounters/${encounterId}/handoff/edit`, {
      base_version: h.version, base_sha256: h.content_sha256, sections, change_summary: summary,
    }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['encounter'] }); toast.success('Saved as a new version'); onDone(); },
  });
  const { encounterId } = useParams({ from: '/handoffs/$encounterId' });
  return (
    <div className="flex flex-col gap-4">
      {sections.filter((s) => editable.has(s.key)).map((s) => (
        <fieldset key={s.key} className="rounded-lg border p-3">
          <legend className="px-1 text-sm font-semibold">{s.title}</legend>
          <div className="flex flex-col gap-2">
            {s.items.map((it, i) => (
              <div key={i} className="flex items-start gap-2">
                <Textarea rows={2} value={it.text} aria-label={`${s.title} item ${i + 1}`}
                  onChange={(e) => setSections((all) => all.map((x) => x.key !== s.key ? x : { ...x, items: x.items.map((y, j) => j === i ? { ...y, text: e.target.value } : y) }))} />
                <Button variant="ghost" size="icon" aria-label="Remove" onClick={() => setSections((all) => all.map((x) => x.key !== s.key ? x : { ...x, items: x.items.filter((_, j) => j !== i) }))}><Trash2 /></Button>
              </div>
            ))}
            <Button variant="outline" size="sm" className="self-start" onClick={() => setSections((all) => all.map((x) => x.key !== s.key ? x : { ...x, items: [...x.items, { text: '', evidence_ids: [], author: 'doctor' as const }] }))}><Plus /> Add line</Button>
          </div>
        </fieldset>
      ))}
      <div className="flex flex-col gap-1.5"><Label htmlFor="edit-summary">What did you change?</Label>
        <Input id="edit-summary" value={summary} onChange={(e) => setSummary(e.target.value)} /></div>
      {submit.isError && <p role="alert" className="text-sm text-fail">{(submit.error as Error).message}</p>}
      <div className="flex gap-2"><Button disabled={summary.trim().length < 3 || submit.isPending} onClick={() => submit.mutate(encounterId)}>Save new version</Button>
        <Button variant="outline" onClick={onDone}>Cancel</Button></div>
    </div>
  );
}

function DecisionPanel({ h, encounterId }: { h: Handoff; encounterId: string }) {
  const qc = useQueryClient();
  const [reviewed, setReviewed] = useState(false);
  const [rationale, setRationale] = useState('');
  const decide = useMutation({
    mutationFn: (decision: 'approved' | 'rejected' | 'changes_requested') => api.post(`/api/encounters/${encounterId}/handoff/decision`, {
      version: h.version, expected_sha256: h.content_sha256, decision, rationale,
      feedback: decision === 'changes_requested' ? rationale.split(/\n+/).filter(Boolean) : [],
    }),
    onSuccess: (_, d) => {
      void qc.invalidateQueries({ queryKey: ['encounter', encounterId] });
      toast.success(d === 'approved' ? 'Handoff approved' : d === 'rejected' ? 'Handoff rejected' : 'Regeneration requested');
    },
    onError: (e) => { if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: ['encounter', encounterId] }); },
  });
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-fg-muted">You are deciding on version {h.version} (hash {h.content_sha256.slice(0, 12)}). If the handoff changes, this decision is refused and you must review again.</p>
      <label className="flex items-start gap-2 text-sm"><Checkbox checked={reviewed} onCheckedChange={(v) => setReviewed(v === true)} className="mt-0.5" />
        I have reviewed the handoff against the cited source evidence.</label>
      <Textarea rows={3} placeholder="Rationale (required to reject or request changes; changes requested are sent to the AI as feedback)"
        value={rationale} onChange={(e) => setRationale(e.target.value)} aria-label="Rationale" />
      <div className="flex flex-wrap gap-2">
        <Button disabled={!reviewed || decide.isPending} onClick={() => decide.mutate('approved')}><Check /> Approve</Button>
        <Button variant="outline" disabled={!reviewed || rationale.trim().length < 5 || decide.isPending} onClick={() => decide.mutate('changes_requested')}>Request changes</Button>
        <Button variant="destructive" disabled={!reviewed || rationale.trim().length < 5 || decide.isPending} onClick={() => decide.mutate('rejected')}><X /> Reject</Button>
      </div>
      {decide.isError && <p role="alert" className="text-sm text-fail">{(decide.error as Error).message}</p>}
    </div>
  );
}

interface RxRow { medication_text: string; dose_text: string; route: string; frequency_text: string; duration_text: string; instructions: string }
const EMPTY_RX: RxRow = { medication_text: '', dose_text: '', route: 'oral', frequency_text: '', duration_text: '', instructions: '' };
const BLOCKING = ['contraindicated', 'major'];

function OrderSummary({ orders }: { orders: OrderLine[] }) {
  if (!orders.length) return <p className="text-sm text-fg-muted">No medication orders.</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {orders.map((o, i) => <li key={i}><b>{o.medication_text}</b> {o.dose_text} {o.route} {o.frequency_text}{o.duration_text ? ` for ${o.duration_text}` : ''}</li>)}
    </ul>
  );
}

function SafetyReview({ encounterId, cs }: { encounterId: string; cs: NonNullable<ReturnType<typeof useDocumentation>['data']>['changeset'] & object }) {
  const qc = useQueryClient();
  const screen = cs.safety_screen!;
  const blocking = screen.alerts.filter((a) => BLOCKING.includes(a.severity));
  const [acks, setAcks] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const decide = useMutation({
    mutationFn: (action: 'override' | 'revise') => api.post(`/api/encounters/${encounterId}/documentation/safety-decision`, {
      changeset_id: cs.id, action, reason, acknowledged_alerts: acks,
    }),
    onSuccess: (_, action) => {
      void qc.invalidateQueries({ queryKey: ['encounter', encounterId] });
      toast.success(action === 'override' ? 'Override recorded - committing to the record' : 'Orders returned for revision');
    },
  });
  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-2 text-sm font-semibold text-fail"><ShieldAlert className="size-4" /> Medication Safety found {blocking.length} blocking alert(s) for these orders</p>
      <OrderSummary orders={cs.content.prescriptions ?? []} />
      {screen.alerts.map((a) => (
        <AlertCard key={a.id} alert={a}>
          {BLOCKING.includes(a.severity) && (
            <label className="mt-2 flex items-center gap-2 text-sm font-medium">
              <Checkbox checked={acks.includes(a.id)} onCheckedChange={(v) => setAcks((x) => v ? [...x, a.id] : x.filter((y) => y !== a.id))} />
              I have considered this alert
            </label>
          )}
        </AlertCard>
      ))}
      {screen.statement && <p className="text-xs text-fg-muted">{screen.statement}</p>}
      <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Clinical reason for overriding"
        placeholder="Clinical reason for proceeding despite the alert(s) (required to override; recorded and audited)" />
      <div className="flex flex-wrap gap-2">
        <Button variant="destructive" disabled={acks.length < blocking.length || reason.trim().length < 10 || decide.isPending}
          onClick={() => decide.mutate('override')}>Override and commit</Button>
        <Button variant="outline" disabled={decide.isPending} onClick={() => decide.mutate('revise')}>Revise the orders</Button>
      </div>
      <p className="text-xs text-fg-muted">An override goes straight to the record and is not screened again. Revised orders are screened again.</p>
      {decide.isError && <p role="alert" className="text-sm text-fail">{(decide.error as Error).message}</p>}
    </div>
  );
}

function OrdersPanel({ encounterId, h }: { encounterId: string; h: Handoff }) {
  const qc = useQueryClient();
  const doc = useDocumentation(encounterId);
  const [form, setForm] = useState<{ subjective?: string; assessment: string; plan: string }>({ assessment: '', plan: '' });
  const [findings, setFindings] = useState<string[] | null>(null);
  const [rx, setRx] = useState<RxRow[]>([]);
  const [prepared, setPrepared] = useState<{ changeset_id: string; content_sha256: string } | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const draft = doc.data?.draft;
  const suggested = useMemo(() => draft?.suggested_findings ?? [], [draft]);
  const chosen = findings ?? suggested.filter((x) => x.startsWith('intake:symptom') || x.startsWith('intake:medication') || x.startsWith('intake:allergy'));
  const prepare = useMutation({
    mutationFn: () => api.post<{ changeset_id: string; content_sha256: string }>(`/api/encounters/${encounterId}/documentation/prepare`, {
      subjective: form.subjective ?? draft?.subjective ?? '', objective: draft?.objective ?? '', assessment: form.assessment, plan: form.plan,
      record_findings: chosen,
      prescriptions: rx.filter((r) => r.medication_text.trim()).map((r) => ({ ...r, refills: 0, quantity_text: '' })),
    }),
    onSuccess: (r) => { setPrepared(r); setProblems([]); },
    onError: (e) => { setPrepared(null); if (e instanceof ApiError && e.detail) setProblems((e.detail.problems as string[]) ?? [e.message]); },
  });
  const submit = useMutation({
    mutationFn: () => api.post(`/api/encounters/${encounterId}/documentation/submit`, {
      changeset_id: prepared!.changeset_id, expected_sha256: prepared!.content_sha256,
    }),
    onSuccess: () => { setPrepared(null); void qc.invalidateQueries({ queryKey: ['encounter', encounterId] }); toast.success('Signed - Medication Safety is screening the orders'); },
  });
  if (doc.isPending) return <Loading />;
  const d = doc.data!;
  const cs = d.changeset;
  if (d.encounter_status === 'finalized') {
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p className="flex items-center gap-2 text-pass"><Check className="size-4" /> Committed to the record {cs?.committed_at ? <Time iso={cs.committed_at} withTime /> : null}. The encounter is finalized.</p>
        {cs?.safety_screen && <p className="flex items-center gap-2 text-fg-muted">{cs.safety_decision?.action === 'override'
          ? <><ShieldAlert className="size-4 text-fail" /> Committed with a doctor override: {cs.safety_decision.reason}</>
          : <><ShieldCheck className="size-4 text-pass" /> Orders screened by Medication Safety: no blocking alert.</>}</p>}
        <OrderSummary orders={cs?.content.prescriptions ?? []} />
      </div>
    );
  }
  if (d.run_waiting_gate === 'safety_review' && cs?.safety_screen) return <SafetyReview encounterId={encounterId} cs={cs} />;
  if (d.run_status === 'running' && ['order_safety_check', 'chart_commit', 'after_visit_summary'].includes(d.run_current_node ?? '')) {
    const label = { order_safety_check: 'Order Entry is asking Medication Safety to screen the orders', chart_commit: 'The Chart agent is writing the record',
                    after_visit_summary: "Writing the patient's after-visit summary" }[d.run_current_node as string];
    return <p className="flex items-center gap-2 text-sm text-fg-muted"><Loader2 className="size-4 animate-spin" /> {label}...</p>;
  }
  if (!draft || d.run_waiting_gate !== 'orders_review') return <p className="text-sm text-fg-muted">The note draft is being prepared by Order Entry...</p>;
  const label = (id: string) => h.evidence_bundle[id]?.label ?? id;
  const invalidate = () => setPrepared(null);
  const revised = cs?.safety_decision?.action === 'revise' ? cs : null;
  return (
    <div className="flex flex-col gap-4">
      {revised && (
        <div className="rounded-md border border-warn bg-warn-tint p-2 text-sm">
          <p className="font-medium">You chose to revise these orders after the safety check:</p>
          <OrderSummary orders={revised.content.prescriptions ?? []} />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="doc-subj">Subjective <span className="text-xs font-normal text-fg-muted">({draft.subjective_source === 'ai_draft' ? 'AI draft from the approved brief - edit as needed' : 'assembled from the approved brief'})</span></Label>
        <Textarea id="doc-subj" rows={4} value={form.subjective ?? draft.subjective} onChange={(e) => { invalidate(); setForm((f) => ({ ...f, subjective: e.target.value })); }} />
      </div>
      <div className="flex flex-col gap-1.5"><Label>Objective (recorded measurements only)</Label><p className="rounded-md border bg-surface-2 p-2 text-sm">{draft.objective}</p></div>
      <div className="flex flex-col gap-1.5"><Label htmlFor="doc-a">Assessment (doctor)</Label>
        <Textarea id="doc-a" rows={3} value={form.assessment} onChange={(e) => { invalidate(); setForm((f) => ({ ...f, assessment: e.target.value })); }} /></div>
      <div className="flex flex-col gap-1.5"><Label htmlFor="doc-p">Plan (doctor; the patient's summary is written from this)</Label>
        <Textarea id="doc-p" rows={3} value={form.plan} onChange={(e) => { invalidate(); setForm((f) => ({ ...f, plan: e.target.value })); }} /></div>
      <fieldset className="rounded-lg border p-3">
        <legend className="px-1 text-sm font-semibold">Add to the patient record (patient-reported today)</legend>
        <div className="flex flex-col gap-1.5">
          {suggested.map((id) => (
            <label key={id} className="flex items-center gap-2 text-sm">
              <Checkbox checked={chosen.includes(id)} onCheckedChange={(v) => { invalidate(); setFindings(v ? [...chosen, id] : chosen.filter((x) => x !== id)); }} />
              {label(id)} <span className="text-xs text-fg-muted">{id.split(':')[1]?.split('-')[0]}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="rounded-lg border p-3">
        <legend className="px-1 text-sm font-semibold">Proposed medication orders (doctor-entered; screened before saving)</legend>
        {rx.map((r, i) => (
          <div key={i} className="mb-2 grid gap-2 sm:grid-cols-[2fr_1fr_1fr_1.4fr_1fr_auto]">
            {(['medication_text', 'dose_text', 'route', 'frequency_text', 'duration_text'] as const).map((k) => (
              <Input key={k} placeholder={{ medication_text: 'Medicine (generic)', dose_text: 'Dose', route: 'Route', frequency_text: 'How often', duration_text: 'For how long' }[k]}
                aria-label={k} value={r[k]} onChange={(e) => { invalidate(); setRx((all) => all.map((x, j) => j === i ? { ...x, [k]: e.target.value } : x)); }} />
            ))}
            <Button variant="ghost" size="icon" aria-label="Remove order" onClick={() => { invalidate(); setRx((all) => all.filter((_, j) => j !== i)); }}><Trash2 /></Button>
            <Input className="sm:col-span-5" placeholder="Instructions for the patient (optional)" aria-label="instructions" value={r.instructions}
              onChange={(e) => { invalidate(); setRx((all) => all.map((x, j) => j === i ? { ...x, instructions: e.target.value } : x)); }} />
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={() => { invalidate(); setRx((all) => [...all, { ...EMPTY_RX }]); }}><Plus /> Add order</Button>
      </fieldset>
      {problems.length > 0 && <ul role="alert" className="list-disc pl-5 text-sm text-fail">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      {!prepared ? (
        <div><Button disabled={form.assessment.trim().length < 3 || form.plan.trim().length < 3 || prepare.isPending} onClick={() => prepare.mutate()}>Validate and preview</Button></div>
      ) : (
        <div className="rounded-lg border border-human bg-human-tint p-3">
          <p className="text-sm font-semibold">Ready to sign</p>
          <p className="text-sm">Signing sends the orders to Medication Safety. If nothing blocks them, the Chart agent writes one clinical note, {chosen.length} finding(s) and {rx.filter((r) => r.medication_text).length} order(s), and the encounter is finalized.</p>
          <p className="mt-1 font-mono text-[11px] text-fg-muted">change set {prepared.changeset_id.slice(0, 8)} - hash {prepared.content_sha256.slice(0, 16)}</p>
          <div className="mt-2 flex gap-2"><Button onClick={() => submit.mutate()} disabled={submit.isPending}>Sign and submit for safety check</Button>
            <Button variant="outline" onClick={() => setPrepared(null)}>Back to editing</Button></div>
          {submit.isError && <p role="alert" className="mt-2 text-sm text-fail">{(submit.error as Error).message}</p>}
        </div>
      )}
    </div>
  );
}

export function AvsDocument({ avs }: { avs: AfterVisitSummary }) {
  const c = avs.content;
  return (
    <article className="avs-print flex flex-col gap-4 text-[15px] leading-7" aria-label="After-visit summary">
      <header className="border-b pb-2">
        <p className="text-xs uppercase tracking-wide text-fg-muted">After-visit summary - {c.visit.location}</p>
        <h2 className="text-xl font-semibold">{c.patient.name}</h2>
        <p className="text-sm text-fg-muted">Date of birth {c.patient.birth_date} - MRN {c.patient.mrn} - Visit {c.visit.date}{c.visit.clinician ? ` with ${c.visit.clinician}` : ''}</p>
      </header>
      <section><h3 className="font-semibold">Why you came in</h3><p>"{c.reason_for_visit.text}" <span className="text-xs text-fg-muted">({c.reason_for_visit.source})</span></p></section>
      <section><h3 className="font-semibold">Your medicines from this visit</h3>
        {c.no_new_medicines ? <p>No new medicines were prescribed at this visit.</p> : (
          <ul className="flex flex-col gap-2">{c.medicines.map((m, i) => (
            <li key={i} className="rounded-md border p-2"><b>{m.name}</b> - {m.dose}<br />{m.plain}{m.instructions ? <><br />{m.instructions}</> : null}</li>))}</ul>)}
      </section>
      <section><h3 className="font-semibold">What happens next</h3><p className="whitespace-pre-wrap">{c.next_steps.text}</p>
        <p className="text-xs text-fg-muted">{c.next_steps.source === 'plain_language_from_doctor_plan' ? "Your doctor's plan, in plain language (checked against the plan)." : "Your doctor's plan."}</p></section>
      <section className="rounded-md border border-fail bg-fail-tint p-2"><h3 className="font-semibold">When to get help</h3><p>{c.when_to_get_help.text}</p></section>
      <footer className="text-xs text-fg-muted">Synthetic demonstration record. {avs.status === 'released' ? `Released by your doctor ${avs.released_at ? fmtDate(avs.released_at, true) : ''}.` : 'Draft - not yet released by your doctor.'}</footer>
    </article>
  );
}

function AvsPanel({ encounterId }: { encounterId: string }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useAfterVisitSummary(encounterId);
  const release = useMutation({
    mutationFn: (avs: AfterVisitSummary) => api.post(`/api/encounters/${encounterId}/after-visit-summary/release`, { avs_id: avs.id, expected_sha256: avs.content_sha256 }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['encounter', encounterId, 'avs'] }); toast.success('Released to the patient'); },
  });
  if (q.isPending) return <Loading />;
  const avs = q.data?.avs;
  if (!avs) return <p className="flex items-center gap-2 text-sm text-fg-muted"><Loader2 className="size-4 animate-spin" /> The After-Visit Summary agent is writing the patient's summary...</p>;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={avs.status} />
        <Link to="/summary/$encounterId" params={{ encounterId }} className="inline-flex items-center gap-1 text-sm"><FileText className="size-4" /> Open printable summary</Link></div>
      <div className="max-h-80 overflow-y-auto rounded-md border p-3"><AvsDocument avs={avs} /></div>
      {avs.status === 'draft' && can('note.approve') && (
        <Button onClick={() => release.mutate(avs)} disabled={release.isPending}><Printer /> Release to the patient</Button>
      )}
      {release.isError && <p role="alert" className="text-sm text-fail">{(release.error as Error).message}</p>}
    </div>
  );
}

const GROUP_TITLE: Record<SbarGroup, string> = { situation: 'S - Situation', background: 'B - Background', assessment: 'A - Assessment support',
  recommendation: 'R - For the doctor to clarify', governance: 'Checks and sources' };

export function HandoffPage() {
  const { encounterId } = useParams({ from: '/handoffs/$encounterId' });
  const { can } = useAuth();
  const enc = useEncounter(encounterId);
  const [version, setVersion] = useState<number | undefined>();
  const h = useHandoff(encounterId);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  if (h.isPending || enc.isPending) return <Loading label="Loading handoff" />;
  if (h.isError) return <ErrorState error={h.error} retry={() => h.refetch()} />;
  const d = h.data;
  const reviewable = ['in_review', 'changes_requested', 'draft'].includes(d.status) && enc.data?.latest_run?.waiting_gate === 'handoff_review';
  const alerts = alertsFrom(d);
  const shown = version && version !== d.version ? null : d;
  return (
    <>
      <PageHeader crumbs={<><Link to="/handoffs">Handoffs</Link> / <Link to="/encounters/$encounterId" params={{ encounterId }}>Encounter</Link></>}
        title={`Handoff brief - ${enc.data?.patient.display_name ?? ''}`}
        subtitle={<>Version {d.version} - {d.author_kind === 'ai' ? 'AI-generated draft' : 'edited by a doctor'} - {d.model ?? 'no model'} - created {fmtDate(d.created_at, true)}</>}
        actions={<><StatusBadge status={d.status} />{can('handoff.edit') && reviewable && !editing && <Button variant="outline" size="sm" onClick={() => setEditing(true)}><Pencil /> Edit</Button>}</>} />
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <RolePill role="warn">Named clinical handoff (not anonymized)</RolePill>
        <RolePill role="run">AI-assisted - requires doctor review</RolePill>
        <RolePill role="neutral">Synthetic data</RolePill>
        {(d.validation.privacy_removed ?? []).length > 0 && <RolePill role="pass">Identifiers removed: {d.validation.privacy_removed!.map((x) => `${x.kind} ${x.count}`).join(', ')}</RolePill>}
      </div>
      {d.content.notice && <p className="mb-3 rounded-md border border-warn bg-warn-tint p-2 text-sm">{d.content.notice}</p>}
      {d.content.escalations?.map((e) => (
        <p key={e.rule_id} className={`mb-2 flex items-center gap-2 rounded-md border p-2 text-sm ${e.level === 'urgent' ? 'border-fail bg-fail-tint' : 'border-warn bg-warn-tint'}`}>
          <Siren className="size-4" /> <b>{e.label}:</b> {e.message}
        </p>
      ))}
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Panel>
          {editing ? <EditForm h={d} onDone={() => setEditing(false)} /> : shown ? (
            <div className="flex flex-col">
              {d.content.sections.map((s, i) => (
                <div key={s.key}>
                  {s.group && s.group !== d.content.sections[i - 1]?.group && (
                    <p className={cn('mb-1 mt-4 rounded px-2 py-1 text-[11px] font-semibold uppercase tracking-wide first:mt-0',
                      s.group === 'governance' ? 'bg-surface-2 text-fg-muted' : 'bg-brand-tint text-brand')}>{GROUP_TITLE[s.group]}</p>
                  )}
                  <section className="border-b py-3 last:border-0" aria-labelledby={`sec-${s.key}`}>
                    <h2 id={`sec-${s.key}`} className="mb-1 text-sm font-semibold">{s.title}</h2>
                    <SectionView section={s} onOpen={setEvidence} alerts={s.key === 'medication_alerts' ? alerts : []} />
                  </section>
                </div>
              ))}
            </div>
          ) : <EmptyState title="Older version selected" body="Use the version list to return to the current version." />}
        </Panel>
        <div className="flex flex-col gap-4">
          {can('handoff.approve') && reviewable && !editing && <Panel title="Your decision" tone="human"><DecisionPanel h={d} encounterId={encounterId} /></Panel>}
          {!can('handoff.approve') && reviewable && <Panel title="Awaiting doctor review"><p className="text-sm text-fg-muted">A doctor must review and approve this handoff.</p></Panel>}
          {d.status === 'approved' && can('note.write') && <Panel title="Note and orders" subtitle="Order Entry (CPOE): your orders are screened by Medication Safety before anything is written." tone="human"><OrdersPanel encounterId={encounterId} h={d} /></Panel>}
          {enc.data?.status === 'finalized' && <Panel title="Patient's after-visit summary" subtitle="This visit only: reason, medicines and how to take them, next steps."><AvsPanel encounterId={encounterId} /></Panel>}
          <Panel title="Versions">
            <ul className="flex flex-col gap-1.5 text-sm">
              {d.versions.slice().reverse().map((v) => (
                <li key={v.version}>
                  <button type="button" onClick={() => setVersion(v.version)} className={cn('flex w-full justify-between gap-2 rounded px-1 text-left hover:bg-surface-2', v.version === d.version && 'font-semibold')}>
                    <span>v{v.version} - {v.author_kind === 'ai' ? 'AI' : 'doctor edit'}{v.change_summary ? `: ${v.change_summary}` : ''}</span>
                    <span className="text-xs text-fg-muted"><Time iso={v.created_at} mode="rel" /></span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
      <EvidenceDrawer id={evidence} item={evidence ? d.evidence_bundle[evidence] : undefined} onClose={() => setEvidence(null)} />
    </>
  );
}
