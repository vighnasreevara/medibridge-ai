import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ShieldCheck, ShieldX } from 'lucide-react';

import { EmptyState, ErrorState, Loading, PageHeader, Panel, RolePill, Time } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useAudit, useAuditSummary, useAuditVerify } from '@/lib/queries';

const CATEGORIES = ['authentication', 'access', 'approval', 'modification', 'safety', 'policy', 'security', 'administration', 'workflow'];
const OUTCOME_ROLE = { success: 'pass', allowed: 'pass', denied: 'fail', failure: 'warn' } as const;

function DailyChart({ hours }: { hours: number }) {
  const q = useAuditSummary(hours);
  const data = useMemo(() => {
    const days: Record<string, Record<string, number>> = {};
    for (const r of q.data?.daily ?? []) {
      const key = r.outcome === 'denied' || r.outcome === 'failure' ? 'denied_or_failed' : 'completed';
      days[r.day] ??= { completed: 0, denied_or_failed: 0 };
      days[r.day][key] += r.count;
    }
    return Object.entries(days).map(([day, v]) => ({ day: day.slice(5), ...v }));
  }, [q.data]);
  if (q.isPending) return <Loading />;
  if (!data.length) return <EmptyState title="No audit events in this window" />;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ left: 0, right: 8 }} accessibilityLayer>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="day" stroke="var(--fg-subtle)" fontSize={11} tickLine={false} />
        <YAxis stroke="var(--fg-subtle)" fontSize={11} tickLine={false} allowDecimals={false} />
        <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--fg)', fontSize: 12 }} cursor={{ fill: 'var(--surface-2)' }} />
        <Legend wrapperStyle={{ fontSize: 12, color: 'var(--fg-muted)' }} />
        {/* Status colours with a legend label; stacked segments separated by a 2px surface gap. */}
        <Bar dataKey="completed" name="Allowed / succeeded" stackId="a" fill="var(--pass)" stroke="var(--surface)" strokeWidth={2} maxBarSize={24} />
        <Bar dataKey="denied_or_failed" name="Denied / failed" stackId="a" fill="var(--fail)" stroke="var(--surface)" strokeWidth={2} radius={[4, 4, 0, 0]} maxBarSize={24} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function AuditPage() {
  const [category, setCategory] = useState<string>('all');
  const [outcome, setOutcome] = useState<string>('all');
  const [action, setAction] = useState('');
  const [before, setBefore] = useState<number | undefined>();
  const verify = useAuditVerify();
  const q = useAudit({ category: category === 'all' ? undefined : category, outcome: outcome === 'all' ? undefined : outcome,
    action: action || undefined, before_seq: before, limit: 100 });
  return (
    <>
      <PageHeader title="Audit & governance" subtitle="Append-only, hash-chained record of access, approvals, modifications and security events. Read-only." />
      <div className="mb-4">
        {verify.isPending ? <Loading label="Verifying the audit chain" /> : verify.isError ? <ErrorState error={verify.error} /> : verify.data.intact ? (
          <p className="flex flex-wrap items-center gap-2 rounded-lg border border-pass bg-pass-tint p-3 text-sm"><ShieldCheck className="size-5 text-pass" />
            Audit chain intact: {verify.data.last_seq} entries, head hash <span className="font-mono text-xs">{verify.data.head_hash?.slice(0, 16)}</span>.</p>
        ) : (
          <p className="flex items-center gap-2 rounded-lg border border-fail bg-fail-tint p-3 text-sm" role="alert"><ShieldX className="size-5 text-fail" />
            Tampering detected at entries {verify.data.problems.map((p) => `${p.seq} (${p.problem})`).join(', ')}.</p>
        )}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Panel title="Events per day (last 14 days)"><DailyChart hours={24 * 14} /></Panel>
        <Panel title="Filters">
          <div className="grid gap-3 sm:grid-cols-3">
            <Select value={category} onValueChange={(v) => { setCategory(v); setBefore(undefined); }}>
              <SelectTrigger aria-label="Category"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="all">All categories</SelectItem>{CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={outcome} onValueChange={(v) => { setOutcome(v); setBefore(undefined); }}>
              <SelectTrigger aria-label="Outcome"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="all">All outcomes</SelectItem>{Object.keys(OUTCOME_ROLE).map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
            </Select>
            <Input placeholder="Action prefix, e.g. handoff." value={action} onChange={(e) => { setAction(e.target.value); setBefore(undefined); }} aria-label="Action prefix" />
          </div>
        </Panel>
      </div>
      <Panel className="mt-4" title="Audit log">
        {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} /> : !q.data.items.length ? <EmptyState title="No matching entries" /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Audit log">
                <thead><tr className="border-b text-left">{['#', 'Time', 'Action', 'Category', 'Outcome', 'Actor', 'Patient', 'Resource', 'Details', 'Hash'].map((h) => <th key={h} className="label px-2 py-1.5">{h}</th>)}</tr></thead>
                <tbody>{q.data.items.map((r) => (
                  <tr key={r.seq} className="border-b align-top last:border-0">
                    <td className="px-2 py-1.5 tnum text-fg-muted">{r.seq}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap"><Time iso={r.occurred_at} withTime /></td>
                    <td className="px-2 py-1.5 font-mono text-xs">{r.action}</td>
                    <td className="px-2 py-1.5">{r.category}</td>
                    <td className="px-2 py-1.5"><RolePill role={OUTCOME_ROLE[r.outcome as keyof typeof OUTCOME_ROLE] ?? 'neutral'}>{r.outcome}</RolePill></td>
                    <td className="px-2 py-1.5">{r.actor}{r.actor_role ? <span className="text-xs text-fg-muted"> ({r.actor_role})</span> : null}</td>
                    <td className="px-2 py-1.5 font-mono text-xs">{r.patient_mrn ?? ''}</td>
                    <td className="px-2 py-1.5 text-xs">{r.resource_type}{r.resource_id ? ` ${r.resource_id.slice(0, 8)}` : ''}</td>
                    <td className="max-w-72 px-2 py-1.5 font-mono text-[11px] break-all text-fg-muted">{Object.keys(r.details).length ? JSON.stringify(r.details) : ''}</td>
                    <td className="px-2 py-1.5 font-mono text-[11px] text-fg-subtle">{r.row_hash}</td>
                  </tr>))}</tbody>
              </table>
            </div>
            <div className="mt-3 flex gap-2">
              {before && <Button variant="outline" size="sm" onClick={() => setBefore(undefined)}>Newest</Button>}
              {q.data.next_before_seq && <Button variant="outline" size="sm" onClick={() => setBefore(q.data.next_before_seq!)}>Older</Button>}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
