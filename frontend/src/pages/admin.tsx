import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';

import { EmptyState, ErrorState, KV, Loading, PageHeader, Panel, RolePill, StatusBadge, Time } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useAdminConfig, useAdminUsers, usePatientSearch, useStaff } from '@/lib/queries';
import type { AdminUser } from '@/lib/types';

function Users() {
  const qc = useQueryClient();
  const { me } = useAuth();
  const q = useAdminUsers();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ email: '', display_name: '', role: 'nurse' });
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);
  const create = useMutation({
    mutationFn: () => api.post<AdminUser>('/api/admin/users', form),
    onSuccess: (u) => { setSecret({ email: u.email, password: u.temporary_password! }); setCreating(false); void qc.invalidateQueries({ queryKey: ['admin'] }); },
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch<AdminUser>(`/api/admin/users/${id}`, body),
    onSuccess: (u) => {
      void qc.invalidateQueries({ queryKey: ['admin'] });
      if (u.temporary_password) setSecret({ email: u.email, password: u.temporary_password });
      else toast.success('Updated; the user\'s sessions were ended');
    },
    onError: (e) => toast.error((e as Error).message),
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end"><Button onClick={() => setCreating(true)}>Add user</Button></div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label="Users">
          <thead><tr className="border-b text-left">{['Name', 'Email', 'Role', 'Status', 'Sessions', 'Last sign-in', ''].map((h) => <th key={h} className="label px-2 py-1.5">{h}</th>)}</tr></thead>
          <tbody>{q.data.items.map((u) => (
            <tr key={u.id} className="border-b last:border-0">
              <td className="px-2 py-1.5 font-medium">{u.display_name}</td>
              <td className="px-2 py-1.5">{u.email}</td>
              <td className="px-2 py-1.5">
                <Select value={u.role} disabled={u.id === me.id} onValueChange={(role) => patch.mutate({ id: u.id, body: { role } })}>
                  <SelectTrigger size="sm" className="h-8 w-32" aria-label={`Role of ${u.display_name}`}><SelectValue /></SelectTrigger>
                  <SelectContent>{q.data.roles.map((r) => <SelectItem key={r.key} value={r.key}>{r.label}</SelectItem>)}</SelectContent>
                </Select>
              </td>
              <td className="px-2 py-1.5"><RolePill role={u.status === 'active' ? 'pass' : 'neutral'}>{u.status}</RolePill></td>
              <td className="px-2 py-1.5 tnum">{u.active_sessions}</td>
              <td className="px-2 py-1.5"><Time iso={u.last_login_at} mode="rel" /></td>
              <td className="px-2 py-1.5">{u.id !== me.id && (
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { status: u.status === 'active' ? 'disabled' : 'active' } })}>{u.status === 'active' ? 'Disable' : 'Enable'}</Button>
                  <Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { reset_password: true } })}>Reset password</Button>
                </span>)}</td>
            </tr>))}</tbody>
        </table>
      </div>
      <Panel title="Roles and permissions">
        <div className="grid gap-3 md:grid-cols-3">{q.data.roles.map((r) => (
          <div key={r.key}><p className="font-semibold">{r.label}</p><p className="mb-2 text-xs text-fg-muted">{r.description}</p>
            <ul className="flex flex-wrap gap-1">{r.permissions.map((p) => <li key={p}><RolePill role="neutral" className="font-mono text-[10px]">{p}</RolePill></li>)}</ul></div>))}</div>
      </Panel>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader><DialogTitle>Add a user</DialogTitle><DialogDescription>A temporary password is generated and shown once.</DialogDescription></DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5"><Label htmlFor="u-name">Display name</Label><Input id="u-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="u-email">Email</Label><Input id="u-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
            <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}><SelectTrigger aria-label="Role"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="doctor">Doctor</SelectItem><SelectItem value="nurse">Nurse / Intake Operator</SelectItem><SelectItem value="admin">Administrator</SelectItem></SelectContent></Select>
            {create.isError && <p role="alert" className="text-sm text-fail">{(create.error as Error).message}</p>}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setCreating(false)}>Cancel</Button><Button onClick={() => create.mutate()} disabled={create.isPending}>Create</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={!!secret} onOpenChange={(v) => !v && setSecret(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Temporary password</DialogTitle><DialogDescription>For {secret?.email}. It will not be shown again.</DialogDescription></DialogHeader>
          <p className="rounded-md border bg-surface-2 p-3 font-mono text-sm select-all">{secret?.password}</p>
          <DialogFooter><Button onClick={() => setSecret(null)}>Done</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CareTeams() {
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [patient, setPatient] = useState<{ id: string; name: string } | null>(null);
  const search = usePatientSearch(query.trim(), 'all');
  const staff = useStaff();
  const [user, setUser] = useState('');
  const [role, setRole] = useState('consulting');
  const [reason, setReason] = useState('');
  const team = useCareTeam(patient?.id);
  const grant = useMutation({
    mutationFn: () => api.post('/api/admin/care-team', { patient_id: patient!.id, user_id: user, role, reason }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['admin', 'care-team'] }); toast.success('Access granted and logged'); setReason(''); },
    onError: (e) => toast.error((e as Error).message),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.post(`/api/admin/care-team/${id}/revoke`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin', 'care-team'] }),
  });
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <div className="flex flex-col gap-2">
        <Input placeholder="Find patient (name or MRN)" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find patient" />
        <ul className="max-h-80 overflow-y-auto rounded-md border">{(search.data?.items ?? []).map((p) => (
          <li key={p.id}><button type="button" className="w-full px-3 py-1.5 text-left text-sm hover:bg-surface-2" onClick={() => setPatient({ id: p.id, name: p.display_name })}>{p.display_name} <span className="text-xs text-fg-muted">{p.mrn}</span></button></li>))}</ul>
      </div>
      {!patient ? <EmptyState title="Choose a patient" /> : (
        <div className="flex flex-col gap-3">
          <p className="font-semibold">{patient.name}</p>
          {team.isPending ? <Loading /> : (
            <ul className="flex flex-col gap-1 text-sm">{team.data?.items.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1">
                <span>{a.user} - {a.role} <span className="text-xs text-fg-muted">({a.reason})</span></span>
                {a.revoked_at ? <RolePill role="neutral">revoked</RolePill> : <Button size="sm" variant="ghost" onClick={() => revoke.mutate(a.id)}>Revoke</Button>}
              </li>))}</ul>
          )}
          <div className="grid gap-2 sm:grid-cols-[1fr_140px]">
            <Select value={user} onValueChange={setUser}><SelectTrigger aria-label="Clinician"><SelectValue placeholder="Clinician" /></SelectTrigger>
              <SelectContent>{(staff.data?.items ?? []).filter((s) => s.role !== 'admin').map((s) => <SelectItem key={s.id} value={s.id}>{s.display_name}</SelectItem>)}</SelectContent></Select>
            <Select value={role} onValueChange={setRole}><SelectTrigger aria-label="Care role"><SelectValue /></SelectTrigger>
              <SelectContent>{['attending', 'consulting', 'nursing', 'intake'].map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}</SelectContent></Select>
          </div>
          <Input placeholder="Reason for access (recorded)" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
          <div><Button disabled={!user || reason.trim().length < 5 || grant.isPending} onClick={() => grant.mutate()}>Grant access</Button></div>
        </div>
      )}
    </div>
  );
}

