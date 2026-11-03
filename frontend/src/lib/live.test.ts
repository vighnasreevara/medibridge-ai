import { afterEach, describe, expect, it, vi } from 'vitest';

import { keysForEvent, LiveClient } from './live';
import type { WorkflowEventView } from './types';

type Listener = (e: MessageEvent<string>) => void;

class FakeES {
  static last: FakeES | null = null;
  listeners: Record<string, Listener[]> = {};
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeES.last = this; }
  addEventListener(type: string, fn: Listener) { (this.listeners[type] ??= []).push(fn); }
  emit(type: string, data: unknown) { for (const fn of this.listeners[type] ?? []) fn({ data: JSON.stringify(data) } as MessageEvent<string>); }
  close() { this.closed = true; }
}

const ev = (seq: number, extra: Partial<WorkflowEventView> = {}): WorkflowEventView => ({
  seq, type: 'node.completed', run_id: 'r1', encounter_id: 'e1', node: 'history', status: 'succeeded', created_at: '2026-10-20T00:00:00Z', ...extra,
});

afterEach(() => vi.useRealTimers());

describe('keysForEvent', () => {
  it('invalidates the run, encounter and overview, never treats events as state', () => {
    const keys = keysForEvent(ev(1, { patient_id: 'p1' })).map((k) => JSON.stringify(k));
    expect(keys).toEqual(expect.arrayContaining(['["overview"]', '["run","r1"]', '["encounter","e1"]', '["patient","p1"]']));
  });
  it('gate events also refresh approvals and transcripts', () => {
    const keys = keysForEvent(ev(2, { type: 'gate.waiting' })).map((k) => k[0]);
    expect(keys).toEqual(expect.arrayContaining(['approvals', 'transcription', 'handoffs']));
  });
});

describe('LiveClient', () => {
  it('goes live on hello, dedupes by seq and batches invalidations', () => {
    vi.useFakeTimers();
    const invalidate = vi.fn();
    const client = new LiveClient(invalidate, '/api/events/stream', FakeES as unknown as typeof EventSource);
    client.start();
    FakeES.last!.emit('hello', { last_seq: 0 });
    expect(client.getState().status).toBe('live');
    FakeES.last!.emit('event', ev(5));
    FakeES.last!.emit('event', ev(5)); // duplicate
    vi.advanceTimersByTime(400);
    expect(client.getState().recent).toHaveLength(1);
    expect(client.getState().lastSeq).toBe(5);
    const calls = invalidate.mock.calls.map((c) => JSON.stringify(c[0]));
    expect(new Set(calls).size).toBe(calls.length); // each key once per batch
  });

  it('reconnects with backoff after an error and resumes from the last seq', () => {
    vi.useFakeTimers();
    const client = new LiveClient(vi.fn(), '/api/events/stream', FakeES as unknown as typeof EventSource);
    client.start();
    const first = FakeES.last!;
    first.emit('hello', {});
    first.emit('event', ev(7));
    first.onerror!();
    expect(client.getState().status).toBe('reconnecting');
    expect(first.closed).toBe(true);
    vi.advanceTimersByTime(600);
    expect(FakeES.last).not.toBe(first);
    expect(FakeES.last!.url).toContain('after=7');
  });

  it('stops when the server ends the session', () => {
    const client = new LiveClient(vi.fn(), '/api/events/stream', FakeES as unknown as typeof EventSource);
    client.start();
    FakeES.last!.emit('session_ended', {});
    expect(client.getState().status).toBe('offline');
  });
});
