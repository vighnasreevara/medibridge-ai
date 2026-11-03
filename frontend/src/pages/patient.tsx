import { Link, useNavigate, useParams } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { FileText } from 'lucide-react';
import { useMemo, useState } from 'react';

import {
  EmptyState, ErrorState, Kpi, Loading, PageHeader, Panel, RolePill, SourceBadge, StatusBadge, Time, fmtDate,
} from '@/components/common';
import { DataTable } from '@/components/data-table';
import { ResultsTab } from '@/components/results';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';
import {
  useAllergies, useConditions, useDocuments, useMedications, useNote, useNotes, usePatient, useTimeline, useVitals,
} from '@/lib/queries';
import type { AllergyView, ConditionView, DocumentView, MedicationView, NoteView, TimelineItem, VitalView } from '@/lib/types';

function Section<T>({ q, children }: { q: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown; data?: T }; children: (data: T) => React.ReactNode }) {
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  return <>{children(q.data as T)}</>;
}

function TimelineTab({ id }: { id: string }) {
  const navigate = useNavigate();
  const q = useTimeline(id);
  const columns = useMemo<ColumnDef<TimelineItem>[]>(() => [
    { accessorKey: 'started_at', header: 'Date', cell: ({ getValue }) => <Time iso={getValue<string>()} withTime /> },
    { accessorKey: 'chief_complaint', header: 'Reason', cell: ({ getValue }) => getValue<string>() || <span className="text-fg-subtle">Not recorded</span> },
    { accessorKey: 'encounter_class', header: 'Type', cell: ({ getValue }) => <span className="capitalize">{getValue<string>()}</span> },
    { accessorKey: 'attending_name', header: 'Attending' },
    { id: 'artifacts', header: 'Notes / docs', enableSorting: false, cell: ({ row }) => <span className="tnum">{row.original.note_count} / {row.original.document_count}</span> },
    { accessorKey: 'status', header: 'Status', cell: ({ getValue }) => <StatusBadge status={getValue<string>()} /> },
  ], []);
  return (
    <Section q={q}>{(d: { items: TimelineItem[] }) => (
      <DataTable label="Encounter timeline" data={d.items} columns={columns} rowKey={(r) => r.id}
        onRowClick={(r) => navigate({ to: '/encounters/$encounterId', params: { encounterId: r.id } })}
        initialSort={[{ id: 'started_at', desc: true }]} empty={<EmptyState title="No encounters on record" />}
        mobileCard={(r) => (
          <div className="flex items-start justify-between gap-2">
            <div><p className="font-medium">{r.chief_complaint || 'Encounter'}</p><p className="text-xs text-fg-muted">{fmtDate(r.started_at)} - {r.attending_name}</p></div>
            <StatusBadge status={r.status} />
          </div>
        )} />
    )}</Section>
  );
}

