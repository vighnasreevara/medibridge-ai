import { useNavigate } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Lock, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { EmptyState, ErrorState, Loading, PageHeader, Panel, RolePill, fmtDate } from '@/components/common';
import { DataTable } from '@/components/data-table';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePatientSearch } from '@/lib/queries';
import type { PatientSummary } from '@/lib/types';

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function PatientsPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState<'all' | 'mine'>('mine');
  const q = useDebounced(query.trim());
  const search = usePatientSearch(q, scope);

  const columns = useMemo<ColumnDef<PatientSummary>[]>(() => [
    { accessorKey: 'display_name', header: 'Name', cell: ({ row }) => <span className="font-medium">{row.original.display_name}</span> },
    { accessorKey: 'mrn', header: 'MRN', cell: ({ getValue }) => <span className="font-mono text-xs">{getValue<string>()}</span> },
    { accessorKey: 'birth_date', header: 'Date of birth', cell: ({ row }) => <span className="tnum">{fmtDate(row.original.birth_date)} ({row.original.age})</span> },
    { accessorKey: 'sex_at_birth', header: 'Sex', cell: ({ getValue }) => <span className="capitalize">{getValue<string>()}</span> },
    {
      id: 'access', header: 'Access', enableSorting: false,
      cell: ({ row }) => row.original.in_care_team
        ? <RolePill role="pass">Care team</RolePill>
        : <RolePill role="neutral"><Lock className="size-3" /> Registry only</RolePill>,
    },
  ], []);

  function open(p: PatientSummary) {
    if (!p.in_care_team) {
      toast.info('This patient is not in your care team', {
        description: 'You can see registry details only. Starting an encounter for them adds you to the care team.',
      });
      return;
    }
    void navigate({ to: '/patients/$patientId', params: { patientId: p.id } });
  }

  return (
    <>
      <PageHeader title="Patient registry" subtitle="Search shows minimal demographics. Clinical records open only for patients in your care team." />
      <Panel>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="relative min-w-64 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-subtle" aria-hidden />
            <Input type="search" placeholder="Name, MRN (MB000000) or date of birth (YYYY-MM-DD)" className="pl-8"
              aria-label="Search patients" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <Tabs value={scope} onValueChange={(v) => setScope(v as 'all' | 'mine')}>
            <TabsList aria-label="Which patients">
              <TabsTrigger value="mine">My care team</TabsTrigger>
              <TabsTrigger value="all">Whole registry</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {search.isPending ? <Loading label="Searching" /> : search.isError ? <ErrorState error={search.error} retry={() => search.refetch()} /> : (
          <DataTable
            label="Patients"
            data={search.data.items}
            columns={columns}
            onRowClick={open}
            rowKey={(p) => p.id}
            initialSort={[{ id: 'display_name', desc: false }]}
            empty={<EmptyState title={q ? 'No matching patients' : 'No patients in this view'} body={q.length === 1 ? 'Type at least 2 characters.' : undefined} />}
            mobileCard={(p) => (
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-medium">{p.display_name}</p>
                  <p className="text-xs text-fg-muted">{p.mrn} - {fmtDate(p.birth_date)} ({p.age})</p>
                </div>
                {p.in_care_team ? <RolePill role="pass">Care team</RolePill> : <RolePill role="neutral"><Lock className="size-3" /></RolePill>}
              </div>
            )}
          />
        )}
      </Panel>
    </>
  );
}
