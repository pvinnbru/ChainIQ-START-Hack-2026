'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ActionDialog } from '@/components/ui/action-dialog';
import { Search, ArrowUpRight, ArrowUp, ArrowDown, PackageSearch, Undo2, Download } from 'lucide-react';
import { toast } from 'sonner';

interface Request {
  id: string;
  created_at: string;
  title: string | null;
  plain_text: string | null;
  status: string;
  business_unit: string | null;
  category_l1: string | null;
  category_l2: string | null;
  country: string | null;
  site: string | null;
  budget_amount: number | null;
  currency: string | null;
  required_by_date: string | null;
  requester_id: string;
}

type SortBy = 'date' | 'l1' | 'l2' | 'country';
type Order = 'asc' | 'desc';

function getStatusColor(status: string) {
  switch (status) {
    case 'new': return 'text-blue-700 border-blue-300 bg-blue-50';
    case 'pending_review': return 'text-amber-700 border-amber-300 bg-amber-50';
    case 'escalated': return 'text-orange-700 border-orange-300 bg-orange-50';
    case 'reviewed': return 'text-indigo-700 border-indigo-300 bg-indigo-50';
    case 'approved': return 'text-emerald-700 border-emerald-300 bg-emerald-50';
    case 'rejected': return 'text-red-700 border-red-300 bg-red-50';
    default: return 'text-gray-700 border-gray-300 bg-gray-50';
  }
}