function ConditionsTab({ id }: { id: string }) {
  const q = useConditions(id);
  const columns = useMemo<ColumnDef<ConditionView>[]>(() => [
    { accessorKey: 'name', header: 'Condition', cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
    { accessorKey: 'clinical_status', header: 'Status', cell: ({ getValue }) => <span className="capitalize">{getValue<string>()}</span> },
    { accessorKey: 'verification_status', header: 'Verification', cell: ({ getValue }) => <span className="capitalize">{getValue<string>()}</span> },
    { accessorKey: 'onset_date', header: 'Onset', cell: ({ getValue }) => <Time iso={getValue<string>()} /> },
    { id: 'source', header: 'Source', enableSorting: false, cell: ({ row }) => <SourceBadge kind={row.original.source_kind} certainty={row.original.certainty} /> },
  ], []);
  return <Section q={q}>{(d: { items: ConditionView[] }) => (
    <DataTable label="Conditions" data={d.items} columns={columns} rowKey={(r) => r.id} empty={<EmptyState title="No conditions recorded" body="This means none are recorded, not that the patient has none." />} />
  )}</Section>;
}

function MedicationsTab({ id }: { id: string }) {
  const q = useMedications(id);
  const columns = useMemo<ColumnDef<MedicationView>[]>(() => [
    { accessorKey: 'medication_text', header: 'Medication', cell: ({ row }) => (
      <div><p className="font-medium">{row.original.medication_text}</p>
        <p className="text-xs text-fg-muted">{[row.original.dose_text, row.original.route, row.original.frequency_text].filter(Boolean).join(' - ') || 'Dose not recorded'}</p></div>
    ) },
    { accessorKey: 'category', header: 'Current?', cell: ({ getValue }) => {
      const v = getValue<string>();
      return v === 'current' ? <RolePill role="pass">Current</RolePill> : v === 'historical' ? <RolePill role="neutral">Historical</RolePill> : <RolePill role="tool">Status unknown</RolePill>;
    } },
    { accessorKey: 'reconciliation', header: 'Reconciled', cell: ({ row }) => (
      <div className="flex flex-col gap-0.5"><StatusBadge status={row.original.reconciliation} />
        {row.original.last_reconciled_at && <span className="text-xs text-fg-muted"><Time iso={row.original.last_reconciled_at} /></span>}</div>
    ) },
    { id: 'source', header: 'Source', enableSorting: false, cell: ({ row }) => <SourceBadge kind={row.original.source_kind} certainty={row.original.certainty} /> },
  ], []);
  return <Section q={q}>{(d: { items: MedicationView[]; summary: { current: number; historical: number; status_unknown: number; uncertain: number; reconciliation: string } }) => (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <RolePill role="pass">{d.summary.current} current</RolePill>
        <RolePill role="neutral">{d.summary.historical} historical</RolePill>
        {d.summary.status_unknown > 0 && <RolePill role="tool">{d.summary.status_unknown} status unknown</RolePill>}
        {d.summary.uncertain > 0 && <RolePill role="tool">{d.summary.uncertain} uncertain</RolePill>}
        <span className="text-fg-muted">List reconciliation:</span> <StatusBadge status={d.summary.reconciliation} />
      </div>
      <DataTable label="Medications" data={d.items} columns={columns} rowKey={(r) => r.id} empty={<EmptyState title="No medications recorded" body="An empty list is not confirmation that the patient takes no medications." />} />
    </div>
  )}</Section>;
}

function AllergiesTab({ id }: { id: string }) {
  const q = useAllergies(id);
  const columns = useMemo<ColumnDef<AllergyView>[]>(() => [
    { accessorKey: 'substance_text', header: 'Substance', cell: ({ row }) => <span className="font-medium capitalize">{row.original.substance_text}</span> },
    { accessorKey: 'reaction_text', header: 'Reaction' },
    { accessorKey: 'criticality', header: 'Criticality', cell: ({ getValue }) => {
      const v = getValue<string>();
      return v === 'high' ? <RolePill role="fail">High</RolePill> : <span className="capitalize">{v.replace(/_/g, ' ')}</span>;
    } },
    { accessorKey: 'verification_status', header: 'Verification', cell: ({ getValue }) => {
      const v = getValue<string>();
      return v === 'confirmed' ? <RolePill role="pass">Confirmed</RolePill> : <RolePill role="tool" className="capitalize">{v}</RolePill>;
    } },
    { accessorKey: 'recorded_at', header: 'Recorded', cell: ({ getValue }) => <Time iso={getValue<string>()} /> },
    { id: 'source', header: 'Source', enableSorting: false, cell: ({ row }) => <SourceBadge kind={row.original.source_kind} certainty={row.original.certainty} /> },
  ], []);
  return <Section q={q}>{(d: { items: AllergyView[] }) => (
    <DataTable label="Allergies" data={d.items} columns={columns} rowKey={(r) => r.id} empty={<EmptyState title="No allergies recorded" body="No recorded allergies is different from a documented 'no known allergies'." />} />
  )}</Section>;
}

function NotesTab({ id }: { id: string }) {
  const q = useNotes(id);
  const [open, setOpen] = useState<string | null>(null);
  const note = useNote(id, open);
  const columns = useMemo<ColumnDef<NoteView>[]>(() => [
    { accessorKey: 'created_at', header: 'Date', cell: ({ getValue }) => <Time iso={getValue<string>()} /> },
    { accessorKey: 'title', header: 'Title', cell: ({ row }) => <span className="font-medium">{row.original.title || row.original.note_type.replace(/_/g, ' ')}</span> },
    { accessorKey: 'author_name', header: 'Author' },
    { accessorKey: 'status', header: 'Status', cell: ({ getValue }) => <StatusBadge status={getValue<string>() === 'final' ? 'finalized' : getValue<string>()} label={getValue<string>()} /> },
  ], []);
  return (
    <>
      <Section q={q}>{(d: { items: NoteView[] }) => (
        <DataTable label="Clinical notes" data={d.items} columns={columns} rowKey={(r) => r.id} onRowClick={(r) => setOpen(r.id)}
          initialSort={[{ id: 'created_at', desc: true }]} empty={<EmptyState title="No clinical notes" />} />
      )}</Section>
      <Sheet open={!!open} onOpenChange={(v) => !v && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{note.data?.title || 'Clinical note'}</SheetTitle>
            <SheetDescription>{note.data ? <>{note.data.author_name} - <Time iso={note.data.created_at} withTime /> - version {note.data.current_version}</> : 'Loading'}</SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-6">
            {note.isPending ? <Loading /> : note.isError ? <ErrorState error={note.error} /> : (
              <>
                <pre className="rounded-lg border bg-surface-2 p-3 text-sm leading-6 whitespace-pre-wrap font-sans">{note.data.body}</pre>
                <p className="mt-2 font-mono text-[11px] text-fg-subtle">{note.data.source_ref}</p>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

function DocumentsTab({ id }: { id: string }) {
  const q = useDocuments(id);
  const columns = useMemo<ColumnDef<DocumentView>[]>(() => [
    { accessorKey: 'clinical_date', header: 'Date', cell: ({ getValue }) => <Time iso={getValue<string>()} /> },
    { accessorKey: 'title', header: 'Document', cell: ({ row }) => (
      <span className="inline-flex items-center gap-1.5 font-medium"><FileText className="size-3.5" /> {row.original.title}</span>
    ) },
    { accessorKey: 'document_type', header: 'Type', cell: ({ getValue }) => <span className="capitalize">{getValue<string>().replace(/_/g, ' ')}</span> },
    { accessorKey: 'source_name', header: 'Source' },
    { accessorKey: 'ingestion_status', header: 'Search index', cell: ({ getValue }) => <StatusBadge status={getValue<string>()} /> },
  ], []);
  return <Section q={q}>{(d: { items: DocumentView[] }) => (
    <DataTable label="Documents" data={d.items} columns={columns} rowKey={(r) => r.id} initialSort={[{ id: 'clinical_date', desc: true }]}
      empty={<EmptyState title="No documents on record" body="No documents is not the same as normal findings." />} />
  )}</Section>;
}

const VITAL_LABEL: Record<string, string> = {
  heart_rate: 'Heart rate', systolic_bp: 'Systolic BP', diastolic_bp: 'Diastolic BP', respiratory_rate: 'Respiratory rate',
  temperature: 'Temperature', spo2: 'SpO2', weight: 'Weight', height: 'Height', pain_score: 'Pain score',
};

function VitalsTab({ id }: { id: string }) {
  const q = useVitals(id);
  const columns = useMemo<ColumnDef<VitalView>[]>(() => [
    { accessorKey: 'measured_at', header: 'Measured', cell: ({ getValue }) => <Time iso={getValue<string>()} withTime /> },
    { accessorKey: 'kind', header: 'Measure', cell: ({ getValue }) => VITAL_LABEL[getValue<string>()] ?? getValue<string>() },
    { accessorKey: 'value', header: 'Value', cell: ({ row }) => <span className="tnum">{row.original.value} {row.original.unit}</span> },
  ], []);
  return <Section q={q}>{(d: { items: VitalView[] }) => (
    <DataTable label="Vital signs" data={d.items} columns={columns} rowKey={(r) => r.id} initialSort={[{ id: 'measured_at', desc: true }]}
      empty={<EmptyState title="No vital signs recorded" body="Vital signs appear only when recorded by an authorized clinician." />} />
  )}</Section>;
}

export function PatientPage() {
  const { patientId } = useParams({ from: '/patients/$patientId' });
  const { can } = useAuth();
  const p = usePatient(patientId);
  if (p.isPending) return <Loading />;
  if (p.isError) return <ErrorState error={p.error} retry={() => p.refetch()} />;
  const d = p.data;
  return (
    <>
      <PageHeader
        crumbs={<Link to="/patients">Patients</Link>}
        title={d.display_name}
        subtitle={<span className="tnum">MRN {d.mrn} - born {fmtDate(d.birth_date)} ({d.age}) - <span className="capitalize">{d.sex_at_birth}</span></span>}
        actions={d.is_synthetic && <RolePill role="warn">Synthetic patient</RolePill>}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Encounters" value={d.counts.encounters} />
        <Kpi label="Active conditions" value={d.counts.active_conditions} />
        <Kpi label="Allergies recorded" value={d.counts.allergies} tone={d.counts.allergies ? 'warn' : undefined} />
        <Kpi label="Documents" value={d.counts.documents} />
      </div>
      <Panel>
        <Tabs defaultValue="timeline">
          <TabsList className="mb-3 flex h-auto flex-wrap justify-start">
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="results">Results</TabsTrigger>
            <TabsTrigger value="conditions">Conditions</TabsTrigger>
            {can('medication.read') && <TabsTrigger value="medications">Medications</TabsTrigger>}
            {can('medication.read') && <TabsTrigger value="allergies">Allergies</TabsTrigger>}
            <TabsTrigger value="notes">Notes</TabsTrigger>
            {can('document.read') && <TabsTrigger value="documents">Documents</TabsTrigger>}
            <TabsTrigger value="vitals">Vitals</TabsTrigger>
          </TabsList>
          <TabsContent value="timeline"><TimelineTab id={patientId} /></TabsContent>
          <TabsContent value="results"><ResultsTab id={patientId} /></TabsContent>
          <TabsContent value="conditions"><ConditionsTab id={patientId} /></TabsContent>
          <TabsContent value="medications"><MedicationsTab id={patientId} /></TabsContent>
          <TabsContent value="allergies"><AllergiesTab id={patientId} /></TabsContent>
          <TabsContent value="notes"><NotesTab id={patientId} /></TabsContent>
          <TabsContent value="documents"><DocumentsTab id={patientId} /></TabsContent>
          <TabsContent value="vitals"><VitalsTab id={patientId} /></TabsContent>
        </Tabs>
      </Panel>
    </>
  );
}
