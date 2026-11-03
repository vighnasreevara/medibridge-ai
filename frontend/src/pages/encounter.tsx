import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from '@tanstack/react-router';
import { CheckCircle2, ShieldAlert } from 'lucide-react';
import { useState, type FormEvent, type ReactNode } from 'react';
import { toast } from 'sonner';

import { ErrorState, KV, Loading, PageHeader, Panel, RolePill, StatusBadge, Time, fmtDate } from '@/components/common';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { api, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useEncounter } from '@/lib/queries';
import type { EncounterDetail } from '@/lib/types';

export const CONSENT_TEXT = {
  audio_recording: 'The patient agrees that this intake conversation may be audio-recorded and stored in their record.',
  ai_processing: 'The patient agrees that a local AI system may transcribe and summarise the conversation for clinician review. No data leaves the hospital system.',
} as const;

export function IdentityStep({ encounter }: { encounter: EncounterDetail }) {
  const qc = useQueryClient();
  const [mrn, setMrn] = useState('');
  const [dob, setDob] = useState('');
  const [method, setMethod] = useState('wristband+dob');
  const verify = useMutation({
    mutationFn: () => api.post<EncounterDetail>(`/api/encounters/${encounter.id}/verify-identity`, { method, mrn, birth_date: dob }),
    onSuccess: (data) => { qc.setQueryData(['encounter', encounter.id], data); toast.success('Identity verified'); },
  });
  if (encounter.identity_verified) {
    return <p className="flex items-center gap-2 text-sm text-pass"><CheckCircle2 className="size-4" /> Verified ({encounter.identity_method}) <Time iso={encounter.identity_verified_at} withTime /></p>;
  }
  const submit = (e: FormEvent) => { e.preventDefault(); verify.mutate(); };
  const mismatch = verify.error instanceof ApiError && verify.error.code === 'identity_mismatch';
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <p className="text-sm text-fg-muted">Read the MRN from the wristband or ID and ask the patient to state their date of birth. Do not rely on the spoken name.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5"><Label htmlFor="v-mrn">MRN on wristband / ID</Label>
          <Input id="v-mrn" required value={mrn} onChange={(e) => setMrn(e.target.value)} autoComplete="off" /></div>
        <div className="flex flex-col gap-1.5"><Label htmlFor="v-dob">Date of birth (stated)</Label>
          <Input id="v-dob" type="date" required value={dob} onChange={(e) => setDob(e.target.value)} /></div>
        <div className="flex flex-col gap-1.5"><Label>Method</Label>
          <Select value={method} onValueChange={setMethod}>
            <SelectTrigger aria-label="Verification method"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="wristband+dob">Wristband + date of birth</SelectItem>
              <SelectItem value="photo_id+dob">Photo ID + date of birth</SelectItem>
              <SelectItem value="two_identifiers_verbal">Two identifiers (verbal)</SelectItem>
            </SelectContent>
          </Select></div>
      </div>
      {mismatch && (
        <Alert variant="destructive"><ShieldAlert /><AlertTitle>Identifiers do not match this patient</AlertTitle>
          <AlertDescription>Stop. Re-check the wristband or ID before continuing. This attempt was logged.</AlertDescription></Alert>
      )}
      {verify.isError && !mismatch && <p role="alert" className="text-sm text-fail">{(verify.error as Error).message}</p>}
      <div><Button type="submit" disabled={verify.isPending}>Verify identity</Button></div>
    </form>
  );
}

export function ConsentStep({ encounter }: { encounter: EncounterDetail }) {
  const qc = useQueryClient();
  const record = useMutation({
    mutationFn: (body: { consent_type: string; status: string; method: string }) =>
      api.post(`/api/encounters/${encounter.id}/consents`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['encounter', encounter.id] }),
  });
  return (
    <ul className="flex flex-col gap-3">
      {(Object.keys(CONSENT_TEXT) as (keyof typeof CONSENT_TEXT)[]).map((type) => {
        const current = encounter.consent[type];
        return (
          <li key={type} className="rounded-lg border p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="max-w-2xl text-sm">{CONSENT_TEXT[type]}</p>
              {current ? (
                <RolePill role={current.status === 'granted' ? 'pass' : 'fail'}>{current.status} - {current.method.replace(/_/g, ' ')}</RolePill>
              ) : <RolePill role="human">Not asked yet</RolePill>}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant={current?.status === 'granted' ? 'secondary' : 'default'} disabled={record.isPending}
                onClick={() => record.mutate({ consent_type: type, status: 'granted', method: 'verbal_witnessed' })}>Patient agrees</Button>
              <Button size="sm" variant="outline" disabled={record.isPending}
                onClick={() => record.mutate({ consent_type: type, status: current?.status === 'granted' ? 'withdrawn' : 'declined', method: 'verbal_witnessed' })}>
                {current?.status === 'granted' ? 'Patient withdraws' : 'Patient declines'}
              </Button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function readiness(e: EncounterDetail) {
  const consent = e.consent.audio_recording?.status === 'granted' && e.consent.ai_processing?.status === 'granted';
  return { identity: e.identity_verified, consent, ready: e.identity_verified && consent };
}

export function EncounterPage({ children }: { children?: ReactNode }) {
  const { encounterId } = useParams({ from: '/encounters/$encounterId' });
  const { can } = useAuth();
  const q = useEncounter(encounterId);
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const e = q.data;
  const r = readiness(e);
  const closed = ['finalized', 'cancelled', 'entered_in_error'].includes(e.status);
  return (
    <>
      <PageHeader
        crumbs={<><Link to="/encounters">Encounters</Link> / <Link to="/patients/$patientId" params={{ patientId: e.patient.id }}>{e.patient.display_name}</Link></>}
        title={e.chief_complaint || 'Encounter'}
        subtitle={<>{e.patient.display_name} - MRN {e.patient.mrn} - born {fmtDate(e.patient.birth_date)} ({e.patient.age})</>}
        actions={<StatusBadge status={e.status} />}
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div className="flex flex-col gap-4">
          {!closed && can('intake.record') && (
            <>
              <Panel title="1. Verify identity" subtitle="Guardrail: every encounter must belong to a verified patient." tone={r.identity ? undefined : 'human'}>
                <IdentityStep encounter={e} />
              </Panel>
              <Panel title="2. Record consent" subtitle="Ask the patient and record their answer for each statement." tone={r.consent ? undefined : 'human'}>
                <ConsentStep encounter={e} />
              </Panel>
            </>
          )}
          {children}
        </div>
        <Panel title="Encounter">
          <KV items={[
            { k: 'Started', v: <Time iso={e.started_at} withTime /> },
            { k: 'Type', v: <span className="capitalize">{e.encounter_class}</span> },
            { k: 'Attending', v: e.attending_name ?? '-' },
            { k: 'Location', v: e.location || '-' },
            { k: 'Identity', v: r.identity ? <span className="text-pass">Verified</span> : <span className="text-human">Not verified</span> },
            { k: 'Consent', v: r.consent ? <span className="text-pass">Recorded</span> : <span className="text-human">Required</span> },
            { k: 'Workflow', v: e.latest_run ? <StatusBadge status={e.latest_run.status} /> : 'Not started' },
            { k: 'Handoff', v: e.handoff ? <StatusBadge status={e.handoff.status} /> : 'None yet' },
          ]} />
        </Panel>
      </div>
    </>
  );
}
