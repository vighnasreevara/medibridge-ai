import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Eye, FileText, Search, Upload } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { PatientPicker } from '@/components/clinical';
import { EmptyState, ErrorState, KV, Loading, PageHeader, Panel, RolePill, StatusBadge, Time, fmtDate } from '@/components/common';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { api, qs } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useDocument, useDocuments, useDocumentText } from '@/lib/queries';
import type { DocumentView, SearchEvidence } from '@/lib/types';

const DOC_TYPES = ['lab_report', 'imaging_report', 'discharge_summary', 'referral_letter', 'clinical_note', 'pathology_report', 'medication_list', 'other'];

function UploadDialog({ patientId, open, onOpenChange }: { patientId: string; open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [type, setType] = useState('lab_report');
  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append('patient_id', patientId);
      fd.append('document_type', type);
      fd.append('title', title);
      if (date) fd.append('clinical_date', date);
      fd.append('file', file.current!.files![0]);
      return api.post('/api/documents', fd);
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['patient', patientId] }); toast.success('Uploaded; ingestion queued'); onOpenChange(false); },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Upload a report</DialogTitle>
          <DialogDescription>PDF or plain text. It is stored locally, scanned for identifiers, and indexed for this patient only.</DialogDescription></DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5"><Label htmlFor="up-title">Title</Label><Input id="up-title" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5"><Label>Type</Label>
              <Select value={type} onValueChange={setType}><SelectTrigger aria-label="Document type"><SelectValue /></SelectTrigger>
                <SelectContent>{DOC_TYPES.map((t) => <SelectItem key={t} value={t}>{t.replace(/_/g, ' ')}</SelectItem>)}</SelectContent></Select></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="up-date">Clinical date</Label><Input id="up-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></div>
          </div>
          <input ref={file} type="file" accept="application/pdf,text/plain" aria-label="File" />
          {upload.isError && <p role="alert" className="text-sm text-fail">{(upload.error as Error).message}</p>}
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={title.length < 2 || upload.isPending} onClick={() => upload.mutate()}>Upload</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SearchBox({ patientId }: { patientId: string }) {
  const [q, setQ] = useState('');
  const search = useMutation({
    mutationFn: (query: string) => api.get<{ evidence: SearchEvidence[]; coverage: { documents_on_file: number; indexed: number; failed: unknown[] } }>(`/api/documents/search${qs({ patient_id: patientId, q: query })}`),
  });
  return (
    <div className="flex flex-col gap-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 2) search.mutate(q.trim()); }}>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search within this patient's reports, e.g. 'ejection fraction'" aria-label="Search reports" />
        <Button type="submit" disabled={search.isPending}><Search /> Search</Button>
      </form>
      {search.isError && <ErrorState error={search.error} />}
      {search.data && (search.data.evidence.length === 0
        ? <p className="text-sm text-fg-muted">No matching passages in {search.data.coverage.indexed} indexed document(s).{search.data.coverage.failed.length ? ` ${search.data.coverage.failed.length} document(s) could not be searched.` : ''}</p>
        : <ol className="flex flex-col gap-2">{search.data.evidence.map((e) => (
          <li key={e.evidence_id} className="rounded-lg border p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm"><Link to="/records/$documentId" params={{ documentId: e.document_id }} className="font-medium">{e.title}</Link>
              <span className="text-fg-muted">{fmtDate(e.clinical_date)} - {e.section}{e.page ? `, p.${e.page}` : ''}</span>
              <span className="ml-auto font-mono text-xs text-fg-subtle">score {e.score}</span></div>
            <p className="mt-1 text-sm whitespace-pre-wrap text-fg-muted">{e.text.slice(0, 400)}{e.text.length > 400 ? '...' : ''}</p>
          </li>))}</ol>)}
    </div>
  );
}

export function RecordsPage() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [patient, setPatient] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const docs = useDocuments(patient ?? '');
  const columns = useMemo<ColumnDef<DocumentView>[]>(() => [
    { accessorKey: 'clinical_date', header: 'Date', cell: ({ getValue }) => <Time iso={getValue<string>()} /> },
    { accessorKey: 'title', header: 'Document', cell: ({ row }) => <span className="inline-flex items-center gap-1.5 font-medium"><FileText className="size-3.5" />{row.original.title}</span> },
    { accessorKey: 'document_type', header: 'Type', cell: ({ getValue }) => <span className="capitalize">{getValue<string>().replace(/_/g, ' ')}</span> },
    { accessorKey: 'source_name', header: 'Source' },
    { accessorKey: 'current_version', header: 'Version' },
    { accessorKey: 'ingestion_status', header: 'Search index', cell: ({ row }) => <span className="flex flex-col"><StatusBadge status={row.original.ingestion_status} />
      {row.original.ingestion_error && <span className="text-xs text-fail">{row.original.ingestion_error}</span>}</span> },
  ], []);
  return (
    <>
      <PageHeader title="Medical records" subtitle="Authorized reports for patients in your care team. Text is shown with identifiers removed."
        actions={<PatientPicker value={patient} onChange={setPatient} />} />
      {!patient ? <Panel><EmptyState title="Choose a patient" /></Panel> : (
        <div className="flex flex-col gap-4">
          <Panel title="Search reports" subtitle="Semantic + keyword search restricted to this patient."><SearchBox patientId={patient} /></Panel>
          <Panel title="Document library" actions={can('document.upload') && <Button size="sm" variant="outline" onClick={() => setUploading(true)}><Upload /> Upload</Button>}>
            {docs.isPending ? <Loading /> : docs.isError ? <ErrorState error={docs.error} /> : (
              <DataTable label="Documents" data={docs.data.items} columns={columns} rowKey={(r) => r.id}
                onRowClick={(r) => navigate({ to: '/records/$documentId', params: { documentId: r.id } })}
                initialSort={[{ id: 'clinical_date', desc: true }]} empty={<EmptyState title="No documents on file" body="Absence of reports is not a normal finding." />} />
            )}
          </Panel>
          {uploading && <UploadDialog patientId={patient} open={uploading} onOpenChange={setUploading} />}
        </div>
      )}
    </>
  );
}

