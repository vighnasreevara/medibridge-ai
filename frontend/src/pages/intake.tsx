import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { FileAudio, Keyboard, Loader2, Mic, Search, Square, Upload, UserPlus } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { AgentProgress, RunStatusLine } from '@/components/clinical';
import { EmptyState, ErrorState, Loading, PageHeader, Panel, RolePill, StatusBadge, Time } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { api, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { todayIso, useEncounter, useRun, useStaff, useTranscription, useWorkflow, useWorklist } from '@/lib/queries';
import type { RegistryPatient, RegistryResult, Segment, Speaker } from '@/lib/types';
import { cn } from '@/lib/utils';

import { ConsentStep, IdentityStep, readiness } from './encounter';

function PatientRow({ p, action }: { p: RegistryPatient; action: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
      <span className="min-w-48 flex-1"><span className="font-medium">{p.given_name} {p.family_name}</span>
        <span className="ml-2 font-mono text-xs text-fg-muted">{p.mrn}</span>
        <span className="block text-sm text-fg-muted">Born {p.birth_date} - {p.sex_at_birth}{p.score != null ? ` - name match ${Math.round(p.score * 100)}%` : ''}</span></span>
      {action}
    </li>
  );
}

/** Patient Registry (EMPI): find a returning patient (MRN + date of birth, or name + date of birth) or register a new one. */
function RegistryPanel() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const doctors = useStaff('doctor');
  const [find, setFind] = useState({ mrn: '', given_name: '', family_name: '', birth_date: '' });
  const [reg, setReg] = useState({ given_name: '', family_name: '', birth_date: '', sex_at_birth: 'unknown', attending_user_id: '' });
  const [result, setResult] = useState<RegistryResult | null>(null);
  const [registering, setRegistering] = useState(false);
  const [dupes, setDupes] = useState<RegistryPatient[] | null>(null);
  const [complaint, setComplaint] = useState('');
  const match = useMutation({
    mutationFn: () => api.post<RegistryResult>('/api/registry/match', { ...find, mrn: find.mrn || null }),
    onSuccess: (r) => { setResult(r); setRegistering(r.status === 'no_match'); if (r.status === 'no_match') setReg((x) => ({ ...x, given_name: find.given_name, family_name: find.family_name, birth_date: find.birth_date })); },
  });
  const register = useMutation({
    mutationFn: (confirm_new: boolean) => api.post<RegistryResult>('/api/registry/register', { ...reg, attending_user_id: reg.attending_user_id || null, confirm_new }),
    onSuccess: (r) => { setDupes(null); setRegistering(false); setResult({ status: 'matched', patient: r.patient }); toast.success(`Registered ${r.patient?.mrn}`); },
    onError: (e) => { if (e instanceof ApiError && e.status === 409) setDupes((e.detail?.candidates as RegistryPatient[]) ?? []); },
  });
  const startIntake = useMutation({
    mutationFn: (p: RegistryPatient) => api.post<{ id: string }>('/api/encounters', {
      patient_id: p.patient_id, attending_user_id: reg.attending_user_id || doctors.data?.items[0]?.id, chief_complaint: complaint || 'Cardiology intake',
    }),
    onSuccess: (e) => navigate({ to: '/intake/$encounterId', params: { encounterId: e.id } }),
  });
  const start = (p: RegistryPatient) => <Button size="sm" disabled={startIntake.isPending} onClick={() => startIntake.mutate(p)}>Start today's intake</Button>;
  const field = (id: string, label: string, value: string, onChange: (v: string) => void, type = 'text') => (
    <div className="flex flex-col gap-1"><Label htmlFor={id}>{label}</Label><Input id={id} type={type} value={value} onChange={(e) => onChange(e.target.value)} /></div>
  );
  return (
    <Panel title="Patient Registry (EMPI)" subtitle="Find a returning patient by MRN and date of birth (or name and date of birth). Register first-time patients here.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {field('f-mrn', 'MRN (wristband)', find.mrn, (v) => setFind((x) => ({ ...x, mrn: v.toUpperCase() })))}
        {field('f-given', 'Given name', find.given_name, (v) => setFind((x) => ({ ...x, given_name: v })))}
        {field('f-family', 'Family name', find.family_name, (v) => setFind((x) => ({ ...x, family_name: v })))}
        {field('f-dob', 'Date of birth', find.birth_date, (v) => setFind((x) => ({ ...x, birth_date: v })), 'date')}
        <div className="flex items-end"><Button className="w-full" disabled={!find.birth_date || (!find.mrn && (!find.given_name || find.family_name.length < 2)) || match.isPending}
          onClick={() => match.mutate()}><Search /> Find patient</Button></div>
      </div>
      <div className="mt-3 flex flex-col gap-1"><Label htmlFor="f-cc">Reason for today's visit (at the desk)</Label>
        <Input id="f-cc" value={complaint} onChange={(e) => setComplaint(e.target.value)} placeholder="e.g. Palpitations" /></div>
      {result && (
        <div className="mt-3 rounded-md border p-2" role="status">
          {result.status === 'matched' && result.patient && <ul><PatientRow p={result.patient} action={start(result.patient)} /></ul>}
          {result.status === 'candidates' && <><p className="text-sm text-fg-muted">Possible matches - confirm identity with the patient before choosing:</p>
            <ul className="divide-y">{result.candidates!.map((c) => <PatientRow key={c.patient_id} p={c} action={start(c)} />)}</ul></>}
          {result.status === 'identity_mismatch' && <p className="text-sm text-fail">This MRN is registered with a different date of birth. Check the wristband and the patient's answer; the attempt was recorded.</p>}
          {result.status === 'no_match' && <p className="text-sm text-fg-muted">No patient matches. Register a new patient below.</p>}
        </div>
      )}
      {can('registry.register') && (
        <div className="mt-3">
          {!registering ? <Button variant="outline" size="sm" onClick={() => setRegistering(true)}><UserPlus /> Register a new patient</Button> : (
            <fieldset className="rounded-lg border p-3">
              <legend className="px-1 text-sm font-semibold">New patient</legend>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {field('r-given', 'Given name', reg.given_name, (v) => setReg((x) => ({ ...x, given_name: v })))}
                {field('r-family', 'Family name', reg.family_name, (v) => setReg((x) => ({ ...x, family_name: v })))}
                {field('r-dob', 'Date of birth', reg.birth_date, (v) => setReg((x) => ({ ...x, birth_date: v })), 'date')}
                <div className="flex flex-col gap-1"><Label>Sex at birth</Label>
                  <Select value={reg.sex_at_birth} onValueChange={(v) => setReg((x) => ({ ...x, sex_at_birth: v }))}>
                    <SelectTrigger aria-label="Sex at birth"><SelectValue /></SelectTrigger>
                    <SelectContent>{['female', 'male', 'intersex', 'unknown'].map((v) => <SelectItem key={v} value={v}>{v}</SelectItem>)}</SelectContent>
                  </Select></div>
                <div className="flex flex-col gap-1"><Label>Attending cardiologist</Label>
                  <Select value={reg.attending_user_id} onValueChange={(v) => setReg((x) => ({ ...x, attending_user_id: v }))}>
                    <SelectTrigger aria-label="Attending cardiologist"><SelectValue placeholder="Choose" /></SelectTrigger>
                    <SelectContent>{(doctors.data?.items ?? []).map((d) => <SelectItem key={d.id} value={d.id}>{d.display_name}</SelectItem>)}</SelectContent>
                  </Select></div>
              </div>
              {dupes && (
                <div className="mt-3 rounded-md border border-warn bg-warn-tint p-2">
                  <p className="text-sm font-medium">Possible duplicates - is this one of these patients?</p>
                  <ul className="divide-y">{dupes.map((c) => <PatientRow key={c.patient_id} p={c} action={start(c)} />)}</ul>
                  <Button size="sm" variant="outline" className="mt-2" onClick={() => register.mutate(true)}>No - register as a new patient</Button>
                </div>
              )}
              <div className="mt-3 flex gap-2">
                <Button disabled={!reg.given_name || reg.family_name.length < 2 || !reg.birth_date || !reg.attending_user_id || register.isPending} onClick={() => register.mutate(false)}>Register</Button>
                <Button variant="outline" onClick={() => { setRegistering(false); setDupes(null); }}>Cancel</Button>
              </div>
              {register.isError && !(register.error instanceof ApiError && register.error.status === 409) && <p role="alert" className="mt-2 text-sm text-fail">{(register.error as Error).message}</p>}
            </fieldset>
          )}
        </div>
      )}
      {startIntake.isError && <p role="alert" className="mt-2 text-sm text-fail">{(startIntake.error as Error).message}</p>}
    </Panel>
  );
}

export function IntakeListPage() {
  const navigate = useNavigate();
  const q = useWorklist({ day: todayIso(), status: 'planned,in_progress', scope: 'all' });
  return (
    <>
      <PageHeader title="Patient intake" subtitle="Cardiology clinic: find or register the patient, then record the intake conversation." />
      <div className="mb-4"><RegistryPanel /></div>
      <Panel title="Today's clinic list" subtitle="Encounters waiting for intake in your care team.">
        {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> :
          !q.data.items.length ? <EmptyState title="No encounters are waiting for intake" body="Create an encounter from the Encounters page." /> : (
            <ul className="divide-y">
              {q.data.items.map((e) => {
                const r = readiness(e);
                return (
                  <li key={e.id}>
                    <button type="button" onClick={() => navigate({ to: '/intake/$encounterId', params: { encounterId: e.id } })}
                      className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3 text-left hover:bg-surface-2">
                      <Time iso={e.started_at} withTime />
                      <span className="min-w-48 flex-1"><span className="font-medium">{e.patient.display_name}</span>
                        <span className="ml-2 text-xs text-fg-muted">{e.patient.mrn}</span>
                        <span className="block text-sm text-fg-muted">{e.chief_complaint}</span></span>
                      <RolePill role={r.identity ? 'pass' : 'human'}>{r.identity ? 'Identity verified' : 'Verify identity'}</RolePill>
                      <RolePill role={r.consent ? 'pass' : 'human'}>{r.consent ? 'Consent recorded' : 'Consent needed'}</RolePill>
                      <StatusBadge status={e.latest_run?.status ?? e.status} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
      </Panel>
    </>
  );
}

function newKey() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function Recorder({ encounterId, onSubmitted }: { encounterId: string; onSubmitted: () => void }) {
  const qc = useQueryClient();
  const [state, setState] = useState<'idle' | 'recording' | 'recorded'>('idle');
  const [seconds, setSeconds] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [mode, setMode] = useState<'audio' | 'manual'>('audio');
  const [manual, setManual] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const key = useRef(newKey()); // one idempotency key per submission attempt: double clicks cannot start two runs
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (state !== 'recording') return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);
  useEffect(() => () => recorder.current?.stream.getTracks().forEach((t) => t.stop()), []);

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks.current = [];
      rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      rec.onstop = () => {
        setBlob(new Blob(chunks.current, { type: rec.mimeType || 'audio/webm' }));
        stream.getTracks().forEach((t) => t.stop());
        setState('recorded');
      };
      rec.start(1000);
      recorder.current = rec;
      setSeconds(0);
      setState('recording');
    } catch {
      toast.error('Microphone unavailable', { description: 'Allow microphone access, upload an audio file, or type the transcript.' });
    }
  }

  const submit = useMutation({
    mutationFn: async (input: { kind: 'audio'; blob: Blob; name: string } | { kind: 'manual'; text: string }) => {
      let transcription_id: string;
      if (input.kind === 'audio') {
        const fd = new FormData();
        const type = input.blob.type.split(';')[0] || 'audio/webm';
        fd.append('file', new File([input.blob], input.name, { type }));
        transcription_id = (await api.post<{ transcription_id: string }>(`/api/encounters/${encounterId}/intake/audio`, fd)).transcription_id;
      } else {
        const segments = input.text.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
          const m = line.match(/^(patient|clinician|nurse|doctor)\s*:\s*(.*)$/i);
          const speaker: Speaker = m ? (m[1].toLowerCase() === 'patient' ? 'patient' : 'clinician') : 'unattributed';
          return { speaker, text: m ? m[2] : line };
        });
        transcription_id = (await api.post<{ transcription_id: string }>(`/api/encounters/${encounterId}/intake/manual-transcript`, { segments })).transcription_id;
      }
      return api.post(`/api/encounters/${encounterId}/runs`, { transcription_id, idempotency_key: key.current });
    },
    onSuccess: () => {
      key.current = newKey();
      void qc.invalidateQueries({ queryKey: ['encounter', encounterId] });
      toast.success('Intake submitted', { description: 'Processing runs locally; you will be asked to review the transcript.' });
      onSubmitted();
    },
  });

  const fmt = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-2" role="group" aria-label="Intake mode">
        <Button variant={mode === 'audio' ? 'default' : 'outline'} size="sm" onClick={() => setMode('audio')} aria-pressed={mode === 'audio'}><Mic /> Record audio</Button>
        <Button variant={mode === 'manual' ? 'default' : 'outline'} size="sm" onClick={() => setMode('manual')} aria-pressed={mode === 'manual'}><Keyboard /> Type transcript</Button>
      </div>
      {mode === 'audio' ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border p-4">
          {state === 'recording' ? (
            <Button variant="destructive" onClick={() => recorder.current?.stop()}><Square /> Stop ({fmt})</Button>
          ) : (
            <Button onClick={start} disabled={submit.isPending}><Mic /> {state === 'recorded' ? 'Record again' : 'Start recording'}</Button>
          )}
          {state === 'recording' && <span className="inline-flex items-center gap-2 text-sm text-fail" role="status"><span className="size-2.5 animate-pulse rounded-full bg-fail" /> Recording {fmt}</span>}
          {state === 'recorded' && blob && (
            <>
              <audio controls src={URL.createObjectURL(blob)} className="h-9" />
              <Button onClick={() => submit.mutate({ kind: 'audio', blob, name: 'intake.webm' })} disabled={submit.isPending}>
                {submit.isPending ? <Loader2 className="animate-spin" /> : <Upload />} Submit recording
              </Button>
            </>
          )}
          <span className="text-xs text-fg-muted">or</span>
          <input ref={fileInput} type="file" accept="audio/*" className="hidden" onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) submit.mutate({ kind: 'audio', blob: f, name: f.name });
          }} />
          <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()} disabled={submit.isPending}><FileAudio /> Upload audio file</Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Textarea rows={8} value={manual} onChange={(e) => setManual(e.target.value)}
            placeholder={'One line per turn, optionally prefixed:\nClinician: What brings you in today?\nPatient: I have had a cough for five days.'} />
          <p className="text-xs text-fg-muted">Use this when audio is unavailable. The transcript is marked as manually entered.</p>
          <div><Button disabled={!manual.trim() || submit.isPending} onClick={() => submit.mutate({ kind: 'manual', text: manual })}>Submit transcript</Button></div>
        </div>
      )}
      {submit.isError && <p role="alert" className="text-sm text-fail">{submit.error instanceof ApiError ? submit.error.message : 'Submission failed.'}</p>}
    </div>
  );
}

