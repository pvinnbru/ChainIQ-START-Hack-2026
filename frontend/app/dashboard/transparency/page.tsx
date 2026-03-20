'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft, Bot, Printer, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Send, Trash2,
  AlertTriangle, CheckCircle, Minus, ArrowRight, Star, Package, Clock, ShieldCheck, TrendingUp,
  Gauge, Mail,
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
  final_normalized_rank: number | null;
  final_reputation_score: number | null;
}

interface ConfidenceBreakdown {
  dimensions: Record<string, number>;
  weights: Record<string, number>;
  worst_dimension: string;
  meta: Record<string, unknown>;
}

interface ConfidenceAssessment {
  score: number;
  label: string;
  explanation: string;
  breakdown: ConfidenceBreakdown;
}

interface FlagAssessment {
  flags: { flag_id: string; severity: string; description: string }[];
  has_warnings?: boolean;
}

interface ExecutionLog {
  request_id: string;
  timestamp: string;
  global_context_snapshot: Record<string, unknown>;
  supplier_logs: SupplierLog[];
  confidence_assessment?: ConfidenceAssessment | null;
  flag_assessment?: FlagAssessment | null;
  global_outputs?: Record<string, unknown> | null;
  escalation_assessment?: Record<string, unknown> | null;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const RULE_COLORS: Record<string, string> = {
  ER: 'bg-orange-100 text-orange-800 border-orange-300 dark:bg-orange-950/60 dark:text-orange-300 dark:border-orange-800',
  AT: 'bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-950/60 dark:text-blue-300 dark:border-blue-800',
  CR: 'bg-purple-100 text-purple-800 border-purple-300 dark:bg-purple-950/60 dark:text-purple-300 dark:border-purple-800',
  RANKING: 'bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800',
};

const ESCALATION_KEY_MAP: Record<string, string> = {
  escalate_to_requester: 'Requester Clarification',
  requester_clarification: 'Requester Clarification',
  requester: 'Requester Clarification',
  escalate_to_procurement_manager: 'Procurement Manager',
  procurement_manager: 'Procurement Manager',
  buyer: 'Procurement Manager',
  escalate_to_head_of_category: 'Head of Category',
  category_head: 'Head of Category',
  escalate_to_security_compliance: 'Compliance Review',
  compliance: 'Compliance Review',
  escalate_to_head_of_strategic_sourcing: 'Strategic Sourcing Lead',
  head_of_strategic_sourcing: 'Strategic Sourcing Lead',
  escalate_to_cpo: 'CPO',
  cpo: 'CPO',
  escalate_to_sourcing_excellence: 'Sourcing Excellence Lead',
  sourcing_excellence: 'Sourcing Excellence Lead',
  escalate_to_marketing_governance: 'Marketing Governance Lead',
  marketing_governance: 'Marketing Governance Lead',
  escalate_to_regional_compliance: 'Regional Compliance Lead',
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
    const shortlisted = logs.filter(s => s.final_normalized_rank != null).length;
    return [
      { label: 'Suppliers Considered', count: total, bg: 'bg-slate-200 dark:bg-slate-700' },
      { label: 'Category Match', count: catMatch, bg: 'bg-blue-200 dark:bg-blue-900' },
      { label: 'Evaluated', count: evaluated, bg: 'bg-amber-200 dark:bg-amber-900' },
      { label: 'Shortlisted', count: shortlisted, bg: 'bg-emerald-200 dark:bg-emerald-900' },
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
  const mid = invert ? pct < 60 : pct > 35;
  const colorClass = good ? 'text-emerald-400' : mid ? 'text-amber-400' : 'text-red-400';
  const colorHex = good ? '#4ade80' : mid ? '#facc15' : '#f87171';

  // Donut chart: radius 18, circumference ~113.1 (for 44px container)
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex items-center justify-center relative" style={{ width: '44px', height: '44px' }}>
      <svg width="44" height="44" viewBox="0 0 44 44" className="transform -rotate-90">
        {/* Background circle */}
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-muted/40"
        />
        {/* Progress circle */}
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          stroke={colorHex}
          strokeWidth="2"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all"
        />
      </svg>
      {/* Center text */}
      <span className="absolute text-[11px] font-mono font-semibold text-muted-foreground">{Math.round(pct)}</span>
    </div>
  );
}

function supplierMetrics(s: SupplierLog) {
  const pr = s.pricing_resolved as Record<string, unknown>;
  const fs = s.final_state as Record<string, unknown>;
  return {
    unitPrice: pr.unit_price ?? pr.unit_price_eur,
    currency: String(pr.currency ?? 'EUR'),
    totalCost: pr.cost_total ?? fs?.cost_total,
    leadStd: pr.standard_lead_time_days ?? pr.lead_time_days,
    leadExp: pr.expedited_lead_time_days as number | undefined,
    // The 4 gauge scores — all from documented evaluate_request.py output:
    costRankScore: fs?.cost_rank_score as number | undefined,    // inverted cost score, 0–100
    reputationScore: fs?.reputation_score as number | undefined, // quality/risk/ESG composite, 0–100
    complianceScore: fs?.compliance_score as number | undefined, // penalty multiplier, 0–1
    isPreferred: fs?.preferred as boolean | undefined,
    isIncumbent: fs?.incumbent as boolean | undefined,
  };
}

function SupplierCard({ supplier: s, rank }: { supplier: SupplierLog; rank: number }) {
  const { unitPrice, currency, totalCost, leadStd, leadExp, costRankScore, reputationScore, complianceScore, isPreferred, isIncumbent } = supplierMetrics(s);
  const isWinner = rank === 1;

  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-3 ${isWinner
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
      {(costRankScore != null || reputationScore != null || complianceScore != null) && (
        <div className="space-y-1.5 pt-1 border-t">
          {costRankScore != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">Cost</span>
              {scoreBar(costRankScore)}
            </div>
          )}
          {reputationScore != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">Reputation</span>
              {scoreBar(reputationScore)}
            </div>
          )}
          {complianceScore != null && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">Compliance</span>
              {scoreBar(Math.round(complianceScore * 100))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Action Pipeline Table ─────────────────────────────────────────────────────

const RULE_GROUP_LABELS: Record<string, string> = {
  ER: 'Escalation Rules',
  AT: 'Approval Threshold Rules',
  CR: 'Compliance Rules',
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
  const changed = actions.filter(a => !a.skipped && JSON.stringify(a.output_value_before) !== JSON.stringify(a.output_value_after)).length;
  const skipped = actions.filter(a => a.skipped).length;
  const label = RULE_GROUP_LABELS[prefix] ?? `${prefix} Rules`;

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
          {changed > 0 && <span className="text-amber-600 dark:text-amber-400">{changed} changed</span>}
          {skipped > 0 && <span className="opacity-50">{skipped} skipped</span>}
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

// ── Confidence Score Card ─────────────────────────────────────────────────────

const DIMENSION_META: Record<string, { label: string; icon: string }> = {
  input_completeness: { label: 'Input Completeness', icon: '📋' },
  market_coverage: { label: 'Market Coverage', icon: '🏪' },
  ranking_decisiveness: { label: 'Ranking Decisiveness', icon: '🎯' },
  data_reliability: { label: 'Data Reliability', icon: '📊' },
  compliance_quality: { label: 'Compliance Quality', icon: '✅' },
};

const CONFIDENCE_COLORS: Record<string, { bg: string; text: string; ring: string; hex: string }> = {
  high: { bg: 'bg-emerald-100 dark:bg-emerald-950/40', text: 'text-emerald-700 dark:text-emerald-400', ring: 'ring-emerald-500', hex: '#10b981' },
  medium: { bg: 'bg-amber-100 dark:bg-amber-950/40', text: 'text-amber-700 dark:text-amber-400', ring: 'ring-amber-500', hex: '#f59e0b' },
  low: { bg: 'bg-orange-100 dark:bg-orange-950/40', text: 'text-orange-700 dark:text-orange-400', ring: 'ring-orange-500', hex: '#f97316' },
  very_low: { bg: 'bg-red-100 dark:bg-red-950/40', text: 'text-red-700 dark:text-red-400', ring: 'ring-red-500', hex: '#ef4444' },
};

function ConfidenceScoreCard({ assessment }: { assessment: ConfidenceAssessment | null | undefined }) {
  if (!assessment) return null;

  const { score, label, explanation, breakdown } = assessment;
  const pct = Math.round(score * 100);
  const colors = CONFIDENCE_COLORS[label] ?? CONFIDENCE_COLORS.medium;
  const dims = breakdown?.dimensions ?? {};
  const weights = breakdown?.weights ?? {};
  const worstDim = breakdown?.worst_dimension;

  // Large donut gauge
  const radius = 38;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <Gauge className="h-4 w-4 text-muted-foreground" />
          Ranking Confidence
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col sm:flex-row gap-6">
          {/* Left: score gauge + label */}
          <div className="flex flex-col items-center gap-2 shrink-0">
            <div className="relative" style={{ width: '96px', height: '96px' }}>
              <svg width="96" height="96" viewBox="0 0 96 96" className="transform -rotate-90">
                <circle cx="48" cy="48" r={radius} fill="none" stroke="currentColor" strokeWidth="4" className="text-muted/30" />
                <circle cx="48" cy="48" r={radius} fill="none" stroke={colors.hex} strokeWidth="4" strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" className="transition-all duration-700" />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-bold font-mono">{pct}</span>
                <span className="text-[10px] text-muted-foreground">/ 100</span>
              </div>
            </div>
            <Badge variant="outline" className={`text-xs font-semibold capitalize px-2.5 py-0.5 ${colors.bg} ${colors.text} border-0`}>
              {label.replace('_', ' ')}
            </Badge>
          </div>

          {/* Right: explanation + dimension bars */}
          <div className="flex-1 min-w-0 space-y-3">
            {/* Explanation */}
            <p className="text-sm text-muted-foreground leading-snug">{explanation}</p>

            {/* Dimension breakdown */}
            <div className="space-y-2">
              {Object.entries(dims).map(([key, value]) => {
                const meta = DIMENSION_META[key] ?? { label: key.replace(/_/g, ' '), icon: '•' };
                const dimPct = Math.round(value * 100);
                const weight = weights[key];
                const isWorst = key === worstDim;
                const dimGood = dimPct >= 65;
                const dimMid = dimPct >= 40;
                const barColor = dimGood ? 'bg-emerald-500' : dimMid ? 'bg-amber-500' : 'bg-red-500';

                return (
                  <div key={key} className={`group ${isWorst ? 'bg-muted/50 -mx-2 px-2 py-1 rounded-lg border border-border/60' : ''}`}>
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <div className="flex items-center gap-1.5 text-xs">
                        <span className="w-4 text-center text-sm leading-none">{meta.icon}</span>
                        <span className={`font-medium ${isWorst ? 'text-foreground' : 'text-muted-foreground'}`}>{meta.label}</span>
                        {isWorst && <span className="text-[10px] text-orange-600 dark:text-orange-400 font-medium">(weakest)</span>}
                      </div>
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono">
                        <span className={`font-semibold ${isWorst ? 'text-foreground' : ''}`}>{dimPct}%</span>
                        {weight != null && <span className="text-[10px] opacity-60">×{Math.round(weight * 100)}%</span>}
                      </div>
                    </div>
                    <div className="h-1.5 bg-muted/60 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${dimPct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Escalation Map ────────────────────────────────────────────────────────────

function EscalationMap({ assessment }: { assessment: any }) {
  const escalations = useMemo(() => {
    if (!assessment || !Array.isArray(assessment.records)) return [];
    return assessment.records.map((r: any) => {
      const person = r.person_to_escalate_to || 'UNKNOWN';
      let label = ESCALATION_KEY_MAP[person];
      if (!label) {
        label = person.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
      }
      return {
        ruleId: r.source || 'RULE',
        outputKey: person + Math.random().toString(),
        label,
        description: r.reason_for_escalation || r.task_for_escalation || 'Review triggered',
      };
    });
  }, [assessment]);

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
      {escalations.map((esc: any, i: number) => (
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

function AiChatSidebar({
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

  if (!open) return null;

  return (
    <div className="w-96 flex flex-col border-l bg-card print:hidden">
      <div className="px-4 py-3 border-b shrink-0 flex items-center justify-between">
        <div className="flex items-center gap-2 text-base font-semibold">
          <Bot className="h-4 w-4 text-red-500" />
          Ask AI
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
            onClick={() => setMessages([])}
            title="Clear chat"
            disabled={messages.length === 0}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={() => onOpenChange(false)}
            title="Close AI Chat"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="py-6">
            <p className="text-sm text-muted-foreground mb-3">Ask me anything — I&apos;ll explain what happened in plain English, no technical jargon.</p>
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
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed ${msg.role === 'user'
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
                      table: ({ children }) => (
                        <div className="overflow-x-auto my-2 -mx-3">
                          <table className="w-full text-xs border-collapse">{children}</table>
                        </div>
                      ),
                      thead: ({ children }) => <thead className="bg-background/50">{children}</thead>,
                      tbody: ({ children }) => <tbody>{children}</tbody>,
                      tr: ({ children }) => <tr className="border-b border-border/40 last:border-0">{children}</tr>,
                      th: ({ children }) => <th className="px-3 py-1.5 text-left font-semibold text-muted-foreground whitespace-nowrap">{children}</th>,
                      td: ({ children }) => <td className="px-3 py-1.5 align-top">{children}</td>,
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
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────


export default function TransparencyPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user } = useAuth();
  const requestId = searchParams.get('id') ?? '';

  // Only higher-rank roles can access the transparency view
  const isRequester = user?.role === 'requester';
  useEffect(() => {
    if (isRequester) {
      router.replace('/dashboard');
    }
  }, [isRequester, router]);

  if (isRequester) {
    return (
      <div className="flex h-[70vh] items-center justify-center">
        <div className="text-center space-y-3">
          <AlertTriangle className="h-10 w-10 mx-auto opacity-40" />
          <p className="font-medium">Access restricted</p>
          <p className="text-sm text-muted-foreground">The AI Decision Log is only available to approvers and reviewers.</p>
          <Button variant="outline" size="sm" onClick={() => router.push('/dashboard')}>Back to Dashboard</Button>
        </div>
      </div>
    );
  }

  const [log, setLog] = useState<ExecutionLog | null>(null);
  const [requestTitle, setRequestTitle] = useState('');
  const [loading, setLoading] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);
  const [summary, setSummary] = useState('');
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [additionalDetailsOpen, setAdditionalDetailsOpen] = useState(false);

  useEffect(() => {
    if (!requestId) return;
    fetch(`${API}/requests/${requestId}`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        setRequestTitle(data.title || data.plain_text?.slice(0, 80) || '');
      })
      .catch(() => { });
  }, [requestId]);

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
      .catch(() => { })
      .finally(() => setLoading(false));
  }, [requestId]);

  useEffect(() => {
    if (!requestId) return;
    setSummaryLoading(true);
    fetch(`${API}/requests/${requestId}/ai-summary`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.summary) setSummary(data.summary); })
      .catch(() => { })
      .finally(() => setSummaryLoading(false));
  }, [requestId]);

  const evaluatedSuppliers = useMemo(
    () => (log?.supplier_logs ?? []).filter(s => s.action_logs?.length > 0),
    [log]
  );

  // min_supplier_quotes lives in final_state of each supplier action log (not in execution_log root)
  const minSupplierQuotes = useMemo(() => {
    const fs = log?.supplier_logs?.[0]?.final_state as Record<string, unknown> | undefined;
    const v = fs?.min_supplier_quotes;
    return typeof v === 'number' ? v : 1;
  }, [log]);

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
    <div className="flex h-screen overflow-hidden relative print:block print:h-auto print:overflow-visible">
      {/* Main content */}
      <div className="flex-1 overflow-y-auto py-6 pr-4 print:flex-none print:overflow-visible print:h-auto print:w-full print:pr-0 print:py-0 print:flex print:justify-center">
        <div className="w-full max-w-6xl mx-auto space-y-6 print:py-6 print:px-0 print:w-[185mm] print:max-w-[185mm]">

          {/* Header */}
          <div className="flex items-start justify-between gap-4 flex-wrap print:hidden">
            <div className="flex items-center gap-3">
              <Button variant="ghost" size="sm" className="gap-1 text-muted-foreground" onClick={() => router.back()}>
                <ArrowLeft className="h-4 w-4" /> Back
              </Button>
              <div>
                <h1 className="text-2xl font-bold tracking-tight">{'AI Decision Log'}</h1>
                <p className="text-muted-foreground text-sm">{new Date(log.timestamp).toLocaleString()}</p>
              </div>
            </div>
            <div className="flex gap-2 shrink-0">
              <Button variant="outline" size="sm" className="gap-2" onClick={() => window.print()}>
                <Printer className="h-4 w-4" />Report
              </Button>
              {!chatOpen && (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  onClick={() => setChatOpen(true)}
                  title="Open AI Chat"
                >
                  <Bot className="h-4 w-4 text-red-500" />
                  <ChevronLeft className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>

          {/* ── Print-only audit report cover ─────────────────────────────── */}
          <div className="hidden print:block text-black">

            {/* ── COVER ─────────────────────────────────────────────────────── */}
            <div className="flex items-start justify-between pb-5 mb-5 border-b-2 border-gray-900">
              <div>
                <p className="text-[9px] uppercase tracking-[0.2em] text-gray-500 mb-0.5 font-medium">ChainIQ AI Sourcing Platform</p>
                <h1 className="text-xl font-bold text-gray-900 leading-tight mb-1">Procurement Audit Report</h1>
                <p className="text-sm font-semibold text-gray-800">{requestTitle || 'AI Procurement Decision Log'}</p>
              </div>
              <div className="text-right text-[10px] text-gray-500 space-y-0.5 mt-1">
                <p><span className="font-semibold">Request ID:</span> {log.request_id}</p>
                <p><span className="font-semibold">Generated:</span> {new Date(log.timestamp).toLocaleString()}</p>
                <p><span className="font-semibold">Classification:</span> Confidential</p>
                {log.confidence_assessment && (
                  <p><span className="font-semibold">Ranking Confidence:</span>{' '}
                    <span style={{ color: log.confidence_assessment.label === 'high' ? '#15803d' : log.confidence_assessment.label === 'medium' ? '#b45309' : '#b91c1c', fontWeight: 700 }}>
                      {Math.round(log.confidence_assessment.score * 100)}% — {log.confidence_assessment.label.replace('_', ' ').toUpperCase()}
                    </span>
                  </p>
                )}
              </div>
            </div>

            {/* ── SECTION 1: REQUEST PARAMETERS ────────────────────────────── */}
            <div className="mb-6">
              <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-2 pb-0.5 border-b border-gray-300">1 · Request Parameters</p>
              <table className="w-full text-[10px] border-collapse">
                <tbody>
                  {([
                    ['Category (L1 / L2)', `${String(snapshot.category_l1 ?? '—')}${snapshot.category_l2 ? ` / ${String(snapshot.category_l2)}` : ''}`],
                    ['Budget', snapshot.budget != null ? `${Number(snapshot.budget).toLocaleString()} ${String(snapshot.currency ?? '')}` : '—'],
                    ['Quantity', snapshot.quantity != null ? `${String(snapshot.quantity)} ${String(snapshot.amount_unit ?? '')}` : '—'],
                    ['Delivery Country', String(snapshot.delivery_country ?? '—')],
                    ['Days Until Required', snapshot.days_until_required != null ? `${String(snapshot.days_until_required)} days` : '—'],
                    ['Preferred Supplier Mentioned', String(snapshot.preferred_supplier_mentioned ?? '—')],
                    ['Incumbent Supplier', String(snapshot.incumbent_supplier ?? '—')],
                    ['Data Residency Constraint', snapshot.data_residency_constraint ? 'Yes' : 'No'],
                    ['ESG Requirement', snapshot.esg_requirement ? 'Yes' : 'No'],
                  ] as [string, string][]).map(([label, value], i) => (
                    <tr key={i} style={{ backgroundColor: i % 2 === 0 ? '#f9fafb' : '#ffffff' }}>
                      <td className="border border-gray-200 px-2.5 py-1 font-semibold text-gray-600 w-52">{label}</td>
                      <td className="border border-gray-200 px-2.5 py-1 text-gray-900">{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* ── SECTION 2: AI ASSESSMENT SUMMARY ─────────────────────────── */}
            {summary && (
              <div className="mb-6">
                <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-2 pb-0.5 border-b border-gray-300">2 · AI Assessment Summary</p>
                <div className="border border-gray-200 rounded-sm p-3 bg-gray-50 text-[10px] text-gray-800 leading-relaxed whitespace-pre-line">
                  {summary.replace(/\*\*/g, '').replace(/^[•\-]\s/gm, '• ')}
                </div>
              </div>
            )}

            {/* ── SECTION 3: RANKING CONFIDENCE ────────────────────────────── */}
            {log.confidence_assessment && (() => {
              const ca = log.confidence_assessment!;
              const dims = ca.breakdown?.dimensions ?? {};
              const weights = ca.breakdown?.weights ?? {};
              const worst = ca.breakdown?.worst_dimension;
              const pct = Math.round(ca.score * 100);
              return (
                <div className="mb-6">
                  <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-2 pb-0.5 border-b border-gray-300">3 · Ranking Confidence Assessment</p>
                  <div className="flex gap-6 items-start">
                    {/* Score pill */}
                    <div className="shrink-0 text-center border border-gray-300 rounded px-4 py-2 bg-gray-50">
                      <div className="text-2xl font-bold font-mono" style={{ color: pct >= 75 ? '#15803d' : pct >= 50 ? '#b45309' : '#b91c1c' }}>{pct}%</div>
                      <div className="text-[9px] uppercase tracking-wide text-gray-500 mt-0.5">{ca.label.replace('_', ' ')}</div>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[10px] text-gray-700 mb-2 italic">{ca.explanation}</p>
                      <table className="w-full text-[10px] border-collapse">
                        <thead>
                          <tr style={{ backgroundColor: '#e5e7eb' }}>
                            <th className="border border-gray-300 px-2 py-1 text-left font-semibold text-gray-700">Dimension</th>
                            <th className="border border-gray-300 px-2 py-1 text-center font-semibold text-gray-700 w-16">Score</th>
                            <th className="border border-gray-300 px-2 py-1 text-center font-semibold text-gray-700 w-16">Weight</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(dims).map(([key, val], i) => {
                            const dimPct = Math.round((val as number) * 100);
                            const w = weights[key];
                            const isWorst = key === worst;
                            return (
                              <tr key={key} style={{ backgroundColor: isWorst ? '#fef3c7' : i % 2 === 0 ? '#f9fafb' : '#ffffff' }}>
                                <td className="border border-gray-200 px-2 py-1 text-gray-700">
                                  {key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                  {isWorst && <span className="ml-1 text-[8px] font-semibold text-amber-700">(limiting factor)</span>}
                                </td>
                                <td className="border border-gray-200 px-2 py-1 text-center font-mono font-semibold" style={{ color: dimPct >= 65 ? '#15803d' : dimPct >= 40 ? '#b45309' : '#b91c1c' }}>{dimPct}%</td>
                                <td className="border border-gray-200 px-2 py-1 text-center text-gray-600">{w != null ? `${Math.round((w as number) * 100)}%` : '—'}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* ── SECTION 4: SUPPLIER RANKING ──────────────────────────────── */}
            {evaluatedSuppliers.length > 0 && (
              <div className="mb-6">
                <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-2 pb-0.5 border-b border-gray-300">4 · Supplier Ranking</p>
                <table className="w-full text-[10px] border-collapse">
                  <thead>
                    <tr style={{ backgroundColor: '#1f2937', color: '#ffffff' }}>
                      <th className="border border-gray-600 px-2 py-1.5 text-center w-6">#</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-left">Supplier</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-right">Unit Price</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-right">Total Cost</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-center">Lead Time</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-center w-14" title="normalized_rank × 100">Score</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-center w-16" title="Quality / Risk / ESG composite">Reputation</th>
                      <th className="border border-gray-600 px-2 py-1.5 text-center w-16" title="Compliance multiplier × 100">Compliance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...evaluatedSuppliers]
                      .sort((a, b) => (b.final_normalized_rank ?? 0) - (a.final_normalized_rank ?? 0))
                      .map((s, i) => {
                        const m = supplierMetrics(s);
                        const isWinner = i === 0;
                        const rank = s.final_normalized_rank;
                        const rankPct = rank != null ? Math.round(rank * 100) : null;
                        const repPct = m.reputationScore != null ? Math.round(m.reputationScore) : null;
                        const compPct = m.complianceScore != null ? Math.round(m.complianceScore * 100) : null;
                        return (
                          <tr key={i} style={{ backgroundColor: isWinner ? '#f0fdf4' : i % 2 === 0 ? '#f9fafb' : '#ffffff' }}>
                            <td className="border border-gray-200 px-2 py-1 text-center font-bold" style={{ color: isWinner ? '#15803d' : '#6b7280' }}>
                              {isWinner ? '★' : i + 1}
                            </td>
                            <td className="border border-gray-200 px-2 py-1">
                              <span className={isWinner ? 'font-bold' : 'font-medium'}>{s.supplier_name}</span>
                              {m.isPreferred && <span className="ml-1 text-[8px] font-semibold text-blue-700">[Preferred]</span>}
                              {m.isIncumbent && <span className="ml-1 text-[8px] font-semibold text-purple-700">[Incumbent]</span>}
                              <span className="ml-1 text-gray-400">· {s.category_l2}</span>
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-right font-mono">
                              {m.unitPrice != null ? `${m.currency} ${Number(m.unitPrice).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—'}
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-right font-mono">
                              {m.totalCost != null ? `${m.currency} ${Number(m.totalCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-center">
                              {m.leadStd != null ? `${m.leadStd}d` : '—'}
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-center font-mono font-bold" style={{ color: rankPct != null && rankPct >= 65 ? '#15803d' : rankPct != null && rankPct >= 40 ? '#b45309' : '#b91c1c' }}>
                              {rankPct != null ? `${rankPct}` : '—'}
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-center font-mono" style={{ color: repPct != null && repPct >= 65 ? '#15803d' : repPct != null && repPct >= 40 ? '#b45309' : '#6b7280' }}>
                              {repPct != null ? `${repPct}` : '—'}
                            </td>
                            <td className="border border-gray-200 px-2 py-1 text-center font-mono" style={{ color: compPct != null && compPct >= 95 ? '#15803d' : compPct != null && compPct >= 70 ? '#b45309' : '#b91c1c' }}>
                              {compPct != null ? `${compPct}%` : '—'}
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
                <p className="text-[8px] text-gray-400 mt-1">Score = normalized_rank × 100 (composite: 95% cost + 2.5% reputation + 2.5% historic performance, × compliance multiplier). ★ = recommended supplier.</p>
              </div>
            )}

            {/* ── SECTION 5: FLAGS, PROCESS REQUIREMENTS & ESCALATIONS ─────── */}
            <div className="mb-6">
              <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-3 pb-0.5 border-b border-gray-300">5 · Flags, Process Requirements & Escalations</p>

              {/* 5a: Flag Assessment */}
              {(() => {
                const fa = log.flag_assessment;
                const flagList = fa?.flags ?? [];
                return (
                  <div className="mb-3">
                    <p className="text-[9px] font-semibold text-gray-600 mb-1.5">5a · Result Quality Flags</p>
                    {flagList.length === 0 ? (
                      <p className="text-[10px] text-gray-600 border border-gray-200 px-2.5 py-1.5 bg-gray-50 rounded-sm">✓ No result quality flags raised — ranking is reliable.</p>
                    ) : (
                      <table className="w-full text-[10px] border-collapse">
                        <thead>
                          <tr style={{ backgroundColor: '#e5e7eb' }}>
                            <th className="border border-gray-300 px-2 py-1 text-left w-44">Flag</th>
                            <th className="border border-gray-300 px-2 py-1 text-center w-16">Severity</th>
                            <th className="border border-gray-300 px-2 py-1 text-left">Description</th>
                          </tr>
                        </thead>
                        <tbody>
                          {flagList.map((f, i) => (
                            <tr key={i} style={{ backgroundColor: f.severity === 'warning' ? '#fffbeb' : '#f9fafb' }}>
                              <td className="border border-gray-200 px-2 py-1 font-mono text-[9px] text-gray-700">{f.flag_id}</td>
                              <td className="border border-gray-200 px-2 py-1 text-center font-semibold" style={{ color: f.severity === 'warning' ? '#b45309' : '#3b82f6' }}>
                                {f.severity.toUpperCase()}
                              </td>
                              <td className="border border-gray-200 px-2 py-1 text-gray-700">{f.description}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                );
              })()}

              {/* 5b: Process Requirements (global_outputs) */}
              {(() => {
                const go = log.global_outputs ?? {};
                const requireKeys = Object.entries(go).filter(([k]) => k.startsWith('requires_'));
                const minQuotes = go.min_supplier_quotes as number | undefined;
                const fastTrack = go.fast_track_eligible as boolean | undefined;
                const LABELS: Record<string, string> = {
                  requires_security_review: 'Security Architecture Review',
                  requires_engineering_review: 'Engineering / CAD Review',
                  requires_design_signoff: 'Business Design Sign-off',
                  requires_cv_review: 'Named Consultant CVs',
                  requires_certification_check: 'Supplier Certification Check',
                  requires_brand_safety_review: 'Brand Safety Review',
                  requires_performance_baseline: 'SEM Performance Baseline',
                };
                const activeRequirements = requireKeys.filter(([, v]) => v === true);
                return (
                  <div className="mb-3">
                    <p className="text-[9px] font-semibold text-gray-600 mb-1.5">5b · Process Requirements</p>
                    <table className="w-full text-[10px] border-collapse">
                      <tbody>
                        <tr style={{ backgroundColor: '#f9fafb' }}>
                          <td className="border border-gray-200 px-2.5 py-1 font-semibold text-gray-600 w-52">Minimum Supplier Quotes</td>
                          <td className="border border-gray-200 px-2.5 py-1 font-bold text-gray-900">{minQuotes ?? 1}</td>
                        </tr>
                        <tr>
                          <td className="border border-gray-200 px-2.5 py-1 font-semibold text-gray-600">Fast-Track Eligible</td>
                          <td className="border border-gray-200 px-2.5 py-1" style={{ color: fastTrack ? '#15803d' : '#6b7280', fontWeight: fastTrack ? 700 : 400 }}>
                            {fastTrack ? '✓ Yes — single quote permitted' : 'No'}
                          </td>
                        </tr>
                        {activeRequirements.length === 0 ? (
                          <tr style={{ backgroundColor: '#f0fdf4' }}>
                            <td className="border border-gray-200 px-2.5 py-1 font-semibold text-gray-600">Additional Reviews Required</td>
                            <td className="border border-gray-200 px-2.5 py-1 text-green-700 font-medium">✓ None</td>
                          </tr>
                        ) : activeRequirements.map(([key], i) => (
                          <tr key={key} style={{ backgroundColor: i % 2 === 0 ? '#fffbeb' : '#ffffff' }}>
                            <td className="border border-gray-200 px-2.5 py-1 font-semibold text-gray-600">{i === 0 ? 'Additional Reviews Required' : ''}</td>
                            <td className="border border-gray-200 px-2.5 py-1 font-semibold text-amber-800">⚠ {LABELS[key] ?? key.replace(/_/g, ' ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              })()}

              {/* 5c: Escalations */}
              <div>
                <p className="text-[9px] font-semibold text-gray-600 mb-1.5">5c · Escalations</p>
                {(() => {
                  const records = log.escalation_assessment?.records;
                  const escs = Array.isArray(records) ? records.map((r: any) => {
                    const person = r.person_to_escalate_to || 'UNKNOWN';
                    let label = ESCALATION_KEY_MAP[person] || person.replace(/_/g, ' ');
                    return {
                      ruleId: r.source || 'RULE',
                      label: label,
                      description: r.reason_for_escalation || r.task_for_escalation || 'Review triggered',
                      severity: r.severity || 'blocking',
                    };
                  }) : [];
                  if (escs.length === 0) return (
                    <p className="text-[10px] text-gray-600 border border-gray-200 px-2.5 py-1.5 bg-gray-50 rounded-sm">✓ No escalations triggered — request can proceed autonomously.</p>
                  );
                  return (
                    <table className="w-full text-[10px] border-collapse">
                      <thead>
                        <tr style={{ backgroundColor: '#e5e7eb' }}>
                          <th className="border border-gray-300 px-2 py-1 text-left w-20">Rule</th>
                          <th className="border border-gray-300 px-2 py-1 text-left w-40">Escalation Required</th>
                          <th className="border border-gray-300 px-2 py-1 text-left">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {escs.map((e, i) => (
                          <tr key={i} style={{ backgroundColor: i % 2 === 0 ? '#fef2f2' : '#ffffff' }}>
                            <td className="border border-gray-200 px-2 py-1 font-mono text-[9px] text-gray-700">{e.ruleId}</td>
                            <td className="border border-gray-200 px-2 py-1 font-semibold text-red-800">{e.label}</td>
                            <td className="border border-gray-200 px-2 py-1 text-gray-700">{e.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  );
                })()}
              </div>
            </div>

            {/* ── SIGNATURE / APPROVAL BLOCK ──────────────────────────────── */}
            <div className="mt-8 mb-4">
              <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-gray-500 mb-3 pb-0.5 border-b border-gray-300">6 · Approval & Sign-off</p>
              <div className="grid grid-cols-3 gap-6 text-[10px] text-gray-600">
                {['Prepared by (AI System)', 'Reviewed by (Procurement Manager)', 'Approved by (Finance / CPO)'].map((label) => (
                  <div key={label}>
                    <p className="font-semibold mb-1">{label}</p>
                    <div className="border-b border-gray-400 mt-6 mb-1" />
                    <p className="text-[9px] text-gray-400">Name &amp; Date</p>
                  </div>
                ))}
              </div>
            </div>

            {/* ── FOOTER ───────────────────────────────────────────────────── */}
            <div className="border-t border-gray-300 pt-2 flex justify-between text-[8px] text-gray-400 mt-2">
              <span>ChainIQ AI Sourcing Platform — Confidential — Not for external distribution</span>
              <span>Generated {new Date(log.timestamp).toLocaleString()} · Request {log.request_id}</span>
            </div>
          </div>
          {/* ── End print audit report ─────────────────────────────────────── */}

          {/* AI Summary + Request Details — side by side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 print:hidden">
            {/* AI Summary */}
            <Card className="border-primary/20 bg-primary/5 dark:bg-primary/10">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Bot className="h-4 w-4 text-red-500" />
                  Decision Summary
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
                        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                        ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-1">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-1">{children}</ol>,
                        li: ({ children }) => <li className="leading-snug">{children}</li>,
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
                <div className="grid grid-cols-2 xl:grid-cols-3 gap-2.5">
                  {[
                    { label: 'Category', value: `${String(snapshot.category_l1 ?? '—')}${snapshot.category_l2 ? ` / ${String(snapshot.category_l2)}` : ''}` },
                    { label: 'Budget', value: snapshot.budget != null ? `${Number(snapshot.budget).toLocaleString()} ${String(snapshot.currency ?? '')}` : '—' },
                    { label: 'Quantity', value: snapshot.quantity != null ? `${String(snapshot.quantity)} ${String(snapshot.amount_unit ?? '')}` : '—' },
                    { label: 'Delivery', value: String(snapshot.delivery_country ?? '—') },
                    { label: 'Lead time', value: snapshot.days_until_required != null ? `${String(snapshot.days_until_required)} days` : '—' },
                    { label: 'Preferred', value: String(snapshot.preferred_supplier_mentioned ?? '—') },
                    { label: 'Incumbent', value: String(snapshot.incumbent_supplier ?? '—') },
                    { label: 'Data residency', value: snapshot.data_residency_constraint ? 'Yes' : 'No' },
                    { label: 'ESG required', value: snapshot.esg_requirement ? 'Yes' : 'No' },
                    ...Object.entries(snapshot)
                      .filter(([key]) => !['category_l1', 'category_l2', 'budget', 'currency', 'quantity', 'amount_unit', 'delivery_country', 'days_until_required', 'preferred_supplier_mentioned', 'incumbent_supplier', 'data_residency_constraint', 'esg_requirement'].includes(key))
                      .map(([key, val]) => ({ label: CONTEXT_LABELS[key] ?? key.replace(/_/g, ' '), value: String(val) }))
                  ].map((item, idx) => (
                    <div key={idx} className="flex flex-col gap-0.5 p-2 rounded-lg bg-muted/40 border border-border/50 transition-colors hover:bg-muted/60 hover:border-border/80">
                      <span className="text-[9px] uppercase font-semibold text-muted-foreground tracking-wider">{item.label}</span>
                      <span className="font-medium text-xs text-foreground break-words">{item.value}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>



          {/* Supplier Ranking — ranked list */}
          <Card className="print:hidden">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-4">
                <CardTitle className="text-base">Supplier Ranking</CardTitle>
                {log.confidence_assessment && (() => {
                  const ca = log.confidence_assessment!;
                  const pct = Math.round(ca.score * 100);
                  const colors: Record<string, { hex: string; text: string; bg: string }> = {
                    high: { hex: '#10b981', text: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800' },
                    medium: { hex: '#f59e0b', text: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800' },
                    low: { hex: '#f97316', text: 'text-orange-600 dark:text-orange-400', bg: 'bg-orange-50 dark:bg-orange-950/40 border-orange-200 dark:border-orange-800' },
                    very_low: { hex: '#ef4444', text: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-950/40 border-red-200 dark:border-red-800' },
                  };
                  const c = colors[ca.label] ?? colors.medium;
                  const r = 10, circ = 2 * Math.PI * r;
                  const offset = circ - (pct / 100) * circ;
                  return (
                    <div className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs ${c.bg}`} title={ca.explanation}>
                      <svg width="28" height="28" viewBox="0 0 28 28" className="-rotate-90 shrink-0">
                        <circle cx="14" cy="14" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-muted/30" />
                        <circle cx="14" cy="14" r={r} fill="none" stroke={c.hex} strokeWidth="2.5" strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" />
                      </svg>
                      <div className="leading-tight">
                        <div className={`font-bold font-mono text-sm ${c.text}`}>{pct}%</div>
                        <div className="text-muted-foreground capitalize" style={{ fontSize: '10px' }}>Confidence</div>
                      </div>
                    </div>
                  );
                })()}
              </div>
              {(() => {
                const minQ = minSupplierQuotes;
                return minQ > 0 ? (
                  <p className="text-xs text-muted-foreground mt-0">
                    {minQ === 1
                      ? 'Policy requires at least 1 quote'
                      : <><span className="font-semibold text-foreground">{minQ} quotes</span> required by policy</>}
                  </p>
                ) : null;
              })()}
            </CardHeader>
            <CardContent className="p-0">
              {evaluatedSuppliers.length === 0 ? (
                <p className="px-6 py-6 text-sm text-muted-foreground">No suppliers passed the category filter for evaluation.</p>
              ) : (
                <div className="w-full overflow-x-auto">
                  <table className="w-full text-xs print:text-[10px] text-left">
                    <thead>
                      <tr className="border-b text-xs print:text-[10px] text-muted-foreground uppercase tracking-wide bg-muted/30">
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 w-6 text-center">#</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 min-w-max">Supplier</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 text-center whitespace-nowrap">Unit Price</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 text-center whitespace-nowrap">Total Cost</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 text-center whitespace-nowrap">Lead Time</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 w-16 print:w-14 text-center" title="Overall rank: 95% cost + 2.5% reputation + 2.5% historic, × compliance">Score</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 w-16 print:w-14 text-center" title="Composite quality, risk and ESG score">Reputation</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 w-16 print:w-14 text-center" title="Policy compliance — 100 = fully compliant, lower = soft violations">Compliance</th>
                        <th className="px-3 print:px-1 py-2.5 print:py-1.5 w-24 text-center print:hidden">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evaluatedSuppliers
                        .slice()
                        .sort((a, b) => (b.final_normalized_rank ?? 0) - (a.final_normalized_rank ?? 0))
                        .map((s, i) => {
                          const rank = i + 1;
                          const m = supplierMetrics(s);
                          const isRequired = rank <= minSupplierQuotes;
                          const isWinner = rank === 1;
                          return (
                            <tr
                              key={`${s.supplier_id}-${s.category_l2}`}
                              className={`border-b last:border-0 transition-colors print:break-inside-avoid ${isRequired
                                ? 'bg-emerald-50/60 dark:bg-emerald-950/20'
                                : 'hover:bg-muted/30'
                                }`}
                            >
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5 text-center">
                                <div className="flex items-center justify-center gap-1">
                                  {isWinner && <Star className="h-3.5 w-3.5 text-emerald-500 fill-emerald-400 shrink-0" />}
                                  <span className={`font-bold ${isWinner ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                                    {rank}
                                  </span>
                                </div>
                              </td>
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="font-medium">{s.supplier_name}</span>
                                  {m.isPreferred && <Badge variant="outline" className="text-[10px] print:text-[8px] text-blue-600 border-blue-200 bg-blue-50 dark:bg-blue-950/40 dark:text-blue-300">Preferred</Badge>}
                                  {m.isIncumbent && <Badge variant="outline" className="text-[10px] print:text-[8px] text-violet-600 border-violet-200 bg-violet-50 dark:bg-violet-950/40 dark:text-violet-300">Incumbent</Badge>}
                                </div>
                                <p className="text-xs print:text-[9px] text-muted-foreground mt-0.5">{s.category_l2}</p>
                              </td>
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5 text-center font-mono text-xs print:text-[10px] whitespace-nowrap">
                                {m.unitPrice != null
                                  ? `${m.currency} ${Number(m.unitPrice).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                                  : <span className="text-muted-foreground">—</span>}
                              </td>
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5 text-center font-mono text-xs print:text-[10px] whitespace-nowrap">
                                {m.totalCost != null
                                  ? `${m.currency} ${Number(m.totalCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                                  : <span className="text-muted-foreground">—</span>}
                              </td>
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5 text-center text-xs print:text-[10px] whitespace-nowrap">
                                {m.leadStd != null
                                  ? <>{String(m.leadStd)}d{m.leadExp != null ? <span className="text-muted-foreground"> / {String(m.leadExp)}d</span> : ''}</>
                                  : <span className="text-muted-foreground">—</span>}
                              </td>
                              {/* Ranking Score: normalized_rank [0,1] → display 0–100 */}
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5">
                                <div className="flex flex-col items-center gap-1">
                                  {scoreBar(s.final_normalized_rank != null ? Math.round(s.final_normalized_rank * 100) : null) ?? <span className="text-xs print:text-[10px] text-muted-foreground">—</span>}
                                </div>
                              </td>
                              {/* Reputation Score: already 0–100 */}
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5">
                                <div className="flex flex-col items-center gap-1">
                                  {scoreBar(m.reputationScore ?? null) ?? <span className="text-xs print:text-[10px] text-muted-foreground">—</span>}
                                </div>
                              </td>
                              {/* Compliance Score: [0,1] → display 0–100 */}
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5">
                                <div className="flex flex-col items-center gap-1">
                                  {scoreBar(m.complianceScore != null ? Math.round(m.complianceScore * 100) : null) ?? <span className="text-xs print:text-[10px] text-muted-foreground">—</span>}
                                </div>
                              </td>
                              <td className="px-3 print:px-1 py-2.5 print:py-1.5 text-center print:hidden">
                                {isRequired ? (
                                  <a
                                    href={`mailto:?subject=${encodeURIComponent(`Quote Request: ${requestTitle || requestId} — ${s.supplier_name}`)}&body=${encodeURIComponent(
                                      `Dear ${s.supplier_name} Team,\n\n` +
                                      `We are reaching out regarding procurement request ${requestId}.\n\n` +
                                      `Request: ${requestTitle || 'N/A'}\n` +
                                      `Category: ${s.category_l2}\n` +
                                      (m.unitPrice != null ? `Indicative Unit Price: ${m.currency} ${Number(m.unitPrice).toLocaleString(undefined, { maximumFractionDigits: 2 })}\n` : '') +
                                      (m.totalCost != null ? `Estimated Total: ${m.currency} ${Number(m.totalCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}\n` : '') +
                                      `\nCould you please provide a formal quote for this request at your earliest convenience?\n\n` +
                                      `Best regards`
                                    )}`}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors no-underline"
                                    title="Open email client to request a quote"
                                  >
                                    <Mail className="h-3 w-3" />
                                    Quote
                                  </a>
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Result Quality Flags */}
          {log.flag_assessment && (log.flag_assessment.flags?.length ?? 0) > 0 && (
            <Card className="print:hidden">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  Result Quality Flags
                  <span className="ml-auto text-xs font-normal text-muted-foreground">
                    {log.flag_assessment.flags!.filter(f => f.severity === 'warning').length > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-950/50 text-amber-700 dark:text-amber-400 font-medium mr-1">
                        ⚠ {log.flag_assessment.flags!.filter(f => f.severity === 'warning').length} warning{log.flag_assessment.flags!.filter(f => f.severity === 'warning').length > 1 ? 's' : ''}
                      </span>
                    )}
                    {log.flag_assessment.flags!.filter(f => f.severity === 'info').length > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-950/50 text-blue-700 dark:text-blue-400 font-medium">
                        ℹ {log.flag_assessment.flags!.filter(f => f.severity === 'info').length} info
                      </span>
                    )}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-xs text-left">
                    <thead>
                      <tr className="border-b text-xs text-muted-foreground uppercase tracking-wide bg-muted/30">
                        <th className="px-3 py-2 w-48">Flag</th>
                        <th className="px-3 py-2 w-24 text-center">Severity</th>
                        <th className="px-3 py-2">Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(() => {
                        const FLAG_LABELS: Record<string, string> = {
                          NO_COMPLIANT_SUPPLIERS: 'No Compliant Suppliers',
                          LOW_RANK_CLUSTER: 'Low Ranking Cluster',
                          INDISTINGUISHABLE_RANKS: 'Indistinguishable Rankings',
                          HIGH_EXCLUSION_RATE: 'High Exclusion Rate',
                          BUDGET_INSUFFICIENT: 'Budget Insufficient',
                          PREFERRED_SUPPLIER_RESTRICTED: 'Preferred Supplier Restricted',
                          PREFERRED_BONUS_DECISIVE: 'Preferred Bonus Was Decisive',
                          QUANTITY_EXCEEDS_TIER_MAXIMUM: 'Quantity Exceeds Tier Maximum',
                        };
                        return log.flag_assessment!.flags!.map((f, i) => (
                          <tr key={i} className={`border-b last:border-0 ${f.severity === 'warning' ? 'bg-amber-50/60 dark:bg-amber-950/10' : ''}`}>
                            <td className="px-3 py-2.5 font-medium text-foreground">{FLAG_LABELS[f.flag_id] ?? f.flag_id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</td>
                            <td className="px-3 py-2.5 text-center">
                              {f.severity === 'warning' ? (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 dark:bg-amber-950/50 text-amber-700 dark:text-amber-400">Warning</span>
                              ) : (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 dark:bg-blue-950/50 text-blue-700 dark:text-blue-400">Info</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-muted-foreground leading-relaxed">{f.description}</td>
                          </tr>
                        ));
                      })()}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Additional Details — collapsible section */}
          <div className="relative print:hidden">
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
            <div className="space-y-6 print:hidden">
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
                    <EscalationMap assessment={log.escalation_assessment} />
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
                            const rank = s.final_normalized_rank != null
                              ? `#${evaluatedSuppliers.filter(x => x.final_normalized_rank != null).sort((a, b) => (b.final_normalized_rank ?? 0) - (a.final_normalized_rank ?? 0)).findIndex(x => x.supplier_id === s.supplier_id && x.category_l2 === s.category_l2) + 1}`
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
                            {(Object.keys(s.pricing_resolved).length > 0 || s.final_normalized_rank != null) && (
                              <div className="px-4 py-3 border-b flex flex-wrap gap-4 text-xs">
                                {Object.entries(s.pricing_resolved).map(([k, v]) => (
                                  <div key={k}>
                                    <span className="text-muted-foreground">{k.replace(/_/g, ' ')}: </span>
                                    <span className="font-mono font-medium">{String(v)}</span>
                                  </div>
                                ))}
                                {s.final_normalized_rank != null && (
                                  <>
                                    <div>
                                      <span className="text-muted-foreground">cost rank score: </span>
                                      <span className="font-mono font-medium">{s.final_normalized_rank.toFixed(4)}</span>
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
        </div>
      </div>

      {/* Ask AI Sidebar */}
      <AiChatSidebar
        open={chatOpen}
        onOpenChange={setChatOpen}
        requestId={requestId}
        executionLog={log}
      />

      <style jsx global>{`
        @page {
          size: A4 portrait;
          margin: 12mm;
        }

        @media print {
          html, body {
            print-color-adjust: exact;
            -webkit-print-color-adjust: exact;
          }
        }
      `}</style>
    </div>
  );
}