function useCareTeam(patientId: string | undefined) {
  return useQuery({
    queryKey: ['admin', 'care-team', patientId],
    queryFn: ({ signal }) => api.get<{ items: { id: string; user: string; role: string; reason: string; revoked_at: string | null }[] }>(`/api/admin/care-team/${patientId}`, signal),
    enabled: !!patientId,
  });
}

function Configuration() {
  const qc = useQueryClient();
  const q = useAdminConfig();
  const [models, setModels] = useState<{ clinical?: string; light?: string }>({});
  const save = useMutation({
    mutationFn: () => api.put('/api/admin/config/models', { clinical: models.clinical ?? q.data!.models.clinical, light: models.light ?? q.data!.models.light }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['admin', 'config'] }); toast.success('Model selection saved (audited)'); },
    onError: (e) => toast.error((e as Error).message),
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} />;
  const c = q.data;
  const chat = (c.models.installed as { name: string; capabilities?: string[]; refused?: string }[]).filter((m) => !m.refused && m.capabilities?.includes('completion'));
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="LLM provider">
        <KV items={[{ k: 'Provider', v: c.provider.name }, { k: 'Endpoint', v: c.provider.endpoint },
          { k: 'Hosted providers', v: c.provider.hosted_phi_allowed ? <RolePill role="warn">allowed</RolePill> : <RolePill role="pass">disabled (no medical text leaves the machine)</RolePill> }]} />
      </Panel>
      <Panel title="Local models" actions={<Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || (!models.clinical && !models.light)}>Save</Button>}>
        <div className="grid gap-3 sm:grid-cols-2">
          {(['clinical', 'light'] as const).map((k) => (
            <div key={k} className="flex flex-col gap-1.5"><Label>{k === 'clinical' ? 'Clinical extraction & handoff' : 'Light tasks'}</Label>
              <Select value={models[k] ?? c.models[k]} onValueChange={(v) => setModels((m) => ({ ...m, [k]: v }))}><SelectTrigger aria-label={`${k} model`}><SelectValue /></SelectTrigger>
                <SelectContent>{chat.map((m) => <SelectItem key={m.name} value={m.name}>{m.name}</SelectItem>)}</SelectContent></Select></div>))}
        </div>
        <ul className="mt-3 flex flex-col gap-1 text-xs">{(c.models.installed as { name: string; size_gb?: number; parameter_size?: string; quantization?: string; capabilities?: string[]; refused?: string }[]).map((m) => (
          <li key={m.name} className="flex justify-between gap-2"><span className="font-mono">{m.name}</span>
            <span className="text-fg-muted">{m.refused ? `refused (${m.refused})` : `${m.parameter_size} ${m.quantization} - ${m.size_gb} GB - ${m.capabilities?.join(', ')}`}</span></li>))}</ul>
        <p className="mt-2 text-xs text-fg-muted">Embeddings: {c.models.embedding} - Speech: {c.models.asr}</p>
      </Panel>
      <Panel title="Trace privacy">
        <KV items={[{ k: 'LangSmith', v: c.tracing.langsmith_enabled ? 'enabled' : 'disabled' }, { k: 'Approved endpoint', v: c.tracing.endpoint_configured ? 'configured' : 'none' },
          { k: 'Content sent', v: 'never (metadata allow-list only)' }, { k: 'Allowed keys', v: <span className="font-mono text-xs">{c.tracing.allowed_metadata_keys.join(', ')}</span> }]} />
      </Panel>
      <Panel title="Feature flags">
        <ul className="flex flex-col gap-1 text-sm">{Object.entries(c.feature_flags as Record<string, boolean>).map(([k, v]) => (
          <li key={k} className="flex justify-between"><span>{k.replace(/_/g, ' ')}</span><RolePill role={v ? 'warn' : 'neutral'}>{v ? 'on' : 'off'}</RolePill></li>))}</ul>
        <p className="mt-2 text-xs text-fg-muted">Safety-relevant flags are fixed in this release and changed only through configuration files under change control.</p>
      </Panel>
      <Panel title="Guardrails">
        <ol className="flex flex-col gap-1 text-sm">{(c.guardrails as { id: number; name: string; enforced_by: string }[]).map((g) => (
          <li key={g.id}><span className="font-medium">{g.id}. {g.name}</span> <span className="text-fg-muted">- {g.enforced_by}</span></li>))}</ol>
      </Panel>
      <Panel title="Escalation rules" subtitle={`${c.escalation_rules.version} - ${c.escalation_rules.status}`}>
        <ul className="flex flex-col gap-1 text-sm">{(c.escalation_rules.rules as { id: string; level: string; label: string }[]).map((r) => (
          <li key={r.id} className="flex justify-between gap-2"><span>{r.label}</span><RolePill role={r.level === 'urgent' ? 'fail' : 'warn'}>{r.level}</RolePill></li>))}</ul>
        <p className="mt-2 text-xs text-fg-muted">{c.escalation_rules.policy}</p>
      </Panel>
    </div>
  );
}

