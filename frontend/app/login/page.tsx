'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';

interface DemoUser {
  id: string;
  name: string;
  email: string;
  role: string;
  business_unit: string | null;
  country: string | null;
  site: string | null;
  requester_role: string | null;
}

const ROLE_LABELS: Record<string, string> = {
  requester: 'Requester',
  approver: 'Procurement Manager',
  category_head: 'Category Head',
  compliance_reviewer: 'Compliance Reviewer',
};

const ROLE_COLORS: Record<string, string> = {
  requester: 'text-blue-700 border-blue-300 bg-blue-50',
  approver: 'text-emerald-700 border-emerald-300 bg-emerald-50',
  category_head: 'text-purple-700 border-purple-300 bg-purple-50',
  compliance_reviewer: 'text-amber-700 border-amber-300 bg-amber-50',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function LoginPage() {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [loading, setLoading] = useState(false);
  const { user, loading: authLoading, refresh } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!authLoading && user) {
      router.replace('/dashboard');
    }
  }, [user, authLoading, router]);

  useEffect(() => {
    fetch(`${API}/auth/users`)
      .then((r) => r.json())
      .then(setUsers)
      .catch(() => toast.error('Could not load demo users — is the backend running?'));
  }, []);

  const login = async (userId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ user_id: userId }),
      });
      if (!res.ok) throw new Error('Login failed');
      await refresh();
      router.push('/dashboard');
    } catch {
      toast.error('Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-2xl space-y-6">
        <div className="text-center space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">ChainIQ</h1>
          <p className="text-muted-foreground">Select a demo user to continue</p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {users.map((u) => (
            <button
              key={u.id}
              onClick={() => login(u.id)}
              disabled={loading}
              className="text-left"
            >
              <Card className="hover:border-primary hover:shadow-md transition-all cursor-pointer h-full">
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base">{u.name}</CardTitle>
                    <Badge
                      variant="outline"
                      className={`text-xs shrink-0 ${ROLE_COLORS[u.role] ?? ''}`}
                    >
                      {ROLE_LABELS[u.role] ?? u.role}
                    </Badge>
                  </div>
                  <CardDescription className="text-xs">{u.email}</CardDescription>
                </CardHeader>
                <CardContent className="pt-0 text-xs text-muted-foreground space-y-0.5">
                  {u.requester_role && <p>{u.requester_role}</p>}
                  {u.business_unit && <p>{u.business_unit}</p>}
                  {u.site && u.country && <p>{u.site}, {u.country}</p>}
                </CardContent>
              </Card>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