export function DocumentPage() {
  const { documentId } = useParams({ from: '/records/$documentId' });
  const doc = useDocument(documentId);
  const text = useDocumentText(documentId);
  const [reason, setReason] = useState('');
  const [asking, setAsking] = useState(false);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  if (doc.isPending) return <Loading />;
  if (doc.isError) return <ErrorState error={doc.error} />;
  const d = doc.data;
  return (
    <>
      <PageHeader crumbs={<Link to="/records">Medical records</Link>} title={d.title}
        subtitle={<>{d.document_type.replace(/_/g, ' ')} - {fmtDate(d.clinical_date)} - {d.source_name}</>}
        actions={<Button variant="outline" size="sm" onClick={() => setAsking(true)} disabled={d.mime_type !== 'application/pdf'}><Eye /> View original</Button>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Panel title="Report text" subtitle="Identifiers removed for display. Each block is a citable chunk.">
          {text.isPending ? <Loading /> : text.isError ? <ErrorState error={text.error} /> :
            text.data.ingestion_status === 'failed' ? (
              <EmptyState title="This document could not be read" body={`Ingestion failed (${text.data.ingestion_error}). Its content is unavailable - this is not a normal finding.`} />
            ) : !text.data.chunks.length ? <EmptyState title="Not yet extracted" body="Ingestion is pending." /> : (
              <ol className="flex flex-col gap-3">{text.data.chunks.map((c) => (
                <li key={c.index} id={`chunk-${c.index}`} className="rounded-lg border p-3">
                  <p className="mb-1 flex justify-between text-xs text-fg-muted"><span>{c.section}{c.page_from ? ` - page ${c.page_from}` : ''}</span><span className="font-mono">chunk {c.index}</span></p>
                  <p className="text-sm leading-6 whitespace-pre-wrap">{c.text}</p>
                </li>))}</ol>
            )}
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel title="Source">
            <KV items={[{ k: 'Version', v: `${d.current_version}` }, { k: 'Status', v: <StatusBadge status={d.ingestion_status} /> },
              { k: 'Pages', v: d.page_count ?? '-' }, { k: 'Size', v: d.size_bytes ? `${(d.size_bytes / 1024).toFixed(0)} KB` : '-' },
              { k: 'SHA-256', v: <span className="font-mono text-xs">{d.content_sha256?.slice(0, 16)}...</span> },
              { k: 'Embedding', v: d.embedding_model ?? '-' }, { k: 'Indexed', v: <Time iso={d.indexed_at} withTime /> },
              { k: 'Identifiers', v: d.phi_scan?.length ? d.phi_scan.map((p) => <RolePill key={p.kind} role="warn" className="mr-1">{p.kind} {p.count}</RolePill>) : 'none found' }]} />
          </Panel>
          <Panel title="Versions">
            <ul className="flex flex-col gap-1 text-sm">{d.versions.map((v) => (
              <li key={v.version} className="flex justify-between gap-2"><span>v{v.version} {v.change_reason && `- ${v.change_reason}`}</span><span className="text-xs text-fg-muted"><Time iso={v.created_at} /></span></li>))}</ul>
          </Panel>
        </div>
      </div>
      {originalUrl && (
        <Panel title="Original document (unredacted)" className="mt-4" actions={<Button size="sm" variant="ghost" onClick={() => setOriginalUrl(null)}>Close</Button>}>
          <iframe title="Original document" src={originalUrl} className="h-[75vh] w-full rounded-md border" />
        </Panel>
      )}
      <Dialog open={asking} onOpenChange={setAsking}>
        <DialogContent>
          <DialogHeader><DialogTitle>View the unredacted original</DialogTitle>
            <DialogDescription>The original may contain contact details and identifiers. State why you need it; your access is recorded in the audit log.</DialogDescription></DialogHeader>
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (at least 5 characters)" aria-label="Reason" />
          <DialogFooter><Button variant="outline" onClick={() => setAsking(false)}>Cancel</Button>
            <Button disabled={reason.trim().length < 5} onClick={() => { setOriginalUrl(`/api/documents/${documentId}/original${qs({ reason: reason.trim() })}`); setAsking(false); }}>Open original</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