function Diagnostics() {
  const run = useMutation({ mutationFn: () => api.post<Record<string, any>>('/api/admin/diagnostics') });
  return (
    <div className="flex flex-col gap-3">
      <div><Button onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? 'Running...' : 'Run diagnostics'}</Button></div>
      {run.data && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Components">
            <ul className="flex flex-col gap-1.5">{(run.data.health.components as { id: string; label: string; status: string; summary: string; latency_ms: number | null }[]).map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-2 text-sm"><span>{c.label}</span>
                <span className="flex items-center gap-2"><span className="text-xs text-fg-muted">{c.summary}{c.latency_ms != null ? ` (${c.latency_ms} ms)` : ''}</span><StatusBadge status={c.status} /></span></li>))}</ul>
          </Panel>
          <Panel title="Data integrity">
            <KV items={[{ k: 'Audit chain', v: run.data.audit_chain.intact ? <RolePill role="pass">intact ({run.data.audit_chain.last_seq})</RolePill> : <RolePill role="fail">broken</RolePill> },
              { k: 'Outbox', v: JSON.stringify(run.data.outbox) }, { k: 'Document ingestion', v: JSON.stringify(run.data.document_ingestion) },
              { k: 'Vector index', v: JSON.stringify(run.data.vector_index) }, { k: 'Took', v: `${run.data.duration_ms} ms` }]} />
          </Panel>
        </div>
      )}
    </div>
  );
}

export function AdminPage() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Administration" subtitle="Users, access, local AI configuration and system diagnostics. Administrators have no clinical-record access." />
      <Panel>
        <Tabs defaultValue="users">
          <TabsList className="mb-4 flex h-auto flex-wrap justify-start">
            {can('admin.users') && <TabsTrigger value="users">Users &amp; roles</TabsTrigger>}
            {can('admin.care_team') && <TabsTrigger value="care">Care teams</TabsTrigger>}
            {can('admin.config') && <TabsTrigger value="config">Configuration</TabsTrigger>}
            {can('admin.diagnostics') && <TabsTrigger value="diag">Diagnostics</TabsTrigger>}
          </TabsList>
          <TabsContent value="users"><Users /></TabsContent>
          <TabsContent value="care"><CareTeams /></TabsContent>
          <TabsContent value="config"><Configuration /></TabsContent>
          <TabsContent value="diag"><Diagnostics /></TabsContent>
        </Tabs>
      </Panel>
    </>
  );
}
