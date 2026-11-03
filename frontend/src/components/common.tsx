// Shared presentational components. Mission-control patterns (Panel, Kpi, StatusBadge,
// provenance badge, glyph shapes, Time with source), implemented for MediBridge.
import { AlertTriangle, Inbox, Loader2, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { ROLE_CLASSES, SOURCE_KIND, statusDef, type Glyph, type Role } from '@/domain/status';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';

export function GlyphSvg({ glyph, size = 12, className }: { glyph: Glyph; size?: number; className?: string }) {
  const p = { width: size, height: size, viewBox: '0 0 16 16', 'aria-hidden': true, className } as const;
  const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 2 } as const;
  switch (glyph) {
    case 'check':
      return <svg {...p}><circle cx="8" cy="8" r="7" fill="currentColor" /><path d="M4.5 8.2l2.2 2.2 4.8-4.8" stroke="var(--surface)" strokeWidth="2" fill="none" /></svg>;
    case 'ring':
      return <svg {...p}><circle cx="8" cy="8" r="6" {...stroke} /></svg>;
    case 'dashed-ring':
      return <svg {...p}><circle cx="8" cy="8" r="6" {...stroke} strokeDasharray="3 2.5" /></svg>;
    case 'half':
      return <svg {...p}><circle cx="8" cy="8" r="6" {...stroke} /><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" /></svg>;
    case 'person':
      return <svg {...p}><circle cx="8" cy="5" r="3" fill="currentColor" /><path d="M2.5 15c0-3.2 2.5-5.3 5.5-5.3s5.5 2.1 5.5 5.3z" fill="currentColor" /></svg>;
    case 'triangle':
      return <svg {...p}><path d="M8 1.5l7 13H1z" fill="currentColor" /><path d="M8 6v4M8 11.8v.4" stroke="var(--surface)" strokeWidth="1.8" /></svg>;
    case 'octagon':
      return <svg {...p}><path d="M5 1h6l4 4v6l-4 4H5l-4-4V5z" fill="currentColor" /><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="var(--surface)" strokeWidth="1.8" /></svg>;
    case 'diamond':
      return <svg {...p}><path d="M8 1l7 7-7 7-7-7z" fill="currentColor" /></svg>;
    case 'slash':
      return <svg {...p}><circle cx="8" cy="8" r="6" {...stroke} /><path d="M3.8 12.2l8.4-8.4" {...stroke} /></svg>;
    default:
      return <svg {...p}><circle cx="8" cy="8" r="4" fill="currentColor" /></svg>;
  }
}

export function StatusBadge({ status, label, className }: { status: string | null | undefined; label?: string; className?: string }) {
  const def = statusDef(status);
  const r = ROLE_CLASSES[def.role];
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap', r.bg, r.fg, className)}
      title={def.description}
    >
      <GlyphSvg glyph={def.glyph} />
      {label ?? def.label}
    </span>
  );
}

export function RolePill({ role, children, className }: { role: Role; children: ReactNode; className?: string }) {
  const r = ROLE_CLASSES[role];
  return <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', r.bg, r.fg, className)}>{children}</span>;
}

export function SourceBadge({ kind, certainty }: { kind: string; certainty?: string }) {
  const def = SOURCE_KIND[kind] ?? { label: kind, role: 'neutral' as Role, description: '' };
  return (
    <span className="inline-flex items-center gap-1">
      <RolePill role={def.role}>
        <span title={def.description}>{def.label}</span>
      </RolePill>
      {certainty && certainty !== 'stated' && <RolePill role="tool">{certainty === 'uncertain' ? 'Uncertain' : 'Inferred'}</RolePill>}
    </span>
  );
}

export function Panel({ title, subtitle, actions, children, className, id, tone }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string; tone?: Role;
}) {
  return (
    <section id={id} className={cn('rounded-[10px] border bg-surface', tone && ROLE_CLASSES[tone].border, className)} aria-labelledby={title && id ? `${id}-title` : undefined}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b px-4 py-3">
          <div className="min-w-0">
            {title && <h2 id={id ? `${id}-title` : undefined} className="text-sm font-semibold">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, tone, onClick }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Role; onClick?: () => void }) {
  const Comp = onClick ? 'button' : 'div';
  return (
    <Comp
      onClick={onClick}
      className={cn('flex min-w-0 flex-col rounded-[10px] border bg-surface px-4 py-3 text-left', onClick && 'transition-colors hover:bg-surface-2', tone && ROLE_CLASSES[tone].border)}
    >
      <span className="label">{label}</span>
      <span className={cn('kpi-value', tone && ROLE_CLASSES[tone].fg)}>{value}</span>
      {sub && <span className="truncate text-xs text-fg-muted">{sub}</span>}
    </Comp>
  );
}

export function KV({ items }: { items: { k: string; v: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map((i) => (
        <div key={i.k} className="contents">
          <dt className="text-fg-muted">{i.k}</dt>
          <dd className="min-w-0 break-words">{i.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({ title, body, action, icon }: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <span className="text-fg-subtle">{icon ?? <Inbox className="size-6" />}</span>
      <p className="font-medium">{title}</p>
      {body && <p className="max-w-md text-sm text-fg-muted">{body}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Something went wrong.';
  const notFound = error instanceof ApiError && error.status === 404;
  return (
    <div role="alert" className="flex flex-col items-center gap-2 rounded-[10px] border border-fail bg-fail-tint px-4 py-6 text-center">
      <AlertTriangle className="size-5 text-fail" aria-hidden />
      <p className="font-medium">{notFound ? 'Not found or not in your care team' : 'Could not load this'}</p>
      <p className="text-sm text-fg-muted">{notFound ? 'You may not have access to this record.' : message}</p>
      {retry && !notFound && (
        <Button variant="outline" size="sm" onClick={retry}><RefreshCw /> Try again</Button>
      )}
    </div>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-8 text-sm text-fg-muted" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}...
    </div>
  );
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

export function relTime(iso: string, now = Date.now()): string {
  const diff = (new Date(iso).getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), 'second');
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  if (abs < 86400 * 45) return rtf.format(Math.round(diff / 86400), 'day');
  if (abs < 86400 * 540) return rtf.format(Math.round(diff / (86400 * 30)), 'month');
  return rtf.format(Math.round(diff / (86400 * 365)), 'year');
}

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '-';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return withTime
    ? d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function Time({ iso, mode = 'abs', withTime = false }: { iso: string | null | undefined; mode?: 'abs' | 'rel' | 'both'; withTime?: boolean }) {
  if (!iso) return <span className="text-fg-subtle">-</span>;
  const abs = fmtDate(iso, withTime);
  const rel = relTime(iso);
  return (
    <time dateTime={iso} title={new Date(iso).toLocaleString()} className="tnum">
      {mode === 'rel' ? rel : mode === 'both' ? `${abs} (${rel})` : abs}
    </time>
  );
}

export function PageHeader({ title, subtitle, actions, crumbs }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; crumbs?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {crumbs && <div className="mb-1 text-xs text-fg-muted">{crumbs}</div>}
        <h1 className="truncate">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
