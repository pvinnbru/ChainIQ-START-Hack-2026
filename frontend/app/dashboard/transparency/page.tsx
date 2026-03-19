'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft, Bot, Printer, ChevronDown, ChevronRight, ChevronUp, Send,
  AlertTriangle, CheckCircle, Minus, ArrowRight, Star, Package, Clock, ShieldCheck, TrendingUp,
} from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ActionLog {
  action_index: number;
  rule_id: string;
  rule_description: string;
  action_type: string;
  action_tuple: (string | number | boolean)[];
  when_condition: string | null;
  when_evaluated: boolean;
  when_passed: boolean;
  input_values: Record<string, unknown>;
  output_key: string;
  output_value_before: unknown;
  output_value_after: unknown;
  skipped: boolean;
}

interface SupplierLog {
  supplier_id: string;
  supplier_name: string;
  category_l2: string;
  excluded: boolean;
  exclusion_reason: string | null;
  pricing_resolved: Record<string, unknown>;
  action_logs: ActionLog[];
  final_state: Record<string, unknown>;
  final_cost_rank_score: number | null;
  final_reputation_score: number | null;
}

interface ExecutionLog {
  request_id: string;
  timestamp: string;
  global_context_snapshot: Record<string, unknown>;
  supplier_logs: SupplierLog[];
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const RULE_COLORS: Record<string, string> = {
  ER:      'bg-orange-100 text-orange-800 border-orange-300 dark:bg-orange-950/60 dark:text-orange-300 dark:border-orange-800',
  AT:      'bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-950/60 dark:text-blue-300 dark:border-blue-800',
  CR:      'bg-purple-100 text-purple-800 border-purple-300 dark:bg-purple-950/60 dark:text-purple-300 dark:border-purple-800',
  RANKING: 'bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800',
};

const ESCALATION_KEY_MAP: Record<string, string> = {
  escalate_to_requester:                  'Requester Clarification',
  escalate_to_procurement_manager:        'Procurement Manager',
  escalate_to_head_of_category:           'Head of Category',
  escalate_to_security_compliance:        'Compliance Review',
  escalate_to_head_of_strategic_sourcing: 'Strategic Sourcing Lead',
  escalate_to_cpo:                        'CPO',
  escalate_to_sourcing_excellence:        'Sourcing Excellence Lead',
  escalate_to_marketing_governance:       'Marketing Governance Lead',
  escalate_to_regional_compliance:        'Regional Compliance Lead',
};

const CONTEXT_LABELS: Record<string, string> = {
  category_l1: 'Category L1',
  category_l2: 'Category L2',
  budget: 'Budget',
  currency: 'Currency',
  quantity: 'Quantity',
  amount_unit: 'Unit',
  delivery_country: 'Delivery Country',
  days_until_required: 'Days Until Required',
  preferred_supplier_mentioned: 'Preferred Supplier',
  incumbent_supplier: 'Incumbent Supplier',
  data_residency_constraint: 'Data Residency',
  esg_requirement: 'ESG Required',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function ruleColor(ruleId: string): string {
  const prefix = ruleId.match(/^([A-Z]+)/)?.[1] ?? '';
  return RULE_COLORS[prefix] ?? 'bg-slate-100 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600';
}

function formatDataFlow(tuple: (string | number | boolean)[]): string {
  if (tuple.length < 5) return tuple.join(' ');
  const [type, in1, in2, op, out] = tuple;
  if (type === 'AL') return `${in1} ${op} ${in2}  →  ${out}`;
  if (type === 'ALI') {
    if (in1 === '_') return `set ${out} = ${in2}`;
    return `${in1} ${op} ${in2}  →  ${out}`;
  }
  if (type === 'OSLM') return `${in1} AND ${in2}  →  ${out}`;
  return tuple.slice(0, 5).join(' · ');
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return v.toLocaleString();
  return String(v);
}

function ValueDelta({ before, after, skipped }: { before: unknown; after: unknown; skipped: boolean }) {
  if (skipped) return <span className="text-xs text-muted-foreground">—</span>;
  const bStr = formatValue(before);
  const aStr = formatValue(after);
  if (bStr === aStr) return <span className="text-xs text-muted-foreground">{aStr}</span>;
  if (before === null || before === undefined) {
    return <span className="text-xs font-mono text-emerald-700 dark:text-emerald-400">→ {aStr}</span>;
  }
  return (
    <span className="text-xs font-mono text-amber-700 dark:text-amber-400">
      {bStr} → {aStr}
    </span>
  );
}

// ── Supplier Funnel ───────────────────────────────────────────────────────────

function SupplierFunnel({ logs }: { logs: SupplierLog[] }) {
  const stages = useMemo(() => {
    const total = logs.length;
    const catMatch = logs.filter(s => !s.exclusion_reason?.includes('mismatch')).length;
    const evaluated = logs.filter(s => s.action_logs?.length > 0).length;
    const shortlisted = logs.filter(s => s.final_cost_rank_score != null).length;
    return [
      { label: 'Suppliers Considered', count: total,       bg: 'bg-slate-200 dark:bg-slate-700' },
      { label: 'Category Match',       count: catMatch,    bg: 'bg-blue-200 dark:bg-blue-900' },
      { label: 'Evaluated',            count: evaluated,   bg: 'bg-amber-200 dark:bg-amber-900' },
      { label: 'Shortlisted',          count: shortlisted, bg: 'bg-emerald-200 dark:bg-emerald-900' },
    ];
  }, [logs]);

  const max = stages[0].count || 1;

  return (
    <div className="space-y-2">
      {stages.map((s, i) => (
        <div key={s.label} className="flex items-center gap-3">
          <div className="w-36 text-xs text-right text-muted-foreground shrink-0">{s.label}</div>
          <div className="flex-1 h-7 bg-muted/40 rounded overflow-hidden">
            <div
              className={`h-full ${s.bg} rounded transition-all flex items-center px-2`}
              style={{ width: `${(s.count / max) * 100}%` }}
            >
              <span className="text-xs font-bold">{s.count}</span>
            </div>
          </div>
          {i < stages.length - 1 && <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />}
          {i === stages.length - 1 && <CheckCircle className="h-3 w-3 text-emerald-600 shrink-0" />}
        </div>
      ))}
    </div>
  );
}

// ── Excluded Suppliers ────────────────────────────────────────────────────────

function ExcludedSuppliers({ logs }: { logs: SupplierLog[] }) {
  const [open, setOpen] = useState(false);
  const excluded = logs.filter(s => s.excluded);
  const groups = excluded.reduce<Record<string, string[]>>((acc, s) => {
    const reason = s.exclusion_reason ?? 'Unknown';
    if (!acc[reason]) acc[reason] = [];
    acc[reason].push(s.supplier_name);
    return acc;
  }, {});

  return (
    <div className="mt-4">
      <button
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        {excluded.length} excluded supplier entries
      </button>
      {open && (
        <div className="mt-2 space-y-2 pl-5">
          {Object.entries(groups).slice(0, 8).map(([reason, names]) => (
            <div key={reason} className="text-xs">
              <p className="text-muted-foreground font-mono mb-0.5">{reason}</p>
              <p className="text-foreground">{[...new Set(names)].join(', ')}</p>
            </div>
          ))}
          {Object.keys(groups).length > 8 && (
            <p className="text-xs text-muted-foreground">+{Object.keys(groups).length - 8} more groups</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Supplier Card ─────────────────────────────────────────────────────────────

function scoreBar(score: number | null, invert = false) {
  if (score == null) return null;
  // scores are 0–100 integers
  const pct = Math.min(100, Math.max(0, score));
  const good = invert ? pct < 30 : pct > 65;
  const mid  = invert ? pct < 60 : pct > 35;
  const color = good ? 'bg-emerald-400' : mid ? 'bg-amber-400' : 'bg-red-400';
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono w-6 text-right text-muted-foreground">{pct}</span>
    </div>
  );
}

function supplierMetrics(s: SupplierLog) {
  const pr = s.pricing_resolved as Record<string, unknown>;
  const fs = s.final_state      as Record<string, unknown>;
  return {
    unitPrice:   pr.unit_price   ?? pr.unit_price_eur,
    currency:    String(pr.currency ?? 'EUR'),
    totalCost:   pr.cost_total   ?? fs?.cost_total,
    leadStd:     pr.standard_lead_time_days ?? pr.lead_time_days,
    leadExp:     pr.expedited_lead_time_days as number | undefined,
    quality:     fs?.quality_score as number | undefined,
    risk:        fs?.risk_score    as number | undefined,
    esg:         fs?.esg_score     as number | undefined,
    isPreferred: fs?.preferred     as boolean | undefined,
    isIncumbent: fs?.incumbent     as boolean | undefined,
  };
}

function SupplierCard({ supplier: s, rank }: { supplier: SupplierLog; rank: number }) {
  const { unitPrice, currency, totalCost, leadStd, leadExp, quality, risk, esg, isPreferred, isIncumbent } = supplierMetrics(s);
  const isWinner = rank === 1;

  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-3 ${
      isWinner
        ? 'border-emerald-500 dark:border-emerald-500 bg-emerald-50/50 dark:bg-emerald-950/20 shadow-md'
        : 'bg-card'
    }`}>
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <span className={`text-sm font-bold ${isWinner ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>#{rank}</span>
          {isWinner && <Star className="h-4 w-4 text-emerald-500 fill-emerald-400" />}
          {isPreferred && <Badge variant="outline" className="text-[10px] text-blue-600 border-blue-200 bg-blue-50 dark:bg-blue-950/40 dark:text-blue-300">Preferred</Badge>}
          {isIncumbent && <Badge variant="outline" className="text-[10px] text-violet-600 border-violet-200 bg-violet-50 dark:bg-violet-950/40 dark:text-violet-300">Incumbent</Badge>}
        </div>
        <p className="font-semibold text-sm leading-snug">{s.supplier_name}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{s.category_l2}</p>
      </div>

      {/* Key metrics */}
      <div className="space-y-1.5 text-xs">
        {unitPrice != null && (
          <div className="flex items-center gap-1.5">
            <Package className="h-3 w-3 text-muted-foreground shrink-0" />
            <span className="text-muted-foreground">Unit</span>
            <span className="font-mono font-medium ml-auto">{currency} {Number(unitPrice).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
          </div>
        )}
        {totalCost != null && (
          <div className="flex items-center gap-1.5">
            <TrendingUp className="h-3 w-3 text-muted-foreground shrink-0" />
            <span className="text-muted-foreground">Total</span>
            <span className="font-mono font-medium ml-auto">{currency} {Number(totalCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
          </div>
        )}
        {leadStd != null && (
          <div className="flex items-center gap-1.5">
            <Clock className="h-3 w-3 text-muted-foreground shrink-0" />
            <span className="text-muted-foreground">Lead time</span>
            <span className="font-medium ml-auto">{String(leadStd)}d{leadExp != null ? ` / ${String(leadExp)}d exp` : ''}</span>
          </div>
        )}
      </div>

      {/* Score bars */}
      {(quality != null || risk != null || esg != null) && (
        <div className="space-y-1.5 pt-1 border-t">
          {quality != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-12 shrink-0">Quality</span>
              {scoreBar(quality)}
            </div>
          )}
          {risk != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-12 shrink-0">Risk</span>
              {scoreBar(risk, true)}
            </div>
          )}
          {esg != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-12 shrink-0">ESG</span>
              {scoreBar(esg)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Action Pipeline Table ─────────────────────────────────────────────────────

const RULE_GROUP_LABELS: Record<string, string> = {
  ER:      'Escalation Rules',
  AT:      'Approval Threshold Rules',
  CR:      'Compliance Rules',
  RANKING: 'Ranking Rules',
};

function ActionRow({ a }: { a: ActionLog }) {
  const changed = !a.skipped && JSON.stringify(a.output_value_before) !== JSON.stringify(a.output_value_after);
  const isEscalation = a.output_key?.startsWith('escalate_');
  const rowClass = a.skipped
    ? 'opacity-40 bg-muted/10'
    : isEscalation && a.output_value_after
    ? 'bg-orange-50 dark:bg-orange-950/20'
    : changed
    ? 'bg-amber-50 dark:bg-amber-950/20'
    : '';

  return (
    <tr className={`border-b last:border-0 ${rowClass}`}>
      <td className="px-2 py-2 text-muted-foreground font-mono">{a.action_index}</td>
      <td className="px-2 py-2">
        <Badge variant="outline" className={`text-[10px] font-mono px-1 py-0 ${ruleColor(a.rule_id)}`}>
          {a.rule_id}
        </Badge>
      </td>
      <td className="px-2 py-2 max-w-[200px]">
        <span title={a.rule_description} className="line-clamp-2 leading-snug">{a.rule_description || '—'}</span>
      </td>
      <td className="px-2 py-2 max-w-[160px]">
        {a.when_condition ? (
          <div>
            <code className="text-[10px] text-muted-foreground line-clamp-2" title={a.when_condition}>
              {a.when_condition.replace('WHEN ', '')}
            </code>
            {a.skipped
              ? <span className="text-[10px] text-red-600 dark:text-red-400">✗ skipped</span>
              : <span className="text-[10px] text-emerald-600 dark:text-emerald-400">✓ matched</span>}
          </div>
        ) : (
          <span className="text-muted-foreground">always</span>
        )}
      </td>
      <td className="px-2 py-2 max-w-[180px]">
        <code className="text-[10px] text-muted-foreground" title={JSON.stringify(a.action_tuple)}>
          {formatDataFlow(a.action_tuple)}
        </code>
      </td>
      <td className="px-2 py-2">
        <ValueDelta before={a.output_value_before} after={a.output_value_after} skipped={a.skipped} />
      </td>
      <td className="px-2 py-2">
        {a.skipped ? (
          <Badge variant="outline" className="text-[10px] text-muted-foreground">skipped</Badge>
        ) : changed ? (
          <Badge variant="outline" className="text-[10px] text-amber-700 border-amber-300 bg-amber-50 dark:bg-amber-950/40 dark:text-amber-300">changed</Badge>
        ) : (
          <Badge variant="outline" className="text-[10px] text-emerald-700 border-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 dark:text-emerald-300">applied</Badge>
        )}
      </td>
    </tr>
  );
}

function RuleGroup({ prefix, actions }: { prefix: string; actions: ActionLog[] }) {
  const [open, setOpen] = useState(true);
  const changed  = actions.filter(a => !a.skipped && JSON.stringify(a.output_value_before) !== JSON.stringify(a.output_value_after)).length;
  const skipped  = actions.filter(a => a.skipped).length;
  const label    = RULE_GROUP_LABELS[prefix] ?? `${prefix} Rules`;

  return (
    <div className="border-b last:border-0">
      {/* Group header */}
      <button
        className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 transition-colors text-left"
        onClick={() => setOpen(o => !o)}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
        <Badge variant="outline" className={`text-[10px] font-mono px-1.5 shrink-0 ${ruleColor(`${prefix}-000`)}`}>{prefix}</Badge>
        <span className="text-sm font-medium">{label}</span>
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground shrink-0">
          <span>{actions.length} rules</span>
          {changed > 0  && <span className="text-amber-600 dark:text-amber-400">{changed} changed</span>}
          {skipped > 0  && <span className="opacity-50">{skipped} skipped</span>}
        </div>
      </button>

      {/* Rows */}
      {open && (
        <div className="overflow-x-auto text-xs">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-t text-muted-foreground text-[10px] uppercase tracking-wide bg-muted/30">
                <th className="px-2 py-1.5 w-6">#</th>
                <th className="px-2 py-1.5 w-20">Rule</th>
                <th className="px-2 py-1.5">Description</th>
                <th className="px-2 py-1.5">Condition</th>
                <th className="px-2 py-1.5">Data Flow</th>
                <th className="px-2 py-1.5">Δ Value</th>
                <th className="px-2 py-1.5 w-20">Status</th>
              </tr>
            </thead>
            <tbody>
              {actions.map(a => <ActionRow key={a.action_index} a={a} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ActionPipelineTable({ actions }: { actions: ActionLog[] }) {
  const groups = useMemo(() => {
    const map = new Map<string, ActionLog[]>();
    for (const a of actions) {
      const prefix = a.rule_id.match(/^([A-Z]+)/)?.[1] ?? 'OTHER';
      if (!map.has(prefix)) map.set(prefix, []);
      map.get(prefix)!.push(a);
    }
    // Preserve a logical order
    const order = ['ER', 'AT', 'CR', 'RANKING'];
    const sorted = [...map.entries()].sort(([a], [b]) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
    return sorted;
  }, [actions]);

  return (
    <div className="divide-y rounded-b-lg overflow-hidden">
      {groups.map(([prefix, rows]) => (
        <RuleGroup key={prefix} prefix={prefix} actions={rows} />
      ))}
    </div>
  );
}

// ── Escalation Map ────────────────────────────────────────────────────────────

function EscalationMap({ logs }: { logs: SupplierLog[] }) {
  const escalations = useMemo(() => {
    const seen = new Set<string>();
    const result: { ruleId: string; outputKey: string; label: string; description: string }[] = [];

    for (const s of logs) {
      for (const a of s.action_logs ?? []) {
        if (
          a.output_key?.startsWith('escalate_') &&
          a.output_value_after !== false &&
          a.output_value_after != null &&
          !seen.has(a.output_key)
        ) {
          seen.add(a.output_key);
          result.push({
            ruleId: a.rule_id,
            outputKey: a.output_key,
            label: ESCALATION_KEY_MAP[a.output_key] ?? a.output_key.replace(/_/g, ' '),
            description: a.rule_description,
          });
        }
      }
    }
    return result;
  }, [logs]);

  if (escalations.length === 0) {
    return (
      <div className="flex items-center gap-3 text-muted-foreground py-4 pl-2">
        <CheckCircle className="h-5 w-5 text-emerald-500" />
        <span className="text-sm">No escalations triggered — request can proceed autonomously.</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {escalations.map((esc, i) => (
        <div key={esc.outputKey} className="flex items-start gap-3">
          <div className="flex flex-col items-center">
            <div className="h-2.5 w-2.5 rounded-full bg-orange-500 mt-1.5 shrink-0" />
            {i < escalations.length - 1 && <div className="w-0.5 bg-orange-200 dark:bg-orange-900 flex-1 mt-1" style={{ height: '32px' }} />}
          </div>
          <div className="flex-1 min-w-0 pb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className={`text-[10px] font-mono px-1.5 ${ruleColor(esc.ruleId)}`}>
                {esc.ruleId}
              </Badge>
              <ArrowRight className="h-3 w-3 text-muted-foreground" />
              <span className="text-sm font-semibold text-orange-700 dark:text-orange-400">{esc.label}</span>
            </div>
            {esc.description && (
              <p className="text-xs text-muted-foreground mt-0.5 leading-snug">{esc.description}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Ask AI Drawer ─────────────────────────────────────────────────────────────

function AiChatDrawer({
  open,
  onOpenChange,
  requestId,
  executionLog,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  requestId: string;
  executionLog: ExecutionLog | null;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = sessionStorage.getItem(`chat-${requestId}`);
      return saved ? JSON.parse(saved) : [];
    } catch { return []; }
  });
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    sessionStorage.setItem(`chat-${requestId}`, JSON.stringify(messages));
  }, [messages, requestId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || streaming || !executionLog) return;
    const userMsg: ChatMessage = { role: 'user', content: text };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput('');
    setStreaming(true);

    const assistantPlaceholder: ChatMessage = { role: 'assistant', content: '' };
    setMessages(m => [...m, assistantPlaceholder]);

    try {
      const res = await fetch(`${API}/ai/chat`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: nextMessages,
          execution_log: executionLog,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Failed' }));
        setMessages(m => {
          const updated = [...m];
          updated[updated.length - 1] = { role: 'assistant', content: `⚠️ ${err.detail ?? 'Error from AI endpoint'}` };
          return updated;
        });
        setStreaming(false);
        return;
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') { setStreaming(false); return; }
          try {
            const parsed = JSON.parse(data);
            if (parsed.content) {
              setMessages(m => {
                const updated = [...m];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = { ...last, content: last.content + parsed.content };
                return updated;
              });
            }
            if (parsed.error) {
              setMessages(m => {
                const updated = [...m];
                updated[updated.length - 1] = { role: 'assistant', content: `⚠️ ${parsed.error}` };
                return updated;
              });
            }
          } catch { /* ignore malformed chunk */ }
        }
      }
    } catch (e) {
      setMessages(m => {
        const updated = [...m];
        updated[updated.length - 1] = { role: 'assistant', content: '⚠️ Network error — is the backend running?' };
        return updated;
      });
    }
    setStreaming(false);
  };

  const SUGGESTIONS = [
    'Why did the #1 supplier win?',
    'Why does this need manager approval?',
    'Can we use our preferred supplier?',
    'What would make this easier to approve next time?',
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-xl flex flex-col p-0">
        <SheetHeader className="px-4 py-3 border-b shrink-0">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Bot className="h-4 w-4 text-primary" />
            Ask AI about this decision
          </SheetTitle>
        </SheetHeader>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="py-6">
              <p className="text-sm text-muted-foreground mb-3">Ask me anything — I'll explain what happened in plain English, no technical jargon.</p>
              <div className="space-y-2">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s}
                    className="w-full text-left text-sm px-3 py-2 rounded-lg border hover:bg-muted/50 transition-colors text-muted-foreground hover:text-foreground"
                    onClick={() => sendMessage(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-foreground'
                }`}
              >
                {msg.content ? (
                  msg.role === 'assistant' ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                        ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{children}</ol>,
                        li: ({ children }) => <li className="leading-snug">{children}</li>,
                        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                        em: ({ children }) => <em className="italic">{children}</em>,
                        h3: ({ children }) => <h3 className="font-semibold text-sm mb-1 mt-2">{children}</h3>,
                        code: ({ children }) => <code className="bg-background/60 rounded px-1 text-xs font-mono">{children}</code>,
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  ) : (
                    <span>{msg.content}</span>
                  )
                ) : (streaming && i === messages.length - 1 ? (
                  <span className="flex items-center gap-1 text-muted-foreground text-xs">
                    <span className="animate-bounce">●</span>
                    <span className="animate-bounce [animation-delay:0.1s]">●</span>
                    <span className="animate-bounce [animation-delay:0.2s]">●</span>
                  </span>
                ) : '…')}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t px-4 py-3 shrink-0">
          <form
            onSubmit={(e) => { e.preventDefault(); sendMessage(input); }}
            className="flex items-center gap-2"
          >
            <input
              className="flex-1 text-sm border rounded-lg px-3 py-2 bg-background focus:outline-none focus:ring-2 focus:ring-primary/50"
              placeholder="Ask about a supplier, rule, or decision…"
              value={input}
              onChange={e => setInput(e.target.value)}
              disabled={streaming || !executionLog}
            />
            <Button type="submit" size="sm" disabled={!input.trim() || streaming || !executionLog} className="shrink-0">
              <Send className="h-4 w-4" />
            </Button>
          </form>
          {!executionLog && (
            <p className="text-xs text-muted-foreground mt-1">Loading execution log…</p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────


export default function TransparencyPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user } = useAuth();
  const requestId = searchParams.get('id') ?? '';

  const [log, setLog] = useState<ExecutionLog | null>(null);
  const [loading, setLoading] = useState(true);
  const [chatOpen, setChatOpen] = useState(false);
  const [summary, setSummary] = useState('');
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [additionalDetailsOpen, setAdditionalDetailsOpen] = useState(false);

  useEffect(() => {
    if (!requestId) return;
    fetch(`${API}/requests/${requestId}/execution-log`, { credentials: 'include' })
      .then(r => {
        if (!r.ok) throw new Error('Not found');
        return r.json();
      })
      .then((data: ExecutionLog) => {
        setLog(data);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [requestId]);

  useEffect(() => {
    if (!requestId) return;
    setSummaryLoading(true);
    fetch(`${API}/requests/${requestId}/ai-summary`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.summary) setSummary(data.summary); })
      .catch(() => {})
      .finally(() => setSummaryLoading(false));
  }, [requestId]);

  const evaluatedSuppliers = useMemo(
    () => (log?.supplier_logs ?? []).filter(s => s.action_logs?.length > 0),
    [log]
  );

  const snapshot = log?.global_context_snapshot ?? {};

  if (loading) {
    return (
      <div className="flex h-[70vh] items-center justify-center">
        <div className="text-center space-y-3">
          <div className="h-10 w-10 animate-spin rounded-full border-b-2 border-primary mx-auto" />
          <p className="text-sm text-muted-foreground">Loading execution log…</p>
        </div>
      </div>
    );
  }

  if (!log) {
    return (
      <div className="py-8 w-full max-w-5xl mx-auto">
        <div className="text-center py-20 text-muted-foreground space-y-3">
          <AlertTriangle className="h-10 w-10 mx-auto opacity-40" />
          <p className="font-medium">No execution log found for {requestId}</p>
          <p className="text-sm">This request may not have an AI evaluation log yet.</p>
          <Button variant="outline" size="sm" onClick={() => router.back()}>Go back</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="py-6 w-full max-w-6xl mx-auto space-y-6 print:py-2">

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap print:hidden">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" className="gap-1 text-muted-foreground" onClick={() => router.back()}>
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">AI Decision Log</h1>
            <p className="text-muted-foreground text-sm">{new Date(log.timestamp).toLocaleString()}</p>
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" className="gap-2" onClick={() => window.print()}>
            <Printer className="h-4 w-4" /> Download PDF
          </Button>
          <Button size="sm" className="gap-2" onClick={() => setChatOpen(true)}>
            <Bot className="h-4 w-4" /> Ask AI
          </Button>
        </div>
      </div>

      {/* Print header (only visible when printing) */}
      <div className="hidden print:block mb-4">
        <h1 className="text-xl font-bold">AI Decision Log — {log.request_id}</h1>
        <p className="text-sm text-muted-foreground">Evaluated: {new Date(log.timestamp).toLocaleString()}</p>
      </div>

      {/* AI Summary + Request Details — side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* AI Summary */}
        <Card className="border-primary/20 bg-primary/5 dark:bg-primary/10">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <Bot className="h-4 w-4 text-primary" />
              What happened with this request
            </CardTitle>
          </CardHeader>
          <CardContent>
            {summaryLoading && !summary && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="animate-bounce">●</span>
                <span className="animate-bounce [animation-delay:0.1s]">●</span>
                <span className="animate-bounce [animation-delay:0.2s]">●</span>
                <span className="ml-1">Generating summary…</span>
              </div>
            )}
            {summary && (
              <div className="text-sm leading-relaxed prose-sm max-w-none">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    p:      ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                    ul:     ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-1">{children}</ul>,
                    ol:     ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-1">{children}</ol>,
                    li:     ({ children }) => <li className="leading-snug">{children}</li>,
                    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                  }}
                >
                  {summary}
                </ReactMarkdown>
              </div>
            )}
            {!summaryLoading && !summary && (
              <p className="text-sm text-muted-foreground">No AI summary available.</p>
            )}
          </CardContent>
        </Card>

        {/* Request Details — compact, always visible */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Request Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Category</span>
                <span className="font-medium">{String(snapshot.category_l1 ?? '—')}{snapshot.category_l2 ? ` / ${String(snapshot.category_l2)}` : ''}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Budget</span>
                <span className="font-medium">{snapshot.budget != null ? `${Number(snapshot.budget).toLocaleString()} ${String(snapshot.currency ?? '')}` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Quantity</span>
                <span className="font-medium">{snapshot.quantity != null ? `${String(snapshot.quantity)} ${String(snapshot.amount_unit ?? '')}` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Delivery</span>
                <span className="font-medium">{String(snapshot.delivery_country ?? '—')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Lead time</span>
                <span className="font-medium">{snapshot.days_until_required != null ? `${String(snapshot.days_until_required)} days` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Preferred</span>
                <span className="font-medium">{String(snapshot.preferred_supplier_mentioned ?? '—')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Incumbent</span>
                <span className="font-medium">{String(snapshot.incumbent_supplier ?? '—')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Data residency</span>
                <span className="font-medium">{snapshot.data_residency_constraint ? 'Yes' : 'No'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">ESG required</span>
                <span className="font-medium">{snapshot.esg_requirement ? 'Yes' : 'No'}</span>
              </div>
              {Object.entries(snapshot)
                .filter(([key]) => !['category_l1','category_l2','budget','currency','quantity','amount_unit','delivery_country','days_until_required','preferred_supplier_mentioned','incumbent_supplier','data_residency_constraint','esg_requirement'].includes(key))
                .map(([key, val]) => (
                  <div key={key} className="flex justify-between">
                    <span className="text-muted-foreground">{CONTEXT_LABELS[key] ?? key.replace(/_/g, ' ')}</span>
                    <span className="font-medium">{String(val)}</span>
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Supplier Ranking — ranked list */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Supplier Ranking</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {evaluatedSuppliers.length === 0 ? (
            <p className="px-6 py-6 text-sm text-muted-foreground">No suppliers passed the category filter for evaluation.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground uppercase tracking-wide bg-muted/30">
                    <th className="px-4 py-2 w-10">#</th>
                    <th className="px-4 py-2">Supplier</th>
                    <th className="px-4 py-2 text-right">Unit Price</th>
                    <th className="px-4 py-2 text-right">Total Cost</th>
                    <th className="px-4 py-2 text-right">Lead Time</th>
                    <th className="px-4 py-2 w-24">Quality</th>
                    <th className="px-4 py-2 w-24">Risk</th>
                    <th className="px-4 py-2 w-24">ESG</th>
                    <th className="px-4 py-2 w-24">Supplier Score</th>
                  </tr>
                </thead>
                <tbody>
                  {evaluatedSuppliers
                    .slice()
                    .sort((a, b) => (a.final_cost_rank_score ?? 1) - (b.final_cost_rank_score ?? 1))
                    .map((s, i) => {
                      const rank = i + 1;
                      const m = supplierMetrics(s);
                      const isWinner = rank === 1;
                      return (
                        <tr
                          key={`${s.supplier_id}-${s.category_l2}`}
                          className={`border-b last:border-0 transition-colors ${
                            isWinner
                              ? 'bg-emerald-50/60 dark:bg-emerald-950/20'
                              : 'hover:bg-muted/30'
                          }`}
                        >
                          <td className="px-4 py-3">
                            <span className={`font-bold ${isWinner ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                              {rank}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-medium">{s.supplier_name}</span>
                              {isWinner && <Star className="h-3.5 w-3.5 text-emerald-500 fill-emerald-400 shrink-0" />}
                              {m.isPreferred && <Badge variant="outline" className="text-[10px] text-blue-600 border-blue-200 bg-blue-50 dark:bg-blue-950/40 dark:text-blue-300">Preferred</Badge>}
                              {m.isIncumbent && <Badge variant="outline" className="text-[10px] text-violet-600 border-violet-200 bg-violet-50 dark:bg-violet-950/40 dark:text-violet-300">Incumbent</Badge>}
                            </div>
                            <p className="text-xs text-muted-foreground mt-0.5">{s.category_l2}</p>
                          </td>
                          <td className="px-4 py-3 text-right font-mono text-xs">
                            {m.unitPrice != null
                              ? `${m.currency} ${Number(m.unitPrice).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                              : <span className="text-muted-foreground">—</span>}
                          </td>
                          <td className="px-4 py-3 text-right font-mono text-xs">
                            {m.totalCost != null
                              ? `${m.currency} ${Number(m.totalCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                              : <span className="text-muted-foreground">—</span>}
                          </td>
                          <td className="px-4 py-3 text-right text-xs">
                            {m.leadStd != null
                              ? <>{String(m.leadStd)}d{m.leadExp != null ? <span className="text-muted-foreground"> / {String(m.leadExp)}d</span> : ''}</>
                              : <span className="text-muted-foreground">—</span>}
                          </td>
                          <td className="px-4 py-3">{scoreBar(m.quality as number | null) ?? <span className="text-xs text-muted-foreground">—</span>}</td>
                          <td className="px-4 py-3">{scoreBar(m.risk as number | null, true) ?? <span className="text-xs text-muted-foreground">—</span>}</td>
                          <td className="px-4 py-3">{scoreBar(m.esg as number | null) ?? <span className="text-xs text-muted-foreground">—</span>}</td>
                          <td className="px-4 py-3">{s.final_cost_rank_score != null ? (
                            <div className="flex items-center gap-1.5">
                              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                                <div className="h-full rounded-full bg-emerald-400" style={{ width: `${Math.min(100, Math.max(0, s.final_cost_rank_score))}%` }} />
                              </div>
                              <span className="text-xs font-mono w-5 text-right text-muted-foreground">{Math.round(s.final_cost_rank_score)}</span>
                            </div>
                          ) : <span className="text-xs text-muted-foreground">—</span>}</td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Additional Details — collapsible section */}
      <div className="relative">
        <button
          className="w-full flex items-center justify-center gap-2 py-3 text-sm text-muted-foreground hover:text-foreground transition-colors group"
          onClick={() => setAdditionalDetailsOpen(o => !o)}
        >
          <div className="flex-1 h-px bg-border" />
          <span className="flex items-center gap-1.5 shrink-0 px-3">
            {additionalDetailsOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            Additional Details
          </span>
          <div className="flex-1 h-px bg-border" />
        </button>
      </div>

      {additionalDetailsOpen && (
        <div className="space-y-6">
          {/* Funnel + Escalation */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Supplier Evaluation Funnel</CardTitle>
              </CardHeader>
              <CardContent>
                <SupplierFunnel logs={log.supplier_logs} />
                <ExcludedSuppliers logs={log.supplier_logs} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">What Needs Your Attention</CardTitle>
                <p className="text-xs text-muted-foreground">Issues the system flagged that require a human decision.</p>
              </CardHeader>
              <CardContent>
                <EscalationMap logs={log.supplier_logs} />
              </CardContent>
            </Card>
          </div>

          {/* Decision Log */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Decision Log — Actions per Supplier</CardTitle>
              <p className="text-xs text-muted-foreground">
                Each row is one rule action executed by the pipeline.
                <span className="inline-block ml-2 px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400 text-[10px]">amber</span> = value changed ·
                <span className="inline-block ml-2 px-1.5 py-0.5 rounded bg-orange-50 dark:bg-orange-950/20 text-orange-700 dark:text-orange-400 text-[10px]">orange</span> = escalation triggered ·
                <span className="opacity-40 inline-block ml-2">dimmed</span> = skipped (WHEN condition not met)
              </p>
            </CardHeader>
            <CardContent className="p-0">
              {evaluatedSuppliers.length === 0 ? (
                <p className="px-6 py-8 text-sm text-muted-foreground">No suppliers were evaluated through the action pipeline.</p>
              ) : (
                <Tabs defaultValue={`${evaluatedSuppliers[0].supplier_id}-${evaluatedSuppliers[0].category_l2}`}>
                  <div className="px-4 pt-2 border-b">
                    <TabsList className="h-auto flex-wrap gap-1 bg-transparent p-0">
                      {evaluatedSuppliers.map(s => {
                        const key = `${s.supplier_id}-${s.category_l2}`;
                        const rank = s.final_cost_rank_score != null
                          ? `#${evaluatedSuppliers.filter(x => x.final_cost_rank_score != null).sort((a, b) => (a.final_cost_rank_score ?? 0) - (b.final_cost_rank_score ?? 0)).findIndex(x => x.supplier_id === s.supplier_id && x.category_l2 === s.category_l2) + 1}`
                          : null;
                        return (
                          <TabsTrigger key={key} value={key} className="text-xs h-8">
                            {rank && <span className="mr-1 font-bold text-emerald-600">{rank}</span>}
                            {s.supplier_name}
                          </TabsTrigger>
                        );
                      })}
                    </TabsList>
                  </div>
                  {evaluatedSuppliers.map(s => {
                    const key = `${s.supplier_id}-${s.category_l2}`;
                    return (
                      <TabsContent key={key} value={key} className="mt-0">
                        {(Object.keys(s.pricing_resolved).length > 0 || s.final_cost_rank_score != null) && (
                          <div className="px-4 py-3 border-b flex flex-wrap gap-4 text-xs">
                            {Object.entries(s.pricing_resolved).map(([k, v]) => (
                              <div key={k}>
                                <span className="text-muted-foreground">{k.replace(/_/g, ' ')}: </span>
                                <span className="font-mono font-medium">{String(v)}</span>
                              </div>
                            ))}
                            {s.final_cost_rank_score != null && (
                              <>
                                <div>
                                  <span className="text-muted-foreground">cost rank score: </span>
                                  <span className="font-mono font-medium">{s.final_cost_rank_score.toFixed(4)}</span>
                                </div>
                                <div>
                                  <span className="text-muted-foreground">reputation score: </span>
                                  <span className="font-mono font-medium">{(s.final_reputation_score ?? 0).toFixed(4)}</span>
                                </div>
                              </>
                            )}
                          </div>
                        )}
                        <ActionPipelineTable actions={s.action_logs} />
                      </TabsContent>
                    );
                  })}
                </Tabs>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Ask AI Drawer */}
      <AiChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        requestId={requestId}
        executionLog={log}
      />
    </div>
  );
}
