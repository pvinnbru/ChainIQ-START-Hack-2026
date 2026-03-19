'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Search, ArrowUpRight, ArrowUp, ArrowDown } from 'lucide-react';

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

  const toggleSort = (sb: SortBy) => {
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
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by title, unit, site…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
      </div>

      {/* Status filter */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-muted-foreground">Status:</span>
        {['all', 'new', 'pending_review', 'escalated', 'reviewed', 'approved', 'rejected'].map((s) => (
          <Button
            key={s}
            size="sm"
            variant={statusFilter === s ? 'default' : 'outline'}
            className="h-7 px-2.5 text-xs"
            onClick={() => setStatusFilter(s)}
          >
            {s === 'all' ? 'All' : s.replace(/_/g, ' ')}
          </Button>
        ))}
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
        <CardHeader className="border-b pb-4">
          <CardTitle className="text-base">
            {filtered.length} {filtered.length === 1 ? 'result' : 'results'}
            {search && <span className="text-muted-foreground font-normal"> for "{search}"</span>}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
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
                {filtered.map((req) => (
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
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs gap-1"
                        onClick={() => handleView(req)}
                      >
                        View
                        <ArrowUpRight className="h-3 w-3" />
                      </Button>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
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
      </Card>
    </div>
  );
}
