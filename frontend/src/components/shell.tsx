// Application shell. Mission-control layout pattern (sidebar with status,
// compact header with live badge, theme toggle on "t"), built with shadcn/ui primitives.
import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import {
  Activity, ClipboardCheck, Eye, FileText, Gauge, HeartPulse, LayoutDashboard, LogOut, Menu, Mic, Moon, Pill,
  ScrollText, Settings, Stethoscope, Sun, Users, Workflow,
} from 'lucide-react';
import { useEffect, useState, type ComponentType } from 'react';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { useAuth } from '@/lib/auth';
import { useLive } from '@/lib/live';
import { cn } from '@/lib/utils';

export interface Section {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  permission: string;
  group: 'Care' | 'Operations' | 'Governance';
}

// Only sections whose routes are implemented are listed here.
export const SECTIONS: Section[] = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, permission: 'agents.monitor', group: 'Care' },
  { to: '/patients', label: 'Patients', icon: Users, permission: 'registry.search', group: 'Care' },
  { to: '/intake', label: 'Intake', icon: Mic, permission: 'intake.record', group: 'Care' },
  { to: '/encounters', label: 'Encounters', icon: Stethoscope, permission: 'encounter.read', group: 'Care' },
  { to: '/handoffs', label: 'Handoffs', icon: ClipboardCheck, permission: 'handoff.read', group: 'Care' },
  { to: '/records', label: 'Medical records', icon: FileText, permission: 'document.read', group: 'Care' },
  { to: '/medication', label: 'Medication & safety', icon: Pill, permission: 'medication.read', group: 'Care' },
  { to: '/mission-control', label: 'Agent mission control', icon: Workflow, permission: 'agents.monitor', group: 'Operations' },
  { to: '/approvals', label: 'Workflow & approvals', icon: Activity, permission: 'agents.monitor', group: 'Operations' },
  { to: '/observability', label: 'Observability', icon: Gauge, permission: 'observability.read', group: 'Operations' },
  { to: '/audit', label: 'Audit & governance', icon: ScrollText, permission: 'audit.read', group: 'Governance' },
  { to: '/admin', label: 'Administration', icon: Settings, permission: 'admin.config', group: 'Governance' },
];

export function useAvailableSections(registered: Set<string>): Section[] {
  const { can } = useAuth();
  return SECTIONS.filter((s) => registered.has(s.to) && can(s.permission));
}

export function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'));
  const [cvd, setCvd] = useState(() => document.documentElement.dataset.cvd === 'on');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('mb-theme', theme); } catch { /* storage unavailable */ }
  }, [theme]);
  useEffect(() => {
    if (cvd) document.documentElement.dataset.cvd = 'on';
    else delete document.documentElement.dataset.cvd;
    try { localStorage.setItem('mb-cvd', cvd ? 'on' : 'off'); } catch { /* storage unavailable */ }
  }, [cvd]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== 't' || e.ctrlKey || e.metaKey || e.altKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable) return;
      setTheme((v) => (v === 'dark' ? 'light' : 'dark'));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return { theme, setTheme, cvd, setCvd };
}

function LiveBadge() {
  const live = useLive();
  const map = {
    live: { text: 'Live', cls: 'text-pass' },
    connecting: { text: 'Connecting', cls: 'text-fg-muted' },
    reconnecting: { text: 'Reconnecting', cls: 'text-warn' },
    offline: { text: 'Offline', cls: 'text-fail' },
  } as const;
  const s = map[live.status];
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium', s.cls)} role="status" aria-live="polite"
      title={live.lastMessageAt ? `Last server message ${new Date(live.lastMessageAt).toLocaleTimeString()}` : undefined}>
      {live.status === 'live' ? <span className="live-dot" aria-hidden /> : <span className="size-2 rounded-full bg-current" aria-hidden />}
      {s.text}
    </span>
  );
}

