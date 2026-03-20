'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { ArrowRight, Building2, MapPin, Briefcase } from 'lucide-react';
import { ROLE_BADGE, ROLE_AVATAR, ROLE_LABELS } from '@/lib/colors';

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

const ROLE_DESCRIPTIONS: Record<string, string> = {
  requester: 'Submit and track procurement requests',
  approver: 'Review, approve or reject requests',
  category_head: 'Oversee category-level procurement decisions',
  compliance_reviewer: 'Ensure requests meet compliance standards',
};

const AVATARS: Record<string, string> = {
  'user-alice': 'AM',
  'user-bob': 'BS',
  'user-carol': 'CD',
  'user-dave': 'DP',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function LoginPage() {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [loggingIn, setLoggingIn] = useState<string | null>(null);
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
    setLoggingIn(userId);
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
      setLoggingIn(null);
    }
  };

  return (
    <div className="min-h-screen flex bg-background">
      {/* Left panel */}
      <div className="hidden lg:flex flex-col justify-between w-2/5 bg-primary p-12 text-primary-foreground">
        <div>
          <div className="flex items-center gap-2 mb-12">
            <div className="h-8 w-8 rounded-md bg-primary-foreground/20 flex items-center justify-center font-bold text-sm">CQ</div>
            <span className="font-semibold text-lg">ChainIQ</span>
          </div>
          <h1 className="text-4xl font-bold leading-tight tracking-tight mb-4">
            Procurement<br />Intelligence<br />Platform
          </h1>
          <p className="text-primary-foreground/70 text-base leading-relaxed mb-8">
            AI-powered procurement routing with policy validation, supplier shortlisting, and automated escalation workflows.
          </p>
          <div className="space-y-4">
            {[
              'Automated policy compliance checks',
              'Smart supplier shortlisting',
              'Role-based escalation routing',
            ].map((feat) => (
              <div key={feat} className="flex items-center gap-3 text-sm text-primary-foreground/80">
                <div className="h-1.5 w-1.5 rounded-full bg-primary-foreground/50 shrink-0" />
                {feat}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-lg">
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-6 lg:hidden">
              <div className="h-7 w-7 rounded-md bg-primary flex items-center justify-center font-bold text-primary-foreground text-xs">CQ</div>
              <span className="font-semibold">ChainIQ</span>
            </div>
            <h2 className="text-2xl font-bold tracking-tight">Demo Login</h2>
            <p className="text-muted-foreground mt-1 text-sm">Choose a role to explore the platform</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {users.map((u) => {
              const isLoading = loggingIn === u.id;
              const initials = AVATARS[u.id] ?? u.name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
              return (
                <button
                  key={u.id}
                  onClick={() => login(u.id)}
                  disabled={!!loggingIn}
                  className={`group text-left rounded-xl border p-4 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2
                    ${isLoading
                      ? 'border-primary bg-primary/5 shadow-md'
                      : 'border-border bg-card hover:border-primary hover:shadow-lg hover:scale-[1.03] hover:-translate-y-1'
                    }
                    ${loggingIn && !isLoading ? 'opacity-50' : ''}
                  `}
                >
                  <div className="flex items-start justify-between gap-2 mb-3">
                    <div className={`h-10 w-10 rounded-full flex items-center justify-center font-semibold text-sm shrink-0 ${ROLE_AVATAR[u.role] ?? 'bg-muted'}`}>
                      {isLoading ? (
                        <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                      ) : initials}
                    </div>
                    <Badge variant="outline" className={`text-[10px] px-1.5 shrink-0 ${ROLE_BADGE[u.role] ?? ''}`}>
                      {ROLE_LABELS[u.role] ?? u.role}
                    </Badge>
                  </div>

                  <div className="mb-2">
                    <p className="font-semibold text-sm leading-snug">{u.name}</p>
                    {u.requester_role && (
                      <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                        <Briefcase className="h-3 w-3 shrink-0" /> {u.requester_role}
                      </p>
                    )}
                  </div>

                  <div className="space-y-0.5 text-xs text-muted-foreground mb-3">
                    {u.business_unit && (
                      <p className="flex items-center gap-1">
                        <Building2 className="h-3 w-3 shrink-0" /> {u.business_unit}
                      </p>
                    )}
                    {u.site && u.country && (
                      <p className="flex items-center gap-1">
                        <MapPin className="h-3 w-3 shrink-0" /> {u.site}, {u.country}
                      </p>
                    )}
                  </div>

                  <p className="text-xs text-muted-foreground/70 leading-snug border-t pt-2 mt-auto">
                    {ROLE_DESCRIPTIONS[u.role] ?? ''}
                  </p>

                  <div className="mt-3 flex items-center gap-1 text-xs font-medium text-primary opacity-0 group-hover:opacity-100 transition-opacity">
                    Sign in as {u.name.split(' ')[0]}
                    <ArrowRight className="h-3 w-3" />
                  </div>
                </button>
              );
            })}
          </div>

          <p className="mt-6 text-center text-xs text-muted-foreground">
            This is a demo environment. No real credentials required.
          </p>
        </div>
      </div>
    </div>
  );
}
