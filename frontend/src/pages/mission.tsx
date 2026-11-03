import { Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react';
import { memo, useEffect, useMemo, useState } from 'react';

import { nodeStatus } from '@/components/clinical';
import { EmptyState, ErrorState, GlyphSvg, Loading, PageHeader, Panel, RolePill, StatusBadge, Time } from '@/components/common';
import { FlowCanvas, arrow } from '@/components/flow';
import { ROLE_CLASSES, statusDef } from '@/domain/status';
import { useLive } from '@/lib/live';
import { useA2AStatus, useRun, useRuns, useTopology } from '@/lib/queries';
import type { AgentServiceStatus, ExecutionView, RunView } from '@/lib/types';
import { cn } from '@/lib/utils';

interface AgentNodeData extends Record<string, unknown> { label: string; agent: string; gate: boolean; remote: boolean; status: string; ms: number | null; selected: boolean; attempts: number }

const AgentNode = memo(({ data }: NodeProps<Node<AgentNodeData>>) => {
  const def = statusDef(data.status === 'pending' ? 'queued' : data.status);
  const r = ROLE_CLASSES[def.role];
  return (
    <div className={cn('w-[170px] rounded-md border px-2 py-1.5 shadow-sm', data.gate ? 'border-dashed' : '', data.status === 'waiting_human' ? `${r.bg} ${r.border} border-2` : 'border-line bg-surface',
      data.selected && 'ring-2 ring-brand')}>
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
      <div className="flex items-center gap-1.5">
        <span className={r.fg}><GlyphSvg glyph={def.glyph} size={13} /></span>
        <span className="truncate text-[12px] font-semibold">{data.label}</span>
        {data.remote && <span className="ml-auto rounded border border-line px-1 text-[9px] font-semibold uppercase tracking-wide text-fg-muted" title="Runs as a separate agent service, called over A2A">A2A</span>}
      </div>
      <div className={cn('flex justify-between text-[11px]', r.fg)}>
        <span>{data.status === 'pending' ? 'not started' : def.label}{data.attempts > 1 ? ` (x${data.attempts})` : ''}</span>
        {data.ms != null && <span className="tnum text-fg-muted">{(data.ms / 1000).toFixed(1)}s</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
    </div>
  );
});

const NODE_LABEL: Record<string, string> = {
  supervisor_start: 'Orchestrator: plan', registry_check: 'Patient Registry', scribe_transcribe: 'Scribe: transcribe',
  transcript_review: 'Gate: transcript', scribe_extract: 'Scribe: extract', chart_review: 'Chart (EHR)',
  evidence_retrieval: 'Evidence Retrieval', medication_screen: 'Medication Safety', handoff_brief: 'Handoff Brief',
  handoff_review: 'Gate: doctor review', order_draft: 'Order Entry: draft', orders_review: 'Gate: orders',
  order_safety_check: 'Order check (A2A peer)', safety_review: 'Gate: safety review', chart_commit: 'Chart: commit',
  after_visit_summary: 'After-Visit Summary', finalize: 'Orchestrator: finalize',
};

function statusFor(run: RunView | undefined, node: string): string {
  if (!run) return 'pending';
  if (node === 'supervisor_start' || node === 'finalize') {
    const ex = (run.executions ?? []).filter((e) => e.node === node);
    if (node === 'finalize') return run.ended_at ? (run.status === 'completed' ? 'succeeded' : run.status) : 'pending';
    return ex.at(-1)?.status ?? 'pending';
  }
  return nodeStatus(run, node);
}

function ExecutionTable({ rows }: { rows: ExecutionView[] }) {
  if (!rows.length) return <EmptyState title="No executions recorded for this step" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label="Executions">
        <thead><tr className="border-b text-left">{['Kind', 'Agent / tool', 'Attempt', 'Status', 'Duration', 'Model', 'Tokens in/out', 'Error'].map((h) => <th key={h} className="label px-2 py-1.5">{h}</th>)}</tr></thead>
        <tbody>{rows.map((x) => (
          <tr key={x.id} className="border-b last:border-0">
            <td className="px-2 py-1.5">{x.kind}</td>
            <td className="px-2 py-1.5 font-mono text-xs">{x.tool_name ?? x.agent_id}</td>
            <td className="px-2 py-1.5 tnum">{x.attempt}</td>
            <td className="px-2 py-1.5"><StatusBadge status={x.status} /></td>
            <td className="px-2 py-1.5 tnum">{x.duration_ms != null ? `${(x.duration_ms / 1000).toFixed(2)} s` : '-'}</td>
            <td className="px-2 py-1.5 text-xs">{x.model ?? '-'}{x.load_ms ? ` (load ${(x.load_ms / 1000).toFixed(1)}s)` : ''}</td>
            <td className="px-2 py-1.5 tnum text-xs">{x.prompt_tokens != null ? `${x.prompt_tokens} / ${x.completion_tokens}` : '-'}</td>
            <td className="px-2 py-1.5 text-xs text-fail">{x.error_code ?? ''}{x.error_class ? ` (${x.error_class})` : ''}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function AgentServices() {
  const q = useA2AStatus();
  const up = (q.data?.agents ?? []).filter((a) => a.status === 'up').length;
  return (
    <Panel title="Agent services (A2A)" subtitle={q.data?.transport === 'inprocess' ? 'AGENT_TRANSPORT=inprocess: agents run inside the API process.'
      : 'Each specialist agent is its own local process. Status is read from its agent card every 15 s.'}
      actions={q.data && q.data.transport === 'a2a' && <RolePill role={up === q.data.agents.length ? 'pass' : 'fail'}>{up}/{q.data.agents.length} up</RolePill>}>
      {q.isPending ? <Loading /> : q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Agent services">
            <thead><tr className="border-b text-left">{['Agent', 'Status', 'Endpoint (JSON-RPC)', 'Skills', 'Card'].map((h) => <th key={h} className="label px-2 py-1.5">{h}</th>)}</tr></thead>
            <tbody>{q.data.agents.map((a: AgentServiceStatus) => (
              <tr key={a.id} className="border-b last:border-0 align-top">
                <td className="px-2 py-1.5">{a.label}</td>
                <td className="px-2 py-1.5"><StatusBadge status={a.status} />{a.problem && <div className="text-xs text-fg-muted">{a.problem}</div>}</td>
                <td className="px-2 py-1.5 font-mono text-xs">{a.rpc_url ?? '-'}</td>
                <td className="px-2 py-1.5 font-mono text-xs">{a.skills.join(', ')}</td>
                <td className="px-2 py-1.5 text-xs text-fg-muted">{a.card ? `${a.card.protocol.join(', ')} - v${a.card.version}${a.latency_ms != null ? ` - ${a.latency_ms} ms` : ''}` : '-'}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function MissionControlPage() {
  const topo = useTopology();
  const runs = useRuns();
  const [runId, setRunId] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState<string | null>(null);
  const live = useLive();
  useEffect(() => { if (!runId && runs.data?.items[0]) setRunId(runs.data.items[0].id); }, [runs.data, runId]);
  const run = useRun(runId);
  const { nodes, edges } = useMemo(() => {
    if (!topo.data) return { nodes: [], edges: [] };
    const ex = run.data?.executions ?? [];
    const nodes: Node<AgentNodeData>[] = topo.data.graph.nodes.map((n) => {
      const mine = ex.filter((e) => e.node === n.id && e.kind === 'agent');
      return { id: n.id, type: 'agent', position: { x: 0, y: 0 }, width: 170, height: 46,
        data: { label: NODE_LABEL[n.id] ?? n.id, agent: n.agent, gate: n.gate, remote: !!n.remote && topo.data.transport === 'a2a', status: statusFor(run.data, n.id),
                ms: mine.at(-1)?.duration_ms ?? null, selected: n.id === nodeId, attempts: mine.length } };
    });
    const edges: Edge[] = topo.data.graph.edges.map((e, i) => {
      const done = ['succeeded', 'completed'].includes(statusFor(run.data, e.source));
      // Failure routes to "finalize" exist on every step; draw them faintly so the main path stays readable.
      const errorRoute = e.target === 'finalize' && !['after_visit_summary', 'safety_review', 'orders_review', 'handoff_review'].includes(e.source);
      return { id: `e${i}`, source: e.source, target: e.target, type: 'smoothstep', markerEnd: arrow,
        style: errorRoute ? { stroke: 'var(--line)', strokeDasharray: '2 4', opacity: 0.45 }
          : { stroke: done ? 'var(--line-strong)' : 'var(--line)', strokeDasharray: e.conditional && !done ? '4 3' : undefined } };
    });
    // Snake layout: rows of five, alternating direction, so 14 steps stay legible in one panel.
    const order = nodes.map((n) => n.id);
    const PER_ROW = 5;
    const placed = nodes.map((n) => {
      const i = order.indexOf(n.id);
      const row = Math.floor(i / PER_ROW);
      const col = row % 2 === 0 ? i % PER_ROW : PER_ROW - 1 - (i % PER_ROW);
      return { ...n, position: { x: col * 215, y: row * 110 } };
    });
    return { nodes: placed, edges };
  }, [topo.data, run.data, nodeId]);
  const nodeTypes = useMemo(() => ({ agent: AgentNode }), []);
  if (topo.isPending) return <Loading />;
  if (topo.isError) return <ErrorState error={topo.error} />;
  const selectedRows = (run.data?.executions ?? []).filter((e) => !nodeId || e.node === nodeId || (e.kind === 'tool' && (run.data?.executions ?? []).find((p) => p.id === e.parent_id)?.node === nodeId));
  const transport = topo.data.transport;
  return (
    <>
      <PageHeader title="Agent mission control" subtitle={<>Live execution of the cardiology intake workflow (Care Orchestrator + eight agents). {transport === 'a2a' ? 'The supervisor runs here; the eight specialist agents are separate services it calls over A2A.' : 'All agents run in the API process.'} States come only from recorded executions. Live feed: {live.status}.</>} />
      <div className="grid gap-4 xl:grid-cols-[300px_1fr]">
        <Panel title="Runs" subtitle="Most recent first (identifiers only)">
          {runs.isPending ? <Loading /> : !runs.data?.items.length ? <EmptyState title="No runs yet" /> : (
            <ul className="flex max-h-[560px] flex-col gap-1 overflow-y-auto">{runs.data.items.map((r) => (
              <li key={r.id}><button type="button" onClick={() => { setRunId(r.id); setNodeId(null); }}
                className={cn('flex w-full flex-col rounded-md px-2 py-1.5 text-left hover:bg-surface-2', r.id === runId && 'bg-brand-tint')}>
                <span className="flex items-center justify-between gap-2"><span className="font-mono text-xs">{r.id.slice(0, 8)}</span><StatusBadge status={r.status} /></span>
                <span className="text-xs text-fg-muted"><Time iso={r.created_at} mode="rel" /> - {r.executions} executions{r.waiting_gate ? ` - at ${r.waiting_gate}` : ''}</span>
              </button></li>))}</ul>
          )}
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel title="LangGraph workflow" subtitle="Click a step to see its executions and tool calls. Dashed borders are human gates."
            actions={run.data && <StatusBadge status={run.data.status} />}>
            <FlowCanvas nodes={nodes} edges={edges} nodeTypes={nodeTypes} label="Agent workflow graph" height={340} onNodeClick={setNodeId} />
            <details className="mt-2 text-sm"><summary className="cursor-pointer text-fg-muted">List view</summary>
              <ol className="mt-1 list-decimal pl-5">{nodes.map((n) => <li key={n.id}>{n.data.label}: {n.data.status}</li>)}</ol></details>
          </Panel>
          <Panel title={nodeId ? `Executions - ${NODE_LABEL[nodeId] ?? nodeId}` : 'All executions in this run'}
            actions={nodeId && <button type="button" className="text-sm" onClick={() => setNodeId(null)}>Show all</button>}>
            {run.isPending ? <Loading /> : <ExecutionTable rows={selectedRows} />}
          </Panel>
          <AgentServices />
          <Panel title="Agents and tools">
            <div className="grid gap-4 md:grid-cols-2">
              <ul className="flex flex-col gap-2 text-sm">{Object.entries(topo.data.agents).map(([id, a]) => (
                <li key={id}><span className="flex justify-between gap-2"><span className="font-medium">{a.label}</span>
                  <RolePill role={a.kind.includes('llm') ? 'run' : 'neutral'}>{a.kind}</RolePill></span>
                  {a.role && <span className="block text-xs text-fg-muted">{a.role}</span>}</li>))}</ul>
              <ul className="flex flex-col gap-1 text-sm">{topo.data.tools.map((t) => (
                <li key={t.name} className="flex justify-between gap-2"><span className="font-mono text-xs">{t.name}</span>
                  <span className="flex gap-1"><RolePill role={t.kind === 'write' ? 'human' : 'pass'}>{t.kind}</RolePill>{t.llm_callable && <RolePill role="run">model-callable</RolePill>}</span></li>))}</ul>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
