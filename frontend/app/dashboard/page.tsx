'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  FileText, Clock, CheckCircle, AlertCircle,
  ArrowUpRight, Plus, ChevronRight, Activity,
} from 'lucide-react';

interface Stats {
  by_status: Record<string, number>;
  total: number;
}

interface ActivityEntry {
  id: string;
  action: string;
  notes: string | null;
  created_at: string;
  actor_name: string;
  request_id: string;
  request_title: string;
}

interface Escalation {
  id: string;
  request_id: string;
  type: string;
  status: string;
  message: string | null;
  created_at: string;
}

const ACTION_COLORS: Record<string, string> = {
  submitted: 'text-blue-600',
  approved: 'text-emerald-600',
  rejected: 'text-red-600',
  reviewed: 'text-indigo-600',
  escalated: 'text-orange-600',
  clarified: 'text-amber-600',
  withdrawn: 'text-gray-500',
};

const TYPE_LABELS: Record<string, string> = {
  requester_clarification: 'Clarification needed',
  procurement_manager: 'Procurement Manager review',
  category_head: 'Category Head review',
  compliance: 'Compliance review',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function DashboardPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<Stats | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);

  useEffect(() => {
    Promise.all([
      fetch(`${API}/requests/stats`, { credentials: 'include' }).then(r => r.json()),
      fetch(`${API}/requests/activity?limit=8`, { credentials: 'include' }).then(r => r.json()),
      fetch(`${API}/escalations/me`, { credentials: 'include' }).then(r => r.json()),
    ]).then(([s, a, e]) => {
      setStats(s);
      setActivity(Array.isArray(a) ? a : []);
      setEscalations(Array.isArray(e) ? e : []);
    }).catch(() => {});
  }, []);

  const isRequester = user?.role === 'requester';
  const s = stats?.by_status ?? {};

  const kpis = isRequester
    ? [
        { label: 'Total Requests', value: stats?.total ?? 0, icon: FileText, color: 'text-blue-600', bg: 'bg-blue-50 dark:bg-blue-950/30', href: '/dashboard/cases' },
        { label: 'Pending Review', value: (s.new ?? 0) + (s.pending_review ?? 0), icon: Clock, color: 'text-amber-600', bg: 'bg-amber-50 dark:bg-amber-950/30', href: '/dashboard/cases' },
        { label: 'Approved', value: s.approved ?? 0, icon: CheckCircle, color: 'text-emerald-600', bg: 'bg-emerald-50 dark:bg-emerald-950/30', href: '/dashboard/cases' },
        { label: 'Needs Attention', value: escalations.length, icon: AlertCircle, color: 'text-orange-600', bg: 'bg-orange-50 dark:bg-orange-950/30', href: '/dashboard/escalations' },
      ]
    : [
        { label: 'Total Requests', value: stats?.total ?? 0, icon: FileText, color: 'text-blue-600', bg: 'bg-blue-50 dark:bg-blue-950/30', href: '/dashboard/cases' },
        { label: 'Pending Review', value: (s.new ?? 0) + (s.pending_review ?? 0), icon: Clock, color: 'text-amber-600', bg: 'bg-amber-50 dark:bg-amber-950/30', href: '/dashboard/cases' },
        { label: 'Escalated', value: s.escalated ?? 0, icon: AlertCircle, color: 'text-orange-600', bg: 'bg-orange-50 dark:bg-orange-950/30', href: '/dashboard/cases' },
        { label: 'Approved', value: s.approved ?? 0, icon: CheckCircle, color: 'text-emerald-600', bg: 'bg-emerald-50 dark:bg-emerald-950/30', href: '/dashboard/cases' },
      ];

  return (
    <div className="py-8 w-full max-w-5xl mx-auto space-y-8">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            Welcome back{user ? `, ${user.name.split(' ')[0]}` : ''}
          </h1>
          <p className="text-muted-foreground mt-1">Here's what's happening with your procurement requests.</p>
        </div>
        {isRequester && (
          <Button className="gap-2" onClick={() => router.push('/dashboard/create')}>
            <Plus className="h-4 w-4" /> New Request
          </Button>
        )}
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {kpis.map((kpi) => (
          <Card key={kpi.label} className="cursor-pointer hover:shadow-md transition-shadow" onClick={() => router.push(kpi.href)}>
            <CardContent className="p-3 flex items-center gap-3">
              <div className={`inline-flex p-1.5 rounded-md shrink-0 ${kpi.bg}`}>
                <kpi.icon className={`h-4 w-4 ${kpi.color}`} />
              </div>
              <div>
                <p className="text-xl font-bold leading-none">{kpi.value}</p>
                <p className="text-xs text-muted-foreground mt-1">{kpi.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Needs attention */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-orange-500" />
              Needs Your Attention
              {escalations.length > 0 && (
                <Badge className="bg-orange-500 text-white text-xs ml-auto">{escalations.length}</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {escalations.length === 0 ? (
              <div className="px-6 py-8 text-center text-muted-foreground">
                <CheckCircle className="h-8 w-8 mx-auto mb-2 opacity-30" />
                <p className="text-sm">You're all caught up!</p>
              </div>
            ) : (
              <div className="divide-y">
                {escalations.slice(0, 4).map((esc) => (
                  <div
                    key={esc.id}
                    className="px-6 py-3 hover:bg-muted/30 cursor-pointer flex items-start justify-between gap-3"
                    onClick={() => router.push(
                      isRequester
                        ? `/dashboard/clarify/${esc.request_id}`
                        : `/dashboard/analysis?id=${esc.request_id}`
                    )}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{TYPE_LABELS[esc.type] ?? esc.type}</p>
                      {esc.message && (
                        <p className="text-xs text-muted-foreground truncate mt-0.5">{esc.message}</p>
                      )}
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">{esc.request_id.slice(0, 8)}…</p>
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
                  </div>
                ))}
                {escalations.length > 4 && (
                  <div
                    className="px-6 py-3 text-xs text-center text-muted-foreground hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push('/dashboard/escalations')}
                  >
                    +{escalations.length - 4} more — View all
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent activity */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Activity className="h-4 w-4 text-primary" />
                Recent Activity
              </CardTitle>
              <Button variant="ghost" size="sm" className="text-xs h-7 gap-1" onClick={() => router.push('/dashboard/cases')}>
                All cases <ArrowUpRight className="h-3 w-3" />
              </Button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {activity.length === 0 ? (
              <div className="px-6 py-8 text-center text-sm text-muted-foreground">No activity yet.</div>
            ) : (
              <div className="divide-y">
                {activity.map((entry) => (
                  <div
                    key={entry.id}
                    className="px-6 py-3 hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/dashboard/analysis?id=${entry.request_id}`)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm truncate">
                          <span className="font-medium">{entry.actor_name}</span>
                          {' '}
                          <span className={ACTION_COLORS[entry.action] ?? 'text-muted-foreground'}>
                            {entry.action}
                          </span>
                        </p>
                        <p className="text-xs text-muted-foreground truncate mt-0.5">{entry.request_title}</p>
                      </div>
                      <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">{timeAgo(entry.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Status breakdown bar */}
      {stats && stats.total > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Status Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {Object.entries(s)
                .sort((a, b) => b[1] - a[1])
                .map(([status, count]) => {
                  const dot: Record<string, string> = {
                    new:            'bg-blue-400',
                    pending_review: 'bg-amber-400',
                    escalated:      'bg-orange-400',
                    reviewed:       'bg-indigo-400',
                    approved:       'bg-emerald-400',
                    rejected:       'bg-red-400',
                    withdrawn:      'bg-gray-400',
                  };
                  return (
                    <div
                      key={status}
                      className="flex items-center gap-2 px-3 py-1.5 rounded-full border bg-card hover:bg-muted/40 cursor-pointer transition-colors"
                      onClick={() => router.push('/dashboard/cases')}
                    >
                      <span className={`h-2 w-2 rounded-full shrink-0 ${dot[status] ?? 'bg-gray-400'}`} />
                      <span className="text-sm font-semibold tabular-nums">{count}</span>
                      <span className="text-xs text-muted-foreground capitalize">{status.replace(/_/g, ' ')}</span>
                    </div>
                  );
                })}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
