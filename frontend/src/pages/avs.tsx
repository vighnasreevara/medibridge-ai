import { Link, useParams } from '@tanstack/react-router';
import { Printer } from 'lucide-react';

import { EmptyState, ErrorState, Loading, PageHeader, Panel } from '@/components/common';
import { Button } from '@/components/ui/button';
import { useAfterVisitSummary, useEncounter } from '@/lib/queries';

import { AvsDocument } from './handoff';

/** Printable after-visit summary for the patient (this visit only). */
export function AvsPage() {
  const { encounterId } = useParams({ from: '/summary/$encounterId' });
  const enc = useEncounter(encounterId);
  const q = useAfterVisitSummary(encounterId);
  if (q.isPending) return <Loading label="Loading summary" />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const avs = q.data.avs;
  return (
    <>
      <div className="no-print">
        <PageHeader crumbs={<><Link to="/handoffs">Handoff briefs</Link> / <Link to="/handoffs/$encounterId" params={{ encounterId }}>Brief</Link></>}
          title={`After-visit summary - ${enc.data?.patient.display_name ?? ''}`}
          subtitle="What the patient takes home: this visit only."
          actions={avs && <Button onClick={() => window.print()}><Printer /> Print</Button>} />
      </div>
      <Panel className="mx-auto w-full max-w-3xl">
        {avs ? <AvsDocument avs={avs} /> : <EmptyState title="No summary yet" body="The summary is written after the encounter is committed to the record." />}
      </Panel>
    </>
  );
}
