'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ActionDialog } from '@/components/ui/action-dialog';
import { AlertCircle, ArrowUpRight, CheckCircle, ThumbsUp, ThumbsDown, Inbox } from 'lucide-react';
import { toast } from 'sonner';
import { STATUS_BADGE } from '@/lib/colors';
import { useEscalationCount } from '@/context/escalation-count-context';

interface Escalation {
  id: string;
  request_id: string;
  type: string;
  status: string;
  message: string | null;
  created_at: string;
}

interface RequestSnippet {
  title: string | null;
  plain_text: string | null;
  budget_amount: number | null;
  currency: string | null;
}

type DialogAction = 'approve' | 'reject' | 'review' | null;

const TYPE_LABELS: Record<string, string> = {
  requester_clarification: 'Clarification Needed',
  procurement_manager: 'Procurement Manager Review',
  category_head: 'Category Head Review',
  compliance: 'Compliance Review',
};

const TYPE_ORDER = ['requester_clarification', 'procurement_manager', 'category_head', 'compliance'];

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function EscalationsPage() {
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [snippets, setSnippets] = useState<Record<string, RequestSnippet>>({});
  const [loading, setLoading] = useState(true);
  const [activeEsc, setActiveEsc] = useState<Escalation | null>(null);
  const [dialogAction, setDialogAction] = useState<DialogAction>(null);
  const { user } = useAuth();
  const { refresh: refreshCount } = useEscalationCount();
  const router = useRouter();
  const isRequester = user?.role === 'requester';

  const pageTitle = isRequester ? 'Clarifications Needed' : 'Pending Reviews';
  const pageDesc = isRequester
    ? 'Requests where the AI needs more information from you.'
    : 'Requests escalated to you for a decision.';

  const load = async () => {
    try {
      const res = await fetch(`${API}/escalations/me`, { credentials: 'include' });
      const data: Escalation[] = await res.json();
      setEscalations(data);

      // Fetch request snippets for context
      const ids = [...new Set(data.map((e) => e.request_id))];
      const results = await Promise.allSettled(
        ids.map((id) =>
          fetch(`${API}/requests/${id}`, { credentials: 'include' }).then((r) => r.json())
        )
      );
      const map: Record<string, RequestSnippet> = {};
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') map[ids[i]] = r.value;
      });
      setSnippets(map);
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openDialog = (esc: Escalation, action: DialogAction) => {
    setActiveEsc(esc);
    setDialogAction(action);
  };

  const handleConfirm = async (notes: string) => {
    if (!activeEsc || !dialogAction) return;
    const action = dialogAction;
    const esc = activeEsc;
    setDialogAction(null);
    setActiveEsc(null);

    try {
      const url = action === 'review'
        ? `${API}/requests/${esc.request_id}/review`
        : `${API}/requests/${esc.request_id}/${action}`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ notes: notes || null }),
      });
      if (!res.ok) throw new Error();
      toast.success(
        action === 'approve' ? 'Request approved' :
        action === 'reject'  ? 'Request rejected' :
        'Request marked as reviewed'
      );
      load();
      refreshCount();
    } catch {
      toast.error('Action failed. Please try again.');
    }
  };

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    );
  }

  // Group by type in defined order
  const grouped = TYPE_ORDER.map((type) => ({
    type,
    items: escalations.filter((e) => e.type === type),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="py-8 w-full max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{pageTitle}</h1>
        <p className="text-muted-foreground mt-1">{pageDesc}</p>
      </div>

      {escalations.length === 0 ? (
        <Card>
          <CardContent className="py-16 flex flex-col items-center gap-3 text-muted-foreground">
            <Inbox className="h-10 w-10 opacity-30" />
            <p className="font-medium">All caught up</p>
            <p className="text-sm">No pending escalations for you right now.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-8">
          {grouped.map(({ type, items }) => (
            <div key={type} className="space-y-3">
              <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-amber-500" />
                <h2 className="text-sm font-semibold text-foreground">{TYPE_LABELS[type] ?? type}</h2>
                <Badge variant="outline" className="text-xs font-mono">{items.length}</Badge>
              </div>

              {items.map((esc) => {
                const snippet = snippets[esc.request_id];
                const title = snippet?.title ?? snippet?.plain_text?.slice(0, 80) ?? esc.request_id;
                const budget = snippet?.budget_amount != null
                  ? `${snippet.currency ?? ''} ${snippet.budget_amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}`.trim()
                  : null;

                return (
                  <Card key={esc.id} className="border-l-4 border-l-amber-400">
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <CardTitle className="text-sm font-semibold leading-snug truncate">
                            {title}
                          </CardTitle>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs font-mono text-muted-foreground">{esc.request_id}</span>
                            {budget && (
                              <span className="text-xs text-muted-foreground">· {budget}</span>
                            )}
                            <span className="text-xs text-muted-foreground">
                              · {new Date(esc.created_at).toLocaleDateString()}
                            </span>
                          </div>
                        </div>
                        <Badge variant="outline" className={`${STATUS_BADGE.escalated} text-xs shrink-0`}>
                          pending
                        </Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {esc.message && (
                        <p className="text-sm text-muted-foreground leading-relaxed border-l-2 border-muted pl-3">
                          {esc.message}
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {isRequester ? (
                          <>
                            <Button size="sm" className="gap-1"
                              onClick={() => router.push(`/dashboard/clarify/${esc.request_id}`)}>
                              Provide Clarification <ArrowUpRight className="h-3 w-3" />
                            </Button>
                            <Button size="sm" variant="outline" className="gap-1"
                              onClick={() => router.push(`/dashboard/transparency?id=${esc.request_id}`)}>
                              View Analysis <ArrowUpRight className="h-3 w-3" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" variant="outline" className="gap-1"
                              onClick={() => router.push(`/dashboard/transparency?id=${esc.request_id}`)}>
                              View Full Analysis <ArrowUpRight className="h-3 w-3" />
                            </Button>
                            <Button size="sm" className="gap-1 bg-emerald-600 hover:bg-emerald-700"
                              onClick={() => openDialog(esc, 'approve')}>
                              <ThumbsUp className="h-3 w-3" /> Approve
                            </Button>
                            <Button size="sm" variant="destructive" className="gap-1"
                              onClick={() => openDialog(esc, 'reject')}>
                              <ThumbsDown className="h-3 w-3" /> Reject
                            </Button>
                            <Button size="sm" variant="ghost" className="gap-1 text-muted-foreground"
                              onClick={() => openDialog(esc, 'review')}>
                              <CheckCircle className="h-3 w-3" /> Mark Reviewed
                            </Button>
                          </>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          ))}
        </div>
      )}

      <ActionDialog
        open={dialogAction === 'approve'}
        onOpenChange={(v) => !v && setDialogAction(null)}
        title="Approve Request"
        description="This will mark the request as approved. Add an optional note to explain your decision."
        confirmLabel="Approve"
        confirmClassName="bg-emerald-600 hover:bg-emerald-700 text-white"
        onConfirm={handleConfirm}
      />
      <ActionDialog
        open={dialogAction === 'reject'}
        onOpenChange={(v) => !v && setDialogAction(null)}
        title="Reject Request"
        description="This will mark the request as rejected. Please provide a reason so the requester knows what to fix."
        confirmLabel="Reject"
        confirmClassName="bg-destructive hover:bg-destructive/90 text-white"
        notesLabel="Reason for rejection (recommended)"
        onConfirm={handleConfirm}
      />
      <ActionDialog
        open={dialogAction === 'review'}
        onOpenChange={(v) => !v && setDialogAction(null)}
        title="Mark as Reviewed"
        description="This will mark the request as reviewed. The request will not be approved or rejected."
        confirmLabel="Mark Reviewed"
        onConfirm={handleConfirm}
      />
    </div>
  );
}
