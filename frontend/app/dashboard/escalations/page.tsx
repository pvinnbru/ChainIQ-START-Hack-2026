'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ActionDialog } from '@/components/ui/action-dialog';
import { AlertCircle, ArrowUpRight, CheckCircle, ThumbsUp, ThumbsDown } from 'lucide-react';
import { toast } from 'sonner';
import { STATUS_BADGE } from '@/lib/colors';

interface Escalation {
  id: string;
  request_id: string;
  type: string;
  status: string;
  message: string | null;
  created_at: string;
}

type DialogAction = 'approve' | 'reject' | 'review' | null;

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
  const [activeEsc, setActiveEsc] = useState<Escalation | null>(null);
  const [dialogAction, setDialogAction] = useState<DialogAction>(null);
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
                  <Badge variant="outline" className={`${STATUS_BADGE.escalated} text-xs`}>
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
                    <Button size="sm" className="gap-1"
                      onClick={() => router.push(`/dashboard/clarify/${esc.request_id}`)}>
                      Provide Clarification <ArrowUpRight className="h-3 w-3" />
                    </Button>
                  ) : (
                    <>
                      <Button size="sm" variant="outline" className="gap-1"
                        onClick={() => router.push(`/dashboard/transparency?id=${esc.request_id}`)}>
                        View Request <ArrowUpRight className="h-3 w-3" />
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
          ))}
        </div>
      )}

      {/* Approve dialog */}
      <ActionDialog
        open={dialogAction === 'approve'}
        onOpenChange={(v) => !v && setDialogAction(null)}
        title="Approve Request"
        description="This will mark the request as approved. Add an optional note to explain your decision."
        confirmLabel="Approve"
        confirmClassName="bg-emerald-600 hover:bg-emerald-700 text-white"
        onConfirm={handleConfirm}
      />

      {/* Reject dialog */}
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

      {/* Mark reviewed dialog */}
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