function WordSpan({ w }: { w: NonNullable<Segment['words']>[number] }) {
  if (!w.uncertain) return <span>{w.w}</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <mark className={cn('rounded-sm bg-transparent underline decoration-2 underline-offset-2', w.suspect_drug ? 'decoration-fail' : 'decoration-warn')}>{w.w}</mark>
      </TooltipTrigger>
      <TooltipContent>{w.suspect_drug ? `Possible misrecognised drug name - did they say "${w.suspect_drug}"?` : `Low confidence (${Math.round(w.p * 100)}%)`}</TooltipContent>
    </Tooltip>
  );
}

export function TranscriptReview({ transcriptionId, onConfirmed }: { transcriptionId: string; onConfirmed?: () => void }) {
  const qc = useQueryClient();
  const q = useTranscription(transcriptionId);
  const [edits, setEdits] = useState<Record<string, { text: string; speaker: Speaker }>>({});
  const segments = useMemo(() => q.data?.reviewed_segments ?? q.data?.raw_segments ?? [], [q.data]);
  const raw = q.data?.raw_segments ?? [];
  const save = useMutation({
    mutationFn: (confirm: boolean) => api.post<{ workflow: string; corrected: number }>(`/api/transcriptions/${transcriptionId}/review`, {
      confirm, segments: segments.map((s) => ({ id: s.id, speaker: edits[s.id]?.speaker ?? s.speaker, text: edits[s.id]?.text ?? s.text })),
    }),
    onSuccess: (r, confirm) => {
      void qc.invalidateQueries({ queryKey: ['transcription', transcriptionId] });
      if (confirm) {
        toast.success('Transcript confirmed', { description: r.corrected ? `${r.corrected} correction(s) saved; the original is preserved.` : undefined });
        onConfirmed?.();
      }
    },
  });
  if (q.isPending) return <Loading label="Loading transcript" />;
  if (q.isError) return <ErrorState error={q.error} />;
  const uncertain = raw.filter((s) => s.uncertain).length;
  const unattributed = segments.filter((s) => (edits[s.id]?.speaker ?? s.speaker) === 'unattributed').length;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <RolePill role={uncertain ? 'warn' : 'pass'}>{uncertain} uncertain passage{uncertain === 1 ? '' : 's'}</RolePill>
        {unattributed > 0 && <RolePill role="human">{unattributed} without a speaker</RolePill>}
        <span className="text-xs text-fg-muted">{q.data.model} {q.data.audio_duration_s ? `- ${q.data.audio_duration_s.toFixed(0)} s audio` : ''}</span>
      </div>
      <p className="text-xs text-fg-muted">Underlined words had low recognition confidence; red underline means a possible misheard drug name. Only correct what you are sure of. Assign speakers so only the patient's words are extracted.</p>
      <ol className="flex flex-col gap-2">
        {segments.map((s) => {
          const r = raw.find((x) => x.id === s.id);
          const text = edits[s.id]?.text ?? s.text;
          const speaker = edits[s.id]?.speaker ?? s.speaker;
          return (
            <li key={s.id} className={cn('rounded-lg border p-3', r?.uncertain && 'border-warn')}>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                <span className="font-mono">{s.id}</span>
                {r?.start != null && <span className="tnum">{r.start.toFixed(1)}-{r.end?.toFixed(1)} s</span>}
                {r?.uncertain_reasons?.map((x) => <RolePill key={x} role="warn">{x.replace(/_/g, ' ')}</RolePill>)}
                <Select value={speaker} onValueChange={(v) => setEdits((e) => ({ ...e, [s.id]: { text, speaker: v as Speaker } }))}>
                  <SelectTrigger size="sm" className="ml-auto h-7 w-36" aria-label={`Speaker for ${s.id}`}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="patient">Patient</SelectItem>
                    <SelectItem value="clinician">Clinician</SelectItem>
                    <SelectItem value="unattributed">Unattributed</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {r?.words?.length ? (
                <p className="mb-2 text-sm leading-6">{r.words.map((w, i) => <WordSpan key={i} w={w} />)}</p>
              ) : null}
              <Textarea rows={2} value={text} aria-label={`Text of ${s.id}`}
                onChange={(e) => setEdits((x) => ({ ...x, [s.id]: { text: e.target.value, speaker } }))} />
              {s.original_text && <p className="mt-1 text-xs text-fg-muted">Original: {s.original_text}</p>}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => save.mutate(false)} disabled={save.isPending}>Save draft</Button>
        <Button onClick={() => save.mutate(true)} disabled={save.isPending || q.data.status === 'reviewed'}>
          {q.data.status === 'reviewed' ? 'Confirmed' : 'Confirm transcript and continue'}
        </Button>
      </div>
      {save.isError && <p role="alert" className="text-sm text-fail">{(save.error as Error).message}</p>}
    </div>
  );
}

export function IntakeConsolePage() {
  const { encounterId } = useParams({ from: '/intake/$encounterId' });
  const enc = useEncounter(encounterId);
  const wf = useWorkflow(encounterId);
  const run = useRun(wf.data?.run?.id);
  if (enc.isPending) return <Loading />;
  if (enc.isError) return <ErrorState error={enc.error} retry={() => enc.refetch()} />;
  const e = enc.data;
  const r = readiness(e);
  const active = wf.data?.run && ['queued', 'running', 'waiting_human', 'interrupted'].includes(wf.data.run.status);
  const waitingTranscript = wf.data?.run?.waiting_gate === 'transcript_review';
  return (
    <>
      <PageHeader crumbs={<Link to="/intake">Intake</Link>} title={`Intake - ${e.patient.display_name}`}
        subtitle={<>MRN {e.patient.mrn} - {e.chief_complaint || 'no reason recorded'} - attending {e.attending_name}</>}
        actions={<Link to="/encounters/$encounterId" params={{ encounterId }} className="text-sm">Encounter workspace</Link>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-4">
          <Panel title="1. Verify identity" tone={r.identity ? undefined : 'human'}><IdentityStep encounter={e} /></Panel>
          <Panel title="2. Record consent" tone={r.consent ? undefined : 'human'}><ConsentStep encounter={e} /></Panel>
          <Panel title="3. Record the intake conversation" subtitle={r.ready ? 'Audio stays on this computer and is processed by a local model.' : 'Available after identity and both consents are recorded.'}>
            {!r.ready ? <p className="text-sm text-fg-muted">Recording is locked until identity is verified and the patient has agreed to recording and AI processing.</p>
              : active ? <p className="text-sm text-fg-muted">An intake is already being processed for this encounter.</p>
              : <Recorder encounterId={encounterId} onSubmitted={() => void wf.refetch()} />}
          </Panel>
          {waitingTranscript && wf.data?.run && (
            <Panel title="4. Review the transcript" subtitle="Gate: a person must confirm the transcript before extraction." tone="human">
              <TranscriptReviewForRun encounterId={encounterId} />
            </Panel>
          )}
          {wf.data?.run?.waiting_gate === 'handoff_review' && (
            <Panel title="Handoff ready for doctor review" tone="human">
              <Link to="/handoffs/$encounterId" params={{ encounterId }}>Open the handoff</Link>
            </Panel>
          )}
        </div>
        <Panel title="Processing" subtitle={<RunStatusLine run={wf.data?.run} />}>
          {wf.data?.run ? <AgentProgress run={run.data} /> : <p className="text-sm text-fg-muted">Nothing has been submitted yet.</p>}
        </Panel>
      </div>
    </>
  );
}

function TranscriptReviewForRun({ encounterId }: { encounterId: string }) {
  const wf = useWorkflow(encounterId);
  const tid = wf.data?.transcription_id;
  if (!tid) return <Loading />;
  return <TranscriptReview transcriptionId={tid} onConfirmed={() => void wf.refetch()} />;
}
