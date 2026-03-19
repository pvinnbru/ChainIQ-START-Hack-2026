'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { AlertCircle, ArrowUpRight, CheckCircle, ThumbsUp, ThumbsDown } from 'lucide-react';
import { toast } from 'sonner';

interface Escalation {
  id: string;
  request_id: string;
  type: string;
  status: string;
  message: string | null;
  created_at: string;
}

const TYPE_LABELS: Record<string, string> = {
  requester_clarification: 'Clarification Needed',
  procurement_manager: 'Procurement Manager Review',
  category_head: 'Category Head Review',
  compliance: 'Compliance Review',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function EscalationsPage() {
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);
  const { user } = useAuth();
  const router = useRouter();
  const isRequester = user?.role === 'requester';

  const load = () => {
    fetch(`${API}/escalations/me`, { credentials: 'include' })
      .then((r) => r.json())
      .then(setEscalations)
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const markReviewed = async (esc: Escalation) => {
    setActing(esc.id);
    try {
      const res = await fetch(`${API}/requests/${esc.request_id}/review`, {
        method: 'POST', credentials: 'include',
      });
      if (!res.ok) throw new Error();
      toast.success('Request marked as reviewed');
      load();
    } catch {
      toast.error('Failed to mark as reviewed');
    } finally {
      setActing(null);
    }
  };

  const approveOrReject = async (esc: Escalation, action: 'approve' | 'reject') => {
    setActing(esc.id + action);
    try {
      const res = await fetch(`${API}/requests/${esc.request_id}/${action}`, {
        method: 'POST', credentials: 'include',
      });
      if (!res.ok) throw new Error();
      toast.success(`Request ${action === 'approve' ? 'approved' : 'rejected'} successfully`);
      load();
    } catch {
      toast.error(`Failed to ${action} request`);
    } finally {
      setActing(null);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    );
  }

  return (
    <div className="py-8 w-full max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">My Escalations</h1>
        <p className="text-muted-foreground mt-1">
          {escalations.length} pending action{escalations.length !== 1 ? 's' : ''}
        </p>
      </div>

      {escalations.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No pending escalations. You're all caught up!
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {escalations.map((esc) => (
            <Card key={esc.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <AlertCircle className="h-4 w-4 text-amber-500" />
                    {TYPE_LABELS[esc.type] ?? esc.type}
                  </CardTitle>
                  <Badge variant="outline" className="text-amber-700 border-amber-300 bg-amber-50 text-xs">
                    pending
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground font-mono">
                  Request: {esc.request_id}
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                {esc.message && (
                  <p className="text-sm text-muted-foreground leading-relaxed">{esc.message}</p>
                )}

                <div className="flex flex-wrap gap-2">
                  {isRequester ? (
                    /* Requester: provide clarification */
                    <Button
                      size="sm"
                      className="gap-1"
                      onClick={() => router.push(`/dashboard/clarify/${esc.request_id}`)}
                    >
                      Provide Clarification
                      <ArrowUpRight className="h-3 w-3" />
                    </Button>
                  ) : (
                    /* Other roles: view, approve, reject, or mark reviewed */
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1"
                        onClick={() => router.push(`/dashboard/analysis?id=${esc.request_id}`)}
                      >
                        View Request
                        <ArrowUpRight className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="default"
                        className="gap-1 bg-emerald-600 hover:bg-emerald-700"
                        disabled={acting === esc.id + 'approve'}
                        onClick={() => approveOrReject(esc, 'approve')}
                      >
                        <ThumbsUp className="h-3 w-3" />
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        className="gap-1"
                        disabled={acting === esc.id + 'reject'}
                        onClick={() => approveOrReject(esc, 'reject')}
                      >
                        <ThumbsDown className="h-3 w-3" />
                        Reject
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="gap-1 text-muted-foreground"
                        disabled={acting === esc.id}
                        onClick={() => markReviewed(esc)}
                      >
                        <CheckCircle className="h-3 w-3" />
                        Mark Reviewed
                      </Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
