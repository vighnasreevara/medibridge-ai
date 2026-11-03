import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { EmptyState, ErrorState, Loading, PageHeader, Panel, StatusBadge, Time } from '@/components/common';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { api, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { todayIso, usePatientSearch, useStaff, useWorklist } from '@/lib/queries';
import type { EncounterDetail, PatientSummary } from '@/lib/types';

export function NewEncounterDialog({ open, onOpenChange, initialPatient }: { open: boolean; onOpenChange: (v: boolean) => void; initialPatient?: PatientSummary }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [patient, setPatient] = useState<PatientSummary | undefined>(initialPatient);
  const [attending, setAttending] = useState<string>('');
  const [cls, setCls] = useState('ambulatory');
  const [complaint, setComplaint] = useState('');
  const search = usePatientSearch(query.trim(), 'all');
  const doctors = useStaff('doctor');
  const create = useMutation({
    mutationFn: () => api.post<EncounterDetail>('/api/encounters', {
      patient_id: patient!.id, attending_user_id: attending, encounter_class: cls, chief_complaint: complaint,
    }),
    onSuccess: (e) => {
      void qc.invalidateQueries({ queryKey: ['worklist'] });
      void qc.invalidateQueries({ queryKey: ['overview'] });
      onOpenChange(false);
      toast.success('Encounter created', { description: 'Verify identity and record consent before intake.' });
      void navigate({ to: '/encounters/$encounterId', params: { encounterId: e.id } });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === 'open_encounter_exists') {
        toast.info('This patient already has an open encounter today');
        onOpenChange(false);
        void navigate({ to: '/encounters/$encounterId', params: { encounterId: String(err.detail?.encounter_id) } });
      }
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New encounter</DialogTitle>
          <DialogDescription>Select the patient from the registry. Identity is verified with two identifiers in the next step.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="enc-patient">Patient</Label>
            {patient ? (
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span><span className="font-medium">{patient.display_name}</span> <span className="text-xs text-fg-muted">{patient.mrn} - {patient.birth_date}</span></span>
                <Button variant="ghost" size="sm" onClick={() => setPatient(undefined)}>Change</Button>
              </div>
            ) : (
              <>
                <Input id="enc-patient" placeholder="Search name, MRN or DOB" value={query} onChange={(e) => setQuery(e.target.value)} />
                {query.trim().length >= 2 && (
                  <ul className="max-h-48 overflow-y-auto rounded-md border">
                    {search.data?.items.map((p) => (
                      <li key={p.id}>
                        <button type="button" className="w-full px-3 py-1.5 text-left text-sm hover:bg-surface-2" onClick={() => setPatient(p)}>
                          {p.display_name} <span className="text-xs text-fg-muted">{p.mrn} - {p.birth_date}</span>
                        </button>
                      </li>
                    ))}
                    {search.data && !search.data.items.length && <li className="px-3 py-2 text-sm text-fg-muted">No matches</li>}
                  </ul>
                )}
              </>
            )}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>Attending doctor</Label>
              <Select value={attending} onValueChange={setAttending}>
                <SelectTrigger aria-label="Attending doctor"><SelectValue placeholder="Choose" /></SelectTrigger>
                <SelectContent>
                  {doctors.data?.items.map((d) => <SelectItem key={d.id} value={d.id}>{d.display_name}{d.specialty ? ` - ${d.specialty}` : ''}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Encounter type</Label>
              <Select value={cls} onValueChange={setCls}>
                <SelectTrigger aria-label="Encounter type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['ambulatory', 'emergency', 'inpatient', 'virtual'].map((c) => <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="enc-complaint">Reason for visit (as stated at the desk)</Label>
            <Textarea id="enc-complaint" maxLength={300} value={complaint} onChange={(e) => setComplaint(e.target.value)} />
          </div>
          {create.isError && !(create.error instanceof ApiError && create.error.code === 'open_encounter_exists') && (
            <p role="alert" className="text-sm text-fail">{(create.error as Error).message}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!patient || !attending || create.isPending} onClick={() => create.mutate()}>Create encounter</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EncountersPage() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [view, setView] = useState<'today' | 'open' | 'all'>('today');
  const [creating, setCreating] = useState(false);
  const params = view === 'today' ? { day: todayIso(), scope: 'all' as const }
    : view === 'open' ? { status: 'planned,in_progress,awaiting_review', scope: 'all' as const } : { scope: 'all' as const };
  const q = useWorklist(params);
  const columns = useMemo<ColumnDef<EncounterDetail>[]>(() => [
    { accessorKey: 'started_at', header: 'Time', cell: ({ getValue }) => <Time iso={getValue<string>()} withTime /> },
    { id: 'patient', accessorFn: (r) => r.patient.display_name, header: 'Patient', cell: ({ row }) => (
      <div><p className="font-medium">{row.original.patient.display_name}</p><p className="text-xs text-fg-muted">{row.original.patient.mrn}</p></div>
    ) },
    { accessorKey: 'chief_complaint', header: 'Reason' },
    { accessorKey: 'attending_name', header: 'Attending' },
    { id: 'checks', header: 'Ready for intake', enableSorting: false, cell: ({ row }) => {
      const e = row.original;
      const consent = e.consent.audio_recording?.status === 'granted' && e.consent.ai_processing?.status === 'granted';
      return <div className="flex flex-col gap-0.5 text-xs">
        <span className={e.identity_verified ? 'text-pass' : 'text-fg-muted'}>{e.identity_verified ? 'Identity verified' : 'Identity not verified'}</span>
        <span className={consent ? 'text-pass' : 'text-fg-muted'}>{consent ? 'Consent recorded' : 'Consent not recorded'}</span>
      </div>;
    } },
    { accessorKey: 'status', header: 'Status', cell: ({ getValue }) => <StatusBadge status={getValue<string>()} /> },
  ], []);
  return (
    <>
      <PageHeader title="Encounters" subtitle="Patients in your care team."
        actions={can('encounter.create') && <Button onClick={() => setCreating(true)}><Plus /> New encounter</Button>} />
      <Panel>
        <Tabs value={view} onValueChange={(v) => setView(v as typeof view)} className="mb-3">
          <TabsList><TabsTrigger value="today">Today</TabsTrigger><TabsTrigger value="open">Open</TabsTrigger><TabsTrigger value="all">All</TabsTrigger></TabsList>
        </Tabs>
        {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
          <DataTable label="Encounters" data={q.data.items} columns={columns} rowKey={(r) => r.id}
            onRowClick={(r) => navigate({ to: '/encounters/$encounterId', params: { encounterId: r.id } })}
            initialSort={[{ id: 'started_at', desc: view === 'all' }]}
            empty={<EmptyState title="No encounters in this view" />}
            mobileCard={(e) => (
              <div className="flex items-start justify-between gap-2">
                <div><p className="font-medium">{e.patient.display_name}</p><p className="text-xs text-fg-muted">{e.chief_complaint}</p></div>
                <StatusBadge status={e.status} />
              </div>
            )} />
        )}
      </Panel>
      {creating && <NewEncounterDialog open={creating} onOpenChange={setCreating} />}
    </>
  );
}
