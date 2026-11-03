import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';

import { EmptyState, ErrorState, Kpi, KV, Loading, PageHeader, Panel, RolePill } from '@/components/common';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useObservability } from '@/lib/queries';
import type { LatencyRow } from '@/lib/types';

const s = (ms: number | null | undefined) => (ms == null ? '-' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const axis = { stroke: 'var(--fg-subtle)', fontSize: 11, tickLine: false, axisLine: { stroke: 'var(--line-strong)' } };
const tooltipStyle = { contentStyle: { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--fg)', fontSize: 12 } };

function LatencyChart({ rows, label }: { rows: LatencyRow[]; label: string }) {
  const data = rows.filter((r) => r.p50_ms != null).map((r) => ({ name: r.name.replace(/_/g, ' '), p50: (r.p50_ms ?? 0) / 1000, p95: (r.p95_ms ?? 0) / 1000 }));
  if (!data.length) return <EmptyState title="No timed executions in this window" />;
  return (
    <>
      <ResponsiveContainer width="100%" height={Math.max(160, data.length * 44 + 40)}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24 }} barGap={2} barCategoryGap="28%" accessibilityLayer>
          <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
          <XAxis type="number" unit=" s" {...axis} />
          <YAxis type="category" dataKey="name" width={130} {...axis} />
          <Tooltip {...tooltipStyle} formatter={(v) => (typeof v === 'number' ? `${v.toFixed(2)} s` : String(v ?? ''))} cursor={{ fill: 'var(--surface-2)' }} />
          <Legend wrapperStyle={{ fontSize: 12, color: 'var(--fg-muted)' }} />
          <Bar dataKey="p50" name="median (p50)" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={14} />
          <Bar dataKey="p95" name="95th percentile" fill="var(--series-2)" radius={[0, 4, 4, 0]} maxBarSize={14} />
        </BarChart>
      </ResponsiveContainer>
      <details className="mt-1 text-sm"><summary className="cursor-pointer text-fg-muted">Table view</summary>
        <table className="mt-1 w-full text-sm" aria-label={label}><thead><tr className="border-b text-left">{['Name', 'Runs', 'p50', 'p95', 'Max', 'Failed', 'Blocked', 'Retries'].map((h) => <th key={h} className="label px-2 py-1">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.name} className="border-b last:border-0"><td className="px-2 py-1">{r.name}</td><td className="px-2 py-1 tnum">{r.runs}</td>
            <td className="px-2 py-1 tnum">{s(r.p50_ms)}</td><td className="px-2 py-1 tnum">{s(r.p95_ms)}</td><td className="px-2 py-1 tnum">{s(r.max_ms)}</td>
            <td className="px-2 py-1 tnum">{r.failed}</td><td className="px-2 py-1 tnum">{r.blocked}</td><td className="px-2 py-1 tnum">{r.retries}</td></tr>)}</tbody></table>
      </details>
    </>
  );
}

