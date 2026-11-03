import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { api, ApiError, onSignedOut } from './api';
import { getLive } from './live';
import type { Me } from './types';

interface AuthValue {
  me: Me;
  can: (permission: string) => boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthGate>');
  return value;
}

interface SessionProbe { authenticated: boolean; user?: Me; reason?: string | null }

let probeReason: string | undefined;

export function useMeQuery() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async ({ signal }) => {
      const probe = await api.get<SessionProbe>('/api/auth/session', signal);
      probeReason = probe.reason ?? undefined;
      return probe.authenticated && probe.user ? probe.user : null;
    },
    retry: false,
    staleTime: 60_000,
  });
}

/** Renders `signedOut` until a valid session exists, then provides the signed-in user. */
export function AuthGate({ children, signedOut }: { children: ReactNode; signedOut: (reason?: string) => ReactNode }) {
  const qc = useQueryClient();
  const me = useMeQuery();
  const [reason, setReason] = useState<string | undefined>();

  useEffect(
    () =>
      onSignedOut((why) => {
        setReason(why);
        getLive()?.stop();
        qc.setQueryData(['me'], null);
        qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
      }),
    [qc],
  );

  if (me.isPending) return null;
  if (!me.data) {
    const why = me.error instanceof ApiError ? (me.error.detail?.reason as string | undefined) : probeReason;
    return <>{signedOut(reason ?? (why === 'no_session' || why === 'unknown_session' ? undefined : why))}</>;
  }
  const value: AuthValue = {
    me: me.data,
    can: (p) => me.data!.permissions.includes(p),
    signOut: async () => {
      try {
        await api.post('/api/auth/logout');
      } finally {
        getLive()?.stop();
        qc.clear();
        qc.setQueryData(['me'], null);
        setReason('logout');
      }
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
