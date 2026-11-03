import type { Edge, Node } from '@xyflow/react';
import { useMemo, useState } from 'react';

import { AlertCard, PatientPicker } from '@/components/clinical';
import { EmptyState, ErrorState, Loading, PageHeader, Panel, RolePill, SourceBadge, StatusBadge } from '@/components/common';
import { FlowCanvas, arrow, layout } from '@/components/flow';
import { useAllergies, useMedicationScreen, useMedications } from '@/lib/queries';
import type { MedicationScreen } from '@/lib/types';

const SEVERITY_COLOR: Record<string, string> = { contraindicated: 'var(--fail)', major: 'var(--fail)', moderate: 'var(--warn)', minor: 'var(--neutral)' };

function DrugGraph({ graph }: { graph: MedicationScreen['graph'] }) {
  const { nodes, edges } = useMemo(() => {
    const edges: Edge[] = graph.edges.map((e, i) => ({
      id: `e${i}`, source: e.source, target: e.target, markerEnd: e.kind === 'INTERACTS_WITH' ? undefined : arrow,
      label: e.kind === 'INTERACTS_WITH' ? e.severity : undefined, animated: false,
      style: e.kind === 'INTERACTS_WITH' ? { stroke: SEVERITY_COLOR[e.severity ?? ''] ?? 'var(--warn)', strokeWidth: 2.5 }
        : { stroke: 'var(--line-strong)', strokeDasharray: '4 3' },
      labelStyle: { fill: 'var(--fg)', fontSize: 11, fontWeight: 600 },
      labelBgStyle: { fill: 'var(--surface)' },
    }));
    const nodes: Node[] = graph.nodes.map((n) => ({
      id: n.id, data: { label: n.label }, position: { x: 0, y: 0 }, width: n.kind === 'drug' ? 140 : 190, height: 40,
      style: n.kind === 'drug'
        ? { background: 'var(--brand-tint)', border: '1.5px solid var(--brand)', color: 'var(--fg)', borderRadius: 8, fontSize: 12, fontWeight: 600 }
        : { background: 'var(--surface)', border: '1px dashed var(--line-strong)', color: 'var(--fg-muted)', borderRadius: 8, fontSize: 11 },
    }));
    return { nodes: layout(nodes, edges, 'LR'), edges };
  }, [graph]);
  if (!graph.nodes.length) return <EmptyState title="No identified medications to draw" />;
  return (
    <>
      <FlowCanvas nodes={nodes} edges={edges} label="Drug relationship graph: medications, their pharmacologic classes and interactions" height={380} />
      <details className="mt-2 text-sm"><summary className="cursor-pointer text-fg-muted">List view of relationships</summary>
        <ul className="mt-1 list-disc pl-5">{graph.edges.map((e, i) => {
          const name = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
          return <li key={i}>{name(e.source)} {e.kind === 'INTERACTS_WITH' ? `interacts with (${e.severity})` : 'belongs to class'} {name(e.target)}</li>;
        })}</ul></details>
    </>
  );
}

function Screen({ patientId }: { patientId: string }) {
  const screen = useMedicationScreen(patientId);
  const meds = useMedications(patientId);
  const allergies = useAllergies(patientId);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Recorded medications" subtitle={meds.data ? <>List reconciliation: <StatusBadge status={meds.data.summary.reconciliation} /></> : undefined}>
          {meds.isPending ? <Loading /> : meds.isError ? <ErrorState error={meds.error} /> : !meds.data.items.length ? <EmptyState title="No medications recorded" body="Not the same as 'takes no medications'." /> : (
            <ul className="flex flex-col gap-2">{meds.data.items.map((m) => {
              const res = screen.data?.medications.find((x) => x.ref === `medication_statement:${m.id}`)?.resolution;
              return (
                <li key={m.id} className="rounded-md border p-2.5 text-sm">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{m.medication_text}</span>
                    <RolePill role={m.category === 'current' ? 'pass' : m.category === 'historical' ? 'neutral' : 'tool'}>{m.category.replace('_', ' ')}</RolePill>
                    <SourceBadge kind={m.source_kind} certainty={m.certainty} />
                    {res && <RolePill role={res.match_type === 'suggestion' ? 'tool' : res.rxcui ? 'run' : 'warn'}>
                      {res.rxcui ? `RxNorm ${res.rxcui}${res.match_type === 'suggestion' ? ' (similarity)' : ''}` : 'not identified'}</RolePill>}</div>
                  <p className="text-xs text-fg-muted">{[m.dose_text, m.route, m.frequency_text].filter(Boolean).join(' - ')} - reconciliation {m.reconciliation.replace('_', ' ')}</p>
                </li>);
            })}</ul>
          )}
        </Panel>
        <Panel title="Allergy history">
          {allergies.isPending ? <Loading /> : allergies.isError ? <ErrorState error={allergies.error} /> : !allergies.data.items.length ? <EmptyState title="No allergies recorded" /> : (
            <ul className="flex flex-col gap-2">{allergies.data.items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2.5 text-sm">
                <span className="font-medium capitalize">{a.substance_text}</span><span className="text-fg-muted">{a.reaction_text}</span>
                <RolePill role={a.verification_status === 'confirmed' ? 'pass' : 'tool'}>{a.verification_status}</RolePill>
                {a.criticality === 'high' && <RolePill role="fail">high criticality</RolePill>}
                <SourceBadge kind={a.source_kind} /></li>))}</ul>
          )}
        </Panel>
      </div>
      <Panel title="Evidence-backed safety screen" subtitle={screen.data ? `Knowledge release ${screen.data.release}` : undefined}>
        {screen.isPending ? <Loading label="Screening" /> : screen.isError ? <ErrorState error={screen.error} retry={() => screen.refetch()} /> : (
          <div className="flex flex-col gap-3">
            {screen.data.alerts.length ? screen.data.alerts.map((a) => <AlertCard key={a.kind + a.id + a.patient_facts.map((f) => f.text).join()} alert={a} />)
              : <p className="text-sm">No alert was found among the identified medications.</p>}
            <div className="rounded-md border bg-surface-2 p-3 text-sm">
              <p className="font-medium">{screen.data.statement}</p>
              <p className="mt-1 text-xs text-fg-muted">Completeness: {screen.data.completeness.status.replace('_', ' ')}</p>
              <ul className="mt-1 list-disc pl-5 text-xs text-fg-muted">{screen.data.completeness.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
          </div>
        )}
      </Panel>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Panel title="Drug relationships" subtitle="Identified medications, FDA pharmacologic classes and curated interactions.">
          {screen.data ? <DrugGraph graph={screen.data.graph} /> : <Loading />}
        </Panel>
        <Panel title="Reference sources">
          {screen.data && (
            <ul className="flex flex-col gap-2 text-sm">{screen.data.sources.map((s) => (
              <li key={s.id}><a href={s.url} target="_blank" rel="noreferrer noopener" className="font-medium">{s.name}</a>
                <p className="text-xs text-fg-muted">Version {s.version} - {s.license}</p></li>))}
              <li className="text-xs text-fg-muted">Interaction edges are a small demo-curated set; each restates a sentence verified verbatim in the cited FDA label. Not a comprehensive interaction database and not clinically reviewed.</li>
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

export function MedicationPage() {
  const [patient, setPatient] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="Medication & safety" subtitle="Deterministic screening against versioned public reference data. Supports clinical judgment; it does not prescribe."
        actions={<PatientPicker value={patient} onChange={setPatient} />} />
      {patient ? <Screen patientId={patient} /> : <Panel><EmptyState title="Choose a patient" /></Panel>}
    </>
  );
}
