import './styles.css';

import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { Toaster } from 'sonner';

import { TooltipProvider } from '@/components/ui/tooltip';
import { queryClient } from '@/lib/api';
import { AuthGate } from '@/lib/auth';
import { initLive } from '@/lib/live';
import { LoginPage } from '@/pages/login';
import { router } from '@/router';

function LiveStarter() {
  useEffect(() => {
    const live = initLive((key) => {
      if (key[0] === 'all') void queryClient.invalidateQueries();
      else void queryClient.invalidateQueries({ queryKey: key });
    });
    live.start();
    return () => live.stop();
  }, []);
  return null;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <AuthGate signedOut={(reason) => <LoginPage reason={reason} />}>
          <LiveStarter />
          <RouterProvider router={router} />
        </AuthGate>
        <Toaster position="bottom-right" richColors closeButton />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
