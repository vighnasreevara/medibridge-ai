import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { EmptyState, ErrorState, Loading } from '@/components/common';
import { usePatientResults } from '@/lib/queries';
import type { ResultSeries } from '@/lib/types';

// Small multiples: one measure per chart, each on its own axis (never two scales on one chart). Values are shown
// exactly as recorded; no normal/abnormal interpretation is added.
const ORDER = ['High-sensitivity troponin I', 'NT-proBNP', 'LV ejection fraction', 'LDL cholesterol', 'Total cholesterol',
  'HDL cholesterol', 'Triglycerides', 'INR', 'Potassium', 'Sodium', 'Creatinine', 'eGFR', 'HbA1c', 'Hemoglobin',
  'Systolic blood pressure', 'Diastolic blood pressure', 'Heart rate', 'Body weight', 'BMI'];
const axis = { stroke: 'var(--fg-subtle)', fontSize: 11, tickLine: false, axisLine: { stroke: 'var(--line-strong)' } };
const tooltipStyle = { contentStyle: { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--fg)', fontSize: 12 } };

const fmt = (v: number | null | undefined) => (v == null ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

function Mini({ s }: { s: ResultSeries }) {
  const data = s.points.filter((p) => p.value != null);
  const sparse = data.length <= 12;
  const delta = s.previous?.value != null && s.latest.value != null ? s.latest.value - s.previous.value : null;
  return (
    <figure className="rounded-lg border p-3" aria-label={`${s.name} trend`}>
      <figcaption className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold">{s.name}</span>
        <span className="tnum text-sm">{fmt(s.latest.value)} <span className="text-xs text-fg-muted">{s.unit}</span></span>
      </figcaption>
      <p className="text-xs text-fg-muted">Latest {s.latest.date}{delta != null ? ` - ${delta > 0 ? '+' : ''}${fmt(delta)} since ${s.previous!.date}` : ''} - {s.count} result(s)</p>
      {data.length > 1 ? (
        <ResponsiveContainer width="100%" height={110}>
          <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
            <XAxis dataKey="date" {...axis} minTickGap={40} />
            <YAxis {...axis} width={44} domain={['auto', 'auto']} />
            <Tooltip {...tooltipStyle} formatter={(v) => [`${fmt(typeof v === 'number' ? v : null)} ${s.unit ?? ''}`, s.name]} />
            {/* Straight segments: values between results were never measured. Dots only while they stay legible. */}
            <Line type="linear" dataKey="value" stroke="var(--series-1)" strokeWidth={sparse ? 2 : 1.5}
              dot={sparse ? { r: 3, strokeWidth: 2, stroke: 'var(--surface)', fill: 'var(--series-1)' } : false}
              activeDot={{ r: 5, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      ) : <p className="py-6 text-center text-xs text-fg-muted">A single result - no trend yet.</p>}
    </figure>
  );
}

export function ResultsTab({ id }: { id: string }) {
  const q = usePatientResults(id);
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const rank = (name: string) => (ORDER.includes(name) ? ORDER.indexOf(name) : ORDER.length);
  const items = [...q.data.items].sort((a, b) => rank(a.name) - rank(b.name));
  if (!items.length) return <EmptyState title="No results recorded" body="Cardiology results appear here once recorded or imported." />;
  const labs = items.filter((s) => s.category === 'laboratory');
  const vitals = items.filter((s) => s.category !== 'laboratory');
  return (
    <div className="flex flex-col gap-4">
      {[['Laboratory and imaging measurements', labs], ['Vital signs', vitals]].map(([title, list]) => (list as ResultSeries[]).length > 0 && (
        <section key={title as string}>
          <h3 className="mb-2 text-sm font-semibold">{title as string}</h3>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{(list as ResultSeries[]).map((s) => <Mini key={s.name} s={s} />)}</div>
        </section>
      ))}
      <details className="text-sm"><summary className="cursor-pointer text-fg-muted">Table view</summary>
        <table className="mt-2 w-full text-sm" aria-label="Results table">
          <thead><tr className="border-b text-left">{['Measure', 'Latest', 'Date', 'Previous', 'Date', 'Results'].map((h, i) => <th key={i} className="label px-2 py-1">{h}</th>)}</tr></thead>
          <tbody>{items.map((s) => (
            <tr key={s.name} className="border-b last:border-0"><td className="px-2 py-1">{s.name}</td>
              <td className="tnum px-2 py-1">{fmt(s.latest.value)} {s.unit}</td><td className="px-2 py-1">{s.latest.date}</td>
              <td className="tnum px-2 py-1">{s.previous ? `${fmt(s.previous.value)} ${s.unit ?? ''}` : '-'}</td><td className="px-2 py-1">{s.previous?.date ?? '-'}</td>
              <td className="tnum px-2 py-1">{s.count}</td></tr>))}</tbody>
        </table>
      </details>
    </div>
  );
}