function LlmScatter({ calls }: { calls: { agent: string; model: string; ms: number; prompt_tokens: number | null }[] }) {
  const models = [...new Set(calls.map((c) => c.model))].slice(0, 3);
  if (!calls.length) return <EmptyState title="No model calls in this window" />;
  const colors = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ScatterChart margin={{ left: 8, right: 16, top: 8 }} accessibilityLayer>
        <CartesianGrid stroke="var(--chart-grid)" />
        <XAxis type="number" dataKey="prompt_tokens" name="prompt tokens" {...axis} />
        <YAxis type="number" dataKey="s" name="duration" unit=" s" {...axis} />
        <ZAxis range={[60, 60]} />
        <Tooltip {...tooltipStyle} formatter={(v, n) => (n === 'duration' && typeof v === 'number' ? `${v.toFixed(1)} s` : String(v ?? ''))} />
        <Legend wrapperStyle={{ fontSize: 12, color: 'var(--fg-muted)' }} />
        {models.map((m, i) => (
          <Scatter key={m} name={m} data={calls.filter((c) => c.model === m).map((c) => ({ ...c, s: c.ms / 1000 }))} fill={colors[i]} stroke="var(--surface)" strokeWidth={2} />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function ObservabilityPage() {
  const [hours, setHours] = useState(168);
  const q = useObservability(hours);
  return (
    <>
      <PageHeader title="Observability" subtitle="Local execution metrics. No transcript, document or patient content is collected here."
        actions={<Tabs value={String(hours)} onValueChange={(v) => setHours(Number(v))}><TabsList>
          <TabsTrigger value="24">24 h</TabsTrigger><TabsTrigger value="168">7 days</TabsTrigger><TabsTrigger value="720">30 days</TabsTrigger></TabsList></Tabs>} />
      {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (() => {
        const d = q.data;
        const failures = d.agents.reduce((n, a) => n + a.failed, 0);
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <Kpi label="Model calls" value={d.llm.calls} sub={`p50 ${s(d.llm.p50_ms)} - p95 ${s(d.llm.p95_ms)}`} />
              <Kpi label="Tokens (in / out)" value={`${(d.llm.prompt_tokens / 1000).toFixed(1)}k`} sub={`${(d.llm.completion_tokens / 1000).toFixed(1)}k generated (local count)`} />
              <Kpi label="Retrieval" value={s(d.retrieval.p50_ms)} sub={`${d.retrieval.calls} searches, p95 ${s(d.retrieval.p95_ms)}`} />
              <Kpi label="Failed agent steps" value={failures} tone={failures ? 'fail' : undefined} />
              <Kpi label="Blocked by guardrails" value={d.guardrail_events['node.blocked'] ?? 0} tone={(d.guardrail_events['node.blocked'] ?? 0) ? 'human' : undefined} />
              <Kpi label="Escalations raised" value={d.guardrail_events['escalation.raised'] ?? 0} sub={`${d.guardrail_events['escalation.acknowledged'] ?? 0} acknowledged`} />
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
              <Panel title="Agent latency" subtitle="Per agent, this window"><LatencyChart rows={d.agents} label="Agent latency table" /></Panel>
              <Panel title="Local model inference" subtitle="Each dot is one structured call (duration vs prompt size)"><LlmScatter calls={d.llm.recent} /></Panel>
            </div>
            {d.transport === 'a2a' && (
              <Panel title="Agent-to-agent calls" subtitle="Supervisor to agent service, per skill: the whole remote round trip, including the agent's own work">
                <LatencyChart rows={d.a2a} label="A2A call latency table" />
                {d.a2a.some((r) => r.errors && Object.keys(r.errors).length) && (
                  <KV items={d.a2a.flatMap((r) => Object.entries(r.errors ?? {}).map(([code, n]) => ({ k: `${r.name}: ${code}`, v: n })))} />
                )}
              </Panel>
            )}
            <div className="grid gap-4 xl:grid-cols-2">
              <Panel title="Tool execution time"><LatencyChart rows={d.tools} label="Tool latency table" /></Panel>
              <div className="flex flex-col gap-4">
                <Panel title="Workflow outcomes">
                  <ul className="flex flex-wrap gap-2">{Object.entries(d.runs_by_status).map(([k, v]) => <li key={k}><RolePill role={k === 'completed' ? 'pass' : k === 'failed' || k === 'blocked' ? 'fail' : k === 'waiting_human' ? 'human' : 'neutral'}>{k.replace('_', ' ')}: {v}</RolePill></li>)}</ul>
                </Panel>
                <Panel title="Guardrail and access outcomes">
                  <KV items={[...Object.entries(d.guardrail_events).map(([k, v]) => ({ k, v })), ...Object.entries(d.access_denials).map(([k, v]) => ({ k: `denied: ${k}`, v }))]} />
                </Panel>
                <Panel title="Models and tracing">
                  <KV items={[...Object.entries(d.models).map(([k, v]) => ({ k, v })),
                    { k: 'LangSmith', v: d.tracing.langsmith_enabled && d.tracing.endpoint_configured ? <RolePill role="warn">enabled (metadata only)</RolePill> : <RolePill role="pass">off - nothing leaves this machine</RolePill> }]} />
                </Panel>
              </div>
            </div>
          </div>
        );
      })()}
    </>
  );
}
