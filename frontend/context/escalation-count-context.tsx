'use client';

import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react';
import { useAuth } from './auth-context';

interface EscalationCountContextValue {
  count: number;
  refresh: () => void;
}

const EscalationCountContext = createContext<EscalationCountContextValue>({
  count: 0,
  refresh: () => {},
});

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export function EscalationCountProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [count, setCount] = useState(0);

  const refresh = useCallback(() => {
    if (!user) return;
    fetch(`${API}/escalations/me`, { credentials: 'include' })
      .then((r) => r.json())
      .then((data: unknown[]) => setCount(data.length))
      .catch(() => {});
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <EscalationCountContext.Provider value={{ count, refresh }}>
      {children}
    </EscalationCountContext.Provider>
  );
}

export function useEscalationCount() {
  return useContext(EscalationCountContext);
}