const SORT_LABELS: Record<SortBy, string> = {
  date: 'Date',
  l1: 'L1 Category',
  l2: 'L2 Category',
  country: 'Country',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function CasesPage() {
  const router = useRouter();
  const { user } = useAuth();
  const isRequester = user?.role === 'requester';
  const [requests, setRequests] = useState<Request[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<SortBy>('date');
  const [order, setOrder] = useState<Order>('asc');
  const [withdrawTarget, setWithdrawTarget] = useState<Request | null>(null);
  const [page, setPage] = useState(1);
  const PER_PAGE = 10;

  const fetchRequests = async (sb: SortBy, ord: Order) => {
    setLoading(true);
    try {
      const url = isRequester
        ? `${API}/requests/mine`
        : `${API}/requests?sort_by=${sb}&order=${ord}`;
      const res = await fetch(url, { credentials: 'include' });
      if (res.ok) setRequests(await res.json());
    } catch {
      // silently fail, keep existing data
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRequests(sortBy, order);
  }, [sortBy, order]);

  useEffect(() => { setPage(1); }, [search, statusFilter]);

  const toggleSort = (sb: SortBy) => {
    setPage(1);
    if (sortBy === sb) {
      setOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(sb);
      setOrder('asc');
    }
  };

  const filtered = requests.filter((r) => {
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    const q = search.toLowerCase();
    return (
      (r.id ?? '').toLowerCase().includes(q) ||
      (r.title ?? '').toLowerCase().includes(q) ||
      (r.business_unit ?? '').toLowerCase().includes(q) ||
      (r.category_l2 ?? '').toLowerCase().includes(q) ||
      (r.site ?? '').toLowerCase().includes(q) ||
      (r.status ?? '').toLowerCase().includes(q)
    );
  });

  const handleView = (req: Request) => {
    sessionStorage.setItem('currentRequest', JSON.stringify(req));
    router.push(`/dashboard/analysis?id=${req.id}`);
  };

  const handleWithdraw = async (notes: string) => {
    if (!withdrawTarget) return;
    const req = withdrawTarget;
    setWithdrawTarget(null);
    try {
      const res = await fetch(`${API}/requests/${req.id}/withdraw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ notes: notes || null }),
      });
      if (!res.ok) throw new Error();
      toast.success('Request withdrawn');
      fetchRequests(sortBy, order);
    } catch {
      toast.error('Failed to withdraw request');
    }
  };

  const exportCsv = () => {
    const headers = ['ID', 'Title', 'Status', 'Category L1', 'Category L2', 'Business Unit', 'Country', 'Site', 'Budget', 'Currency', 'Required By', 'Created At'];
    const rows = filtered.map((r) => [
      r.id,
      r.title ?? r.plain_text?.slice(0, 80) ?? '',
      r.status,
      r.category_l1 ?? '',
      r.category_l2 ?? '',
      r.business_unit ?? '',
      r.country ?? '',
      r.site ?? '',
      r.budget_amount ?? '',
      r.currency ?? '',
      r.required_by_date ?? '',
      r.created_at,
    ]);
    const csv = [headers, ...rows].map((row) => row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cases-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const statusCounts = requests.reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});

  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const paginated = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

  if (loading) {
    return (
      <div className="flex h-[80vh] w-full flex-col items-center justify-center space-y-4">
        <div className="h-12 w-12 animate-spin rounded-full border-b-2 border-primary" />
        <p className="text-muted-foreground animate-pulse">Loading cases...</p>
      </div>
    );
  }

  return (
    <div className="py-8 w-full max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {isRequester ? 'My Cases' : 'All Cases'}
          </h1>
          <p className="text-muted-foreground mt-1">{requests.length} procurement requests</p>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search by title, unit, site…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
          <Button variant="outline" size="sm" className="gap-1.5 shrink-0" onClick={exportCsv}>
            <Download className="h-4 w-4" />
            <span className="hidden sm:inline">Export</span>
          </Button>
        </div>
      </div>

      {/* Status filter */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-muted-foreground">Status:</span>
        {['all', 'new', 'pending_review', 'escalated', 'reviewed', 'approved', 'rejected', 'withdrawn'].map((s) => {
          const count = s === 'all' ? requests.length : (statusCounts[s] ?? 0);
          if (s !== 'all' && count === 0) return null;
          return (
            <Button
              key={s}
              size="sm"
              variant={statusFilter === s ? 'default' : 'outline'}
              className="h-7 px-2.5 text-xs gap-1"
              onClick={() => setStatusFilter(s)}
            >
              {s === 'all' ? 'All' : s.replace(/_/g, ' ')}
              <span className={`text-[10px] font-mono ${statusFilter === s ? 'opacity-80' : 'text-muted-foreground'}`}>
                {count}
              </span>
            </Button>
          );
        })}
      </div>

      {/* Sort controls — approvers/managers only */}
      {!isRequester && (
        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-xs text-muted-foreground">Sort by:</span>
          {(Object.keys(SORT_LABELS) as SortBy[]).map((sb) => (
            <Button
              key={sb}
              size="sm"
              variant={sortBy === sb ? 'default' : 'outline'}
              className="h-7 px-2.5 text-xs gap-1"
              onClick={() => toggleSort(sb)}
            >
              {SORT_LABELS[sb]}
              {sortBy === sb && (
                order === 'asc'
                  ? <ArrowUp className="h-3 w-3" />
                  : <ArrowDown className="h-3 w-3" />
              )}
            </Button>
          ))}
        </div>
      )}

      <Card>
        <CardHeader className="border-b pb-4 flex-row items-center justify-between">
          <CardTitle className="text-base">
            {filtered.length} {filtered.length === 1 ? 'result' : 'results'}
            {search && <span className="text-muted-foreground font-normal"> for "{search}"</span>}
          </CardTitle>
          {totalPages > 1 && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <button
                className="px-2 py-0.5 rounded border text-xs disabled:opacity-40"
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                ←
              </button>
              <span className="text-xs">{page} / {totalPages}</span>
              <button
                className="px-2 py-0.5 rounded border text-xs disabled:opacity-40"
                disabled={page === totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                →
              </button>
            </div>
          )}
        </CardHeader>
        <CardContent className="p-0">
          {/* Mobile card list */}
          <div className="md:hidden divide-y">
            {paginated.map((req) => {
              const title = req.title ?? (req.plain_text ? req.plain_text.slice(0, 60) + (req.plain_text.length > 60 ? '…' : '') : '(untitled)');
              return (
                <div key={req.id} className="p-4 hover:bg-muted/30 transition-colors cursor-pointer" onClick={() => handleView(req)}>
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <p className="font-medium text-sm leading-snug flex-1">{title}</p>
                    <Badge variant="outline" className={`text-xs shrink-0 ${getStatusColor(req.status)}`}>
                      {req.status.replace(/_/g, ' ')}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground mb-2">
                    {req.category_l2 && <span>{req.category_l2}</span>}
                    {req.site && <span>{req.site}{req.country ? `, ${req.country}` : ''}</span>}
                    {req.budget_amount != null && (
                      <span className="font-mono">{req.currency} {req.budget_amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                    )}
                  </div>
                  <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
                    <Button size="sm" variant="outline" className="h-7 px-2 text-xs gap-1" onClick={() => handleView(req)}>
                      View <ArrowUpRight className="h-3 w-3" />
                    </Button>
                    {isRequester && !['approved', 'rejected', 'withdrawn'].includes(req.status) && (
                      <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive" onClick={() => setWithdrawTarget(req)}>
                        <Undo2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
            {filtered.length === 0 && requests.length === 0 && isRequester && (
              <div className="px-4 py-16 text-center">
                <div className="flex flex-col items-center gap-3 text-muted-foreground">
                  <PackageSearch className="h-10 w-10 opacity-30" />
                  <p className="font-medium">No requests yet</p>
                  <p className="text-sm">Submit your first procurement request to get started.</p>
                  <Button size="sm" className="mt-1" onClick={() => router.push('/dashboard/create')}>New Request</Button>
                </div>
              </div>
            )}
            {filtered.length === 0 && (requests.length > 0 || !isRequester) && (
              <div className="px-4 py-12 text-center text-muted-foreground text-sm">No requests match your search.</div>
            )}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-xs text-muted-foreground uppercase bg-muted/50 border-b">
                <tr>
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">Title</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Business Unit</th>
                  <th className="px-4 py-3">Location</th>
                  <th className="px-4 py-3 text-right">Budget</th>
                  <th className="px-4 py-3">Required By</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {paginated.map((req) => (
                  <tr
                    key={req.id}
                    className="border-b last:border-0 hover:bg-muted/30 transition-colors cursor-pointer"
                    onClick={() => handleView(req)}
                  >
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {req.id.slice(0, 8)}…
                    </td>
                    <td className="px-4 py-3 max-w-[220px]">
                      <p className="font-medium leading-snug">
                        {req.title
                          ? req.title
                          : req.plain_text
                            ? req.plain_text.slice(0, 60) + (req.plain_text.length > 60 ? '…' : '')
                            : '(untitled)'}
                      </p>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <Badge variant="outline" className={`text-xs ${getStatusColor(req.status)}`}>
                        {req.status.replace(/_/g, ' ')}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <p className="text-xs text-muted-foreground">{req.category_l1 ?? '—'}</p>
                      <p className="font-medium text-xs leading-snug">{req.category_l2 ?? '—'}</p>
                    </td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">{req.business_unit ?? '—'}</td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <p className="text-xs">{req.site ?? '—'}</p>
                      <p className="text-xs text-muted-foreground">{req.country ?? '—'}</p>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-xs whitespace-nowrap">
                      {req.budget_amount != null
                        ? `${req.currency} ${req.budget_amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                        : <span className="text-muted-foreground">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">
                      {req.required_by_date ?? '—'}
                    </td>
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-1">
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs gap-1" onClick={() => handleView(req)}>
                          View <ArrowUpRight className="h-3 w-3" />
                        </Button>
                        {isRequester && !['approved', 'rejected', 'withdrawn'].includes(req.status) && (
                          <Button size="sm" variant="ghost" className="h-7 px-2 text-xs gap-1 text-muted-foreground hover:text-destructive" onClick={() => setWithdrawTarget(req)}>
                            <Undo2 className="h-3 w-3" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && requests.length === 0 && isRequester && (
                  <tr>
                    <td colSpan={9} className="px-4 py-16 text-center">
                      <div className="flex flex-col items-center gap-3 text-muted-foreground">
                        <PackageSearch className="h-10 w-10 opacity-30" />
                        <p className="font-medium">No requests yet</p>
                        <p className="text-sm">Submit your first procurement request to get started.</p>
                        <Button size="sm" className="mt-1" onClick={() => router.push('/dashboard/create')}>
                          New Request
                        </Button>
                      </div>
                    </td>
                  </tr>
                )}
                {filtered.length === 0 && (requests.length > 0 || !isRequester) && (
                  <tr>
                    <td colSpan={9} className="px-4 py-12 text-center text-muted-foreground">
                      No requests match your search.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
        {totalPages > 1 && (
          <div className="border-t px-4 py-3 flex items-center justify-between text-sm text-muted-foreground">
            <span className="text-xs">
              Showing {(page - 1) * PER_PAGE + 1}–{Math.min(page * PER_PAGE, filtered.length)} of {filtered.length}
            </span>
            <div className="flex items-center gap-1">
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`h-7 w-7 rounded text-xs font-medium transition-colors
                    ${p === page ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>
      <ActionDialog
        open={!!withdrawTarget}
        onOpenChange={(v) => !v && setWithdrawTarget(null)}
        title="Withdraw Request"
        description="This will withdraw your request. It can be resubmitted later if needed."
        confirmLabel="Withdraw"
        confirmClassName="bg-destructive hover:bg-destructive/90 text-white"
        notesLabel="Reason for withdrawal (optional)"
        onConfirm={handleWithdraw}
      />
    </div>
  );
}
