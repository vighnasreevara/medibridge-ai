// One table for every status shown in the UI: label, role colour and glyph.
// Rule (mission-control visual language): colour means who acts next, and a glyph shape always
// accompanies colour so status is never conveyed by colour alone.

export type Role = 'fail' | 'tool' | 'human' | 'warn' | 'run' | 'neutral' | 'pass';
export type Glyph = 'check' | 'ring' | 'dashed-ring' | 'person' | 'triangle' | 'octagon' | 'diamond' | 'half' | 'slash' | 'dot';

export interface StatusDef {
  label: string;
  role: Role;
  glyph: Glyph;
  description: string;
}

export const ROLE_CLASSES: Record<Role, { fg: string; bg: string; border: string; dot: string }> = {
  fail: { fg: 'text-fail', bg: 'bg-fail-tint', border: 'border-fail', dot: 'bg-fail' },
  tool: { fg: 'text-tool', bg: 'bg-tool-tint', border: 'border-tool', dot: 'bg-tool' },
  human: { fg: 'text-human', bg: 'bg-human-tint', border: 'border-human', dot: 'bg-human' },
  warn: { fg: 'text-warn', bg: 'bg-warn-tint', border: 'border-warn', dot: 'bg-warn' },
  run: { fg: 'text-run', bg: 'bg-run-tint', border: 'border-run', dot: 'bg-run' },
  neutral: { fg: 'text-neutral', bg: 'bg-neutral-tint', border: 'border-line-strong', dot: 'bg-neutral' },
  pass: { fg: 'text-pass', bg: 'bg-pass-tint', border: 'border-pass', dot: 'bg-pass' },
};

const S = (label: string, role: Role, glyph: Glyph, description: string): StatusDef => ({ label, role, glyph, description });

export const STATUS: Record<string, StatusDef> = {
  // encounters
  planned: S('Awaiting intake', 'neutral', 'ring', 'Registered; intake has not started.'),
  in_progress: S('In progress', 'run', 'half', 'Intake or processing is under way.'),
  awaiting_review: S('Awaiting doctor', 'human', 'person', 'A doctor must review the handoff.'),
  finalized: S('Finalized', 'pass', 'check', 'Documentation approved and committed.'),
  cancelled: S('Cancelled', 'neutral', 'slash', 'Cancelled.'),
  entered_in_error: S('Entered in error', 'neutral', 'slash', 'Marked as entered in error.'),
  // workflow runs
  queued: S('Queued', 'neutral', 'dashed-ring', 'Waiting to start.'),
  running: S('Running', 'run', 'half', 'An agent is working.'),
  waiting_human: S('Needs a person', 'human', 'person', 'Paused at a human approval gate.'),
  completed: S('Completed', 'pass', 'check', 'Finished successfully.'),
  failed: S('Failed', 'fail', 'octagon', 'Stopped with an error.'),
  interrupted: S('Interrupted', 'tool', 'triangle', 'Stopped unexpectedly; can be resumed.'),
  blocked: S('Blocked', 'fail', 'octagon', 'A guardrail blocked this step.'),
  // executions
  succeeded: S('Succeeded', 'pass', 'check', 'Step succeeded.'),
  skipped: S('Skipped', 'neutral', 'slash', 'Step skipped.'),
  timed_out: S('Timed out', 'tool', 'triangle', 'Step exceeded its time limit.'),
  // handoffs
  generating: S('Generating', 'run', 'half', 'The handoff is being drafted.'),
  draft: S('Draft', 'warn', 'diamond', 'AI draft; not reviewed.'),
  in_review: S('In review', 'human', 'person', 'Submitted for doctor review.'),
  changes_requested: S('Changes requested', 'warn', 'diamond', 'Returned for changes.'),
  approved: S('Approved', 'pass', 'check', 'Approved by a doctor.'),
  rejected: S('Rejected', 'fail', 'octagon', 'Rejected by a doctor.'),
  superseded: S('Superseded', 'neutral', 'slash', 'Replaced by a newer version.'),
  // documents
  pending: S('Pending', 'neutral', 'dashed-ring', 'Waiting for ingestion.'),
  extracting: S('Extracting', 'run', 'half', 'Extracting text.'),
  extracted: S('Extracted', 'run', 'half', 'Text extracted; not yet indexed.'),
  indexing: S('Indexing', 'run', 'half', 'Building search index.'),
  indexed: S('Indexed', 'pass', 'check', 'Searchable.'),
  // health
  pass: S('Healthy', 'pass', 'check', 'Working.'),
  warn: S('Degraded', 'warn', 'triangle', 'Working with limitations.'),
  fail: S('Down', 'fail', 'octagon', 'Not working.'),
  // agent services (A2A)
  up: S('Up', 'pass', 'check', 'Serving its agent card.'),
  down: S('Unreachable', 'fail', 'octagon', 'The agent service did not answer.'),
  mismatch: S('Card mismatch', 'warn', 'triangle', 'The agent card does not offer the skills the supervisor expects.'),
  in_process: S('In process', 'neutral', 'dot', 'Runs inside the API process (AGENT_TRANSPORT=inprocess).'),
  // reconciliation
  recent: S('Reconciled', 'pass', 'check', 'Reviewed by a clinician in the last 6 months.'),
  stale: S('Stale', 'warn', 'triangle', 'Last reconciled more than 6 months ago.'),
  never_reconciled: S('Not reconciled', 'warn', 'triangle', 'Never reconciled by a clinician.'),
};

const UNKNOWN = S('Unknown', 'neutral', 'dot', 'Status not recognised.');

export function statusDef(status: string | null | undefined): StatusDef {
  return (status && STATUS[status]) || { ...UNKNOWN, label: status ? status.replace(/_/g, ' ') : 'Unknown' };
}

// Where a clinical fact came from (guardrail 10). Shown next to every historical statement.
export const SOURCE_KIND: Record<string, { label: string; role: Role; description: string }> = {
  patient_reported: { label: 'Patient-reported', role: 'warn', description: 'Stated by the patient; not verified.' },
  clinician_documented: { label: 'Clinician-documented', role: 'pass', description: 'Documented by a clinician.' },
  record_retrieved: { label: 'From record', role: 'run', description: 'Retrieved from a historical record.' },
  device: { label: 'Device', role: 'run', description: 'Measured by a device.' },
  unconfirmed: { label: 'Unconfirmed', role: 'tool', description: 'Not confirmed by any source.' },
  conflicting: { label: 'Conflicting', role: 'fail', description: 'Sources disagree; needs clinician review.' },
};
