import { useQueryClient } from '@tanstack/react-query';
import { HeartPulse, Loader2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { api, ApiError } from '@/lib/api';
import type { Me } from '@/lib/types';

const REASONS: Record<string, string> = {
  idle_timeout: 'You were signed out after a period of inactivity.',
  absolute_timeout: 'Your session reached its maximum length. Please sign in again.',
  epoch_changed: 'Your access changed. Please sign in again.',
  password_changed: 'Your password changed. Please sign in again.',
  revoked: 'This session was ended.',
  logout: 'You have signed out.',
};

export function LoginPage({ reason }: { reason?: string }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api.post<Me>('/api/auth/login', { email, password });
      setPassword('');
      qc.setQueryData(['me'], me);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Sign-in failed. Check that the API is running.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-brand text-brand-ink"><HeartPulse className="size-5" /></span>
          <div>
            <h1 className="text-lg">MediBridge AI</h1>
            <p className="text-xs text-fg-muted">Patient intake and clinical handoff</p>
          </div>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-4 rounded-xl border bg-surface p-6" aria-describedby="login-note">
          {reason && REASONS[reason] && (
            <Alert><AlertDescription>{REASONS[reason]}</AlertDescription></Alert>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error} aria-describedby={error ? 'login-error' : undefined} />
          </div>
          {error && <p id="login-error" role="alert" className="text-sm text-fail">{error}</p>}
          <Button type="submit" disabled={busy}>{busy && <Loader2 className="animate-spin" />} Sign in</Button>
        </form>
        <p id="login-note" className="mt-4 text-xs leading-5 text-fg-muted">
          Synthetic-data research and demo platform. Not approved for clinical use. Demo accounts are created by the local
          setup script and listed in <code>var/demo-credentials.txt</code>.
        </p>
      </div>
    </main>
  );
}
