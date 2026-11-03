// Live workflow events over SSE.
//
// Design (mission-control live layer):
// - Events are witnesses, not state: each event only invalidates the TanStack Query keys it affects,
//   and the UI re-reads the authoritative API. Progress shown on screen therefore always comes from the server.
// - Reconnect with exponential backoff (0.5 s -> 15 s) and a 40 s silence watchdog (server heartbeats every 15 s).
// - Duplicate events are dropped by seq; the browser resumes with Last-Event-ID automatically.
import { useSyncExternalStore } from 'react';

import type { WorkflowEventView } from './types';

export type LiveStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface LiveState {
  status: LiveStatus;
  lastSeq: number;
  lastMessageAt: number | null;
  reconnects: number;
  recent: WorkflowEventView[];
}

type Invalidate = (key: readonly unknown[]) => void;

const WATCHDOG_MS = 40_000;
const MAX_BACKOFF_MS = 15_000;
const RECENT_LIMIT = 100;

export function keysForEvent(e: WorkflowEventView): (readonly unknown[])[] {
  const keys: (readonly unknown[])[] = [['overview'], ['events']];
  if (e.run_id) keys.push(['run', e.run_id], ['runs']);
  if (e.encounter_id) keys.push(['encounter', e.encounter_id], ['worklist']);
  if (e.type.startsWith('handoff.') || e.type.startsWith('gate.')) keys.push(['handoffs'], ['approvals'], ['transcription']);
  if (e.type.startsWith('documentation.') || e.type.startsWith('run.')) keys.push(['worklist'], ['patient']);
  if (e.type.startsWith('document.')) keys.push(['documents']);
  if (e.patient_id) keys.push(['patient', e.patient_id]);
  return keys;
}

export class LiveClient {
  private state: LiveState = { status: 'offline', lastSeq: 0, lastMessageAt: null, reconnects: 0, recent: [] };
  private listeners = new Set<() => void>();
  private source: EventSource | null = null;
  private backoff = 500;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private seen = new Set<number>();
  private pending = new Map<string, readonly unknown[]>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly invalidate: Invalidate,
    private readonly url = '/api/events/stream',
    private readonly EventSourceImpl: typeof EventSource = EventSource,
  ) {}

  getState = (): LiveState => this.state;

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  start(): void {
    if (this.source) return;
    this.set({ status: this.state.reconnects ? 'reconnecting' : 'connecting' });
    const url = this.state.lastSeq ? `${this.url}?after=${this.state.lastSeq}` : this.url;
    const source = new this.EventSourceImpl(url, { withCredentials: true });
    this.source = source;
    source.addEventListener('hello', () => {
      this.backoff = 500;
      this.set({ status: 'live' });
      this.touch();
      if (this.state.reconnects) this.queue(['all']); // we may have missed events while disconnected
    });
    source.addEventListener('heartbeat', () => this.touch());
    source.addEventListener('event', (msg) => this.onEvent(msg as MessageEvent<string>));
    source.addEventListener('session_ended', () => this.stop('offline'));
    source.onerror = () => this.reconnect();
  }

  stop(status: LiveStatus = 'offline'): void {
    this.source?.close();
    this.source = null;
    if (this.watchdog) clearTimeout(this.watchdog);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.set({ status });
  }

  private onEvent(msg: MessageEvent<string>): void {
    this.touch();
    let event: WorkflowEventView;
    try {
      event = JSON.parse(msg.data);
    } catch {
      return;
    }
    if (this.seen.has(event.seq)) return;
    this.seen.add(event.seq);
    if (this.seen.size > 5000) this.seen = new Set([...this.seen].slice(-2500));
    this.set({
      lastSeq: Math.max(this.state.lastSeq, event.seq),
      recent: [event, ...this.state.recent].slice(0, RECENT_LIMIT),
    });
    for (const key of keysForEvent(event)) this.queue(key);
  }

  private queue(key: readonly unknown[]): void {
    this.pending.set(JSON.stringify(key), key);
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      const keys = [...this.pending.values()];
      this.pending.clear();
      for (const k of keys) this.invalidate(k);
    }, 300);
  }

  private touch(): void {
    this.set({ lastMessageAt: Date.now() });
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => this.reconnect(), WATCHDOG_MS);
  }

  private reconnect(): void {
    this.source?.close();
    this.source = null;
    if (this.watchdog) clearTimeout(this.watchdog);
    this.set({ status: 'reconnecting', reconnects: this.state.reconnects + 1 });
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
    this.retryTimer = setTimeout(() => this.start(), delay);
  }

  private set(patch: Partial<LiveState>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }
}

let client: LiveClient | null = null;

export function initLive(invalidate: Invalidate): LiveClient {
  client ??= new LiveClient(invalidate);
  return client;
}

export function getLive(): LiveClient | null {
  return client;
}

const OFFLINE: LiveState = { status: 'offline', lastSeq: 0, lastMessageAt: null, reconnects: 0, recent: [] };

export function useLive(): LiveState {
  return useSyncExternalStore(
    (fn) => client?.subscribe(fn) ?? (() => undefined),
    () => client?.getState() ?? OFFLINE,
  );
}
