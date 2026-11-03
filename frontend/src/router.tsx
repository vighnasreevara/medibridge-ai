import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router';

import { EmptyState } from '@/components/common';
import { AppShell } from '@/components/shell';
import { EncounterPage } from '@/pages/encounter';
import { EncountersPage } from '@/pages/encounters';
import { OverviewPage } from '@/pages/overview';
import { PatientsPage } from '@/pages/patients';
import { EncounterWorkspace } from '@/pages/workspace';

// Top-level section paths that have a real page; the sidebar lists only these (filtered by permission).
const REGISTERED = new Set(['/', '/patients', '/intake', '/encounters', '/handoffs', '/records', '/medication',
  '/mission-control', '/approvals', '/observability', '/audit', '/admin']);

const rootRoute = createRootRoute({
  component: () => <AppShell registered={REGISTERED} />,
  notFoundComponent: () => <EmptyState title="Page not found" body="This page does not exist or you do not have access." />,
});

const lazy = <T extends string>(loader: () => Promise<Record<T, unknown>>, name: T) =>
  lazyRouteComponent(loader as () => Promise<Record<string, never>>, name as never);

const routes = {
  overview: createRoute({ getParentRoute: () => rootRoute, path: '/', component: OverviewPage }),
  patients: createRoute({ getParentRoute: () => rootRoute, path: '/patients', component: PatientsPage }),
  patient: createRoute({ getParentRoute: () => rootRoute, path: '/patients/$patientId', component: lazy(() => import('@/pages/patient'), 'PatientPage') }),
  intake: createRoute({ getParentRoute: () => rootRoute, path: '/intake', component: lazy(() => import('@/pages/intake'), 'IntakeListPage') }),
  intakeConsole: createRoute({ getParentRoute: () => rootRoute, path: '/intake/$encounterId', component: lazy(() => import('@/pages/intake'), 'IntakeConsolePage') }),
  encounters: createRoute({ getParentRoute: () => rootRoute, path: '/encounters', component: EncountersPage }),
  encounter: createRoute({ getParentRoute: () => rootRoute, path: '/encounters/$encounterId', component: () => <EncounterPage><EncounterWorkspace /></EncounterPage> }),
  handoffs: createRoute({ getParentRoute: () => rootRoute, path: '/handoffs', component: lazy(() => import('@/pages/handoff'), 'HandoffsPage') }),
  handoff: createRoute({ getParentRoute: () => rootRoute, path: '/handoffs/$encounterId', component: lazy(() => import('@/pages/handoff'), 'HandoffPage') }),
  summary: createRoute({ getParentRoute: () => rootRoute, path: '/summary/$encounterId', component: lazy(() => import('@/pages/avs'), 'AvsPage') }),
  records: createRoute({ getParentRoute: () => rootRoute, path: '/records', component: lazy(() => import('@/pages/records'), 'RecordsPage') }),
  document: createRoute({ getParentRoute: () => rootRoute, path: '/records/$documentId', component: lazy(() => import('@/pages/records'), 'DocumentPage') }),
  medication: createRoute({ getParentRoute: () => rootRoute, path: '/medication', component: lazy(() => import('@/pages/medication'), 'MedicationPage') }),
  mission: createRoute({ getParentRoute: () => rootRoute, path: '/mission-control', component: lazy(() => import('@/pages/mission'), 'MissionControlPage') }),
  approvals: createRoute({ getParentRoute: () => rootRoute, path: '/approvals', component: lazy(() => import('@/pages/approvals'), 'ApprovalsPage') }),
  observability: createRoute({ getParentRoute: () => rootRoute, path: '/observability', component: lazy(() => import('@/pages/observability'), 'ObservabilityPage') }),
  audit: createRoute({ getParentRoute: () => rootRoute, path: '/audit', component: lazy(() => import('@/pages/audit'), 'AuditPage') }),
  admin: createRoute({ getParentRoute: () => rootRoute, path: '/admin', component: lazy(() => import('@/pages/admin'), 'AdminPage') }),
};

export const router = createRouter({
  routeTree: rootRoute.addChildren(Object.values(routes)),
  defaultPreload: 'intent',
  scrollRestoration: true,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