function NavList({ sections, onNavigate }: { sections: Section[]; onNavigate?: () => void }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const groups = ['Care', 'Operations', 'Governance'] as const;
  return (
    <nav aria-label="Main" className="flex flex-col gap-4">
      {groups.map((g) => {
        const items = sections.filter((s) => s.group === g);
        if (!items.length) return null;
        return (
          <div key={g}>
            <p className="label px-3 pb-1">{g}</p>
            <ul className="flex flex-col gap-0.5">
              {items.map((s) => {
                const active = s.to === '/' ? path === '/' : path.startsWith(s.to);
                const Icon = s.icon;
                return (
                  <li key={s.to}>
                    <Link
                      to={s.to}
                      onClick={onNavigate}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm text-fg-muted no-underline transition-colors hover:bg-surface-2 hover:text-fg',
                        active && 'bg-brand-tint font-medium text-fg',
                      )}
                    >
                      <Icon className={cn('size-4 shrink-0', active && 'text-brand')} />
                      <span className="truncate">{s.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-3">
      <span className="flex size-8 items-center justify-center rounded-lg bg-brand text-brand-ink"><HeartPulse className="size-4" /></span>
      <div className="leading-tight">
        <p className="text-sm font-bold">MediBridge AI</p>
        <p className="text-[11px] text-fg-subtle">Cardiology intake &amp; handoff</p>
      </div>
    </div>
  );
}

export function AppShell({ registered }: { registered: Set<string> }) {
  const { me, signOut } = useAuth();
  const sections = useAvailableSections(registered);
  const { theme, setTheme, cvd, setCvd } = useTheme();
  const [mobileNav, setMobileNav] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const current = sections.find((s) => (s.to === '/' ? path === '/' : path.startsWith(s.to)));

  return (
    <div className="flex min-h-full">
      <a href="#main" className="skip-link">Skip to content</a>
      <aside className="no-print sticky top-0 hidden h-screen w-[var(--sidebar-w)] shrink-0 flex-col gap-5 overflow-y-auto border-r bg-surface py-4 lg:flex">
        <Brand />
        <NavList sections={sections} />
        <div className="mt-auto px-3 text-[11px] leading-4 text-fg-subtle">
          Synthetic-data research and demo platform. Not approved for clinical use. AI output must be reviewed by a clinician.
        </div>
      </aside>
      <Sheet open={mobileNav} onOpenChange={setMobileNav}>
        <SheetContent side="left" className="w-72 gap-5 overflow-y-auto py-4">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Brand />
          <NavList sections={sections} onNavigate={() => setMobileNav(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="no-print bg-warn-tint px-4 py-1 text-center text-xs font-medium text-warn" role="note">
          Synthetic data only - not for clinical use. Every AI output requires clinician review.
        </div>
        <header className="no-print sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-surface/95 px-4 backdrop-blur">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation" onClick={() => setMobileNav(true)}>
            <Menu />
          </Button>
          <p className="min-w-0 truncate text-sm font-semibold">{current?.label ?? 'MediBridge AI'}</p>
          <div className="ml-auto flex items-center gap-2">
            <LiveBadge />
            <Button variant="ghost" size="icon" aria-label={cvd ? 'Turn off colour-blind palette' : 'Turn on colour-blind palette'}
              aria-pressed={cvd} onClick={() => setCvd(!cvd)} title="Colour-blind palette">
              <Eye className={cn(cvd && 'text-brand')} />
            </Button>
            <Button variant="ghost" size="icon" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme (t)`}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? <Sun /> : <Moon />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="max-w-56" aria-label={me.display_name}>
                  <span className="hidden truncate sm:inline">{me.display_name}</span>
                  <span className="sm:hidden" aria-hidden>
                    {me.display_name.replace(/^Dr\.\s*/, '').split(/\s+/).map((w) => w[0]).slice(0, 2).join('')}
                  </span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <p className="truncate">{me.display_name}</p>
                  <p className="text-xs font-normal text-fg-muted">{me.role_label}</p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void signOut()}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
