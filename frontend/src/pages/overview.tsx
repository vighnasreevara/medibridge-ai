import { Link, useNavigate } from '@tanstack/react-router';

import { Kpi, Loading, ErrorState, PageHeader, Panel, StatusBadge, Time, EmptyState } from '@/components/common';
import { useAuth } from '@/lib/auth';
import { useLive } from '@/lib/live';
import { todayIso, useHealth, useOverview, useWorklist } from '@/lib/queries';

const EVENT_LABEL: Record<string, string> = {
  'run.started': 'Workflow started',
  'run.completed': 'Workflow completed',
  'run.failed': 'Workflow failed',
  'run.interrupted': 'Workflow interrupted',
  'node.started': 'Agent started',
  'node.completed': 'Agent finished',
  'node.failed': 'Agent failed',
  'gate.waiting': 'Waiting for a person',
  'gate.resolved': 'Approval recorded',
  'handoff.drafted': 'Handoff drafted',
  'handoff.approved': 'Handoff approved',
  'document.indexed': 'Document indexed',
};

function TodayWorklist() {
  const navigate = useNavigate();
  const q = useWorklist({ day: todayIso(), scope: 'all' });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  if (!q.data.items.length) return <EmptyState title="No encounters today in your care team" />;
  return (
    <ul className="divide-y">
      {q.data.items.map((e) => (
        <li key={e.id}>
          <button type="button" onClick={() => navigate({ to: '/encounters/$encounterId', params: { encounterId: e.id } })}
            className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-1 py-2.5 text-left hover:bg-surface-2">
            <Time iso={e.started_at} withTime mode="abs" />
            <span className="min-w-40 flex-1 font-medium">{e.patient.display_name}
              <span className="ml-2 text-xs font-normal text-fg-muted">{e.patient.mrn}</span></span>
            <span className="text-sm text-fg-muted">{e.chief_complaint || 'No complaint recorded'}</span>
            <StatusBadge status={e.status} />
          </button>
        </li>
      ))}
    </ul>
  );
}

export function OverviewPage() {
  const { me, can } = useAuth();
  const overview = useOverview();
  const health = useHealth();
  const live = useLive();
  const o = overview.data;
  return (
    <>
      <PageHeader title={`Good ${new Date().getHours() < 12 ? 'morning' : 'afternoon'}, ${me.display_name}`}
        subtitle={o ? (o.scope === 'care_team' ? 'Counts for patients in your care team.' : 'Counts for the whole demo hospital (no patient details).') : undefined} />
      {overview.isError && <ErrorState error={overview.error} retry={() => overview.refetch()} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Encounters today" value={o?.encounters_today ?? '-'} />
        <Kpi label="Awaiting intake" value={o?.awaiting_intake ?? '-'} />
        <Kpi label="In progress" value={o?.in_progress ?? '-'} tone={o?.in_progress ? 'run' : undefined} />
        <Kpi label="Awaiting doctor" value={o?.awaiting_review ?? '-'} tone={o?.awaiting_review ? 'human' : undefined} />
        <Kpi label="Draft handoffs" value={o?.draft_handoffs ?? '-'} tone={o?.draft_handoffs ? 'warn' : undefined} />
        <Kpi label="Pending approvals" value={o?.pending_approvals ?? '-'} tone={o?.pending_approvals ? 'human' : undefined} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        {can('encounter.read') ? (
          <Panel title="Today's encounters" subtitle="Your care team, in arrival order" actions={<Link to="/encounters" className="text-sm">All encounters</Link>}>
            <TodayWorklist />
          </Panel>
        ) : (
          <Panel title="Workflow status" subtitle="Runs by status">
            {o && Object.keys(o.runs_by_status).length ? (
              <ul className="flex flex-wrap gap-2">
                {Object.entries(o.runs_by_status).map(([status, n]) => (
                  <li key={status} className="flex items-center gap-2 rounded-md border px-3 py-1.5"><StatusBadge status={status} /> <span className="tnum font-semibold">{n}</span></li>
                ))}
              </ul>
            ) : <EmptyState title="No workflow runs yet" />}
          </Panel>
        )}

        <div className="flex flex-col gap-4">
          <Panel title="System health" subtitle={health.data ? <>Checked <Time iso={health.data.checked_at} mode="rel" /></> : undefined}>
            {health.isPending ? <Loading /> : health.isError ? <ErrorState error={health.error} retry={() => health.refetch()} /> : (
              <ul className="flex flex-col gap-2">
                {health.data.components.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-3">
                    <span className="text-sm font-medium">{c.label}</span>
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-xs text-fg-muted">{c.summary}</span>
                      <StatusBadge status={c.status} />
                    </span>
                  </li>
                ))}
                <li className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium">Live updates</span>
                  <StatusBadge status={live.status === 'live' ? 'pass' : live.status === 'offline' ? 'fail' : 'warn'}
                    label={live.status === 'live' ? 'Connected' : live.status} />
                </li>
              </ul>
            )}
          </Panel>
          <Panel title="Recent activity" subtitle="Workflow events (no clinical content)">
            {!o ? <Loading /> : o.recent_events.length === 0 ? <EmptyState title="No workflow activity yet" /> : (
              <ol className="flex flex-col gap-2">
                {o.recent_events.slice(0, 8).map((ev) => (
                  <li key={ev.seq} className="flex items-center justify-between gap-2 text-sm">
                    <span className="min-w-0 truncate">{EVENT_LABEL[ev.type] ?? ev.type}{ev.node ? <span className="text-fg-muted"> - {ev.node.replace(/_/g, ' ')}</span> : null}</span>
                    <span className="shrink-0 text-xs text-fg-muted"><Time iso={ev.created_at} mode="rel" /></span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
