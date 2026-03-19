'use client';

import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { FileText, Anchor, Truck, AlertTriangle, ShieldAlert, ClipboardList, Ban, Send, CheckCircle, ThumbsUp, ThumbsDown } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { toast } from 'sonner';
import { useAuth } from '@/context/auth-context';
import { ActionDialog } from '@/components/ui/action-dialog';

type DialogAction = 'approve' | 'reject' | 'review' | null;

const MOCK_OUTPUT = {
  "request_id": "REQ-000004",
  "processed_at": "2026-03-14T18:02:11Z",
  "request_interpretation": {
    "category_l1": "IT",
    "category_l2": "Docking Stations",
    "quantity": 240,
    "unit_of_measure": "device",
    "budget_amount": 25199.55,
    "currency": "EUR",
    "delivery_country": "DE",
    "required_by_date": "2026-03-20",
    "days_until_required": 6,
    "data_residency_required": false,
    "esg_requirement": false,
    "preferred_supplier_stated": "Dell Enterprise Europe",
    "incumbent_supplier": "Bechtle Workplace Solutions",
    "requester_instruction": "no exception — single supplier only"
  },
  "validation": {
    "completeness": "pass",
    "issues_detected": [
      {
        "issue_id": "V-001",
        "severity": "critical",
        "type": "budget_insufficient",
        "description": "Budget of EUR 25,199.55 cannot cover 240 units at any compliant supplier's standard pricing. Lowest available unit price is EUR 148.80 (Bechtle, tier 100–499), yielding a minimum total of EUR 35,712.00 — EUR 10,512.45 over budget.",
        "action_required": "Requester must either increase budget to at least EUR 35,712 or reduce quantity to a maximum of 169 units within the stated budget."
      },
      {
        "issue_id": "V-002",
        "severity": "high",
        "type": "policy_conflict",
        "description": "Requester instruction 'no exception' conflicts with AT-002: a contract value above EUR 25,000 requires at least 2 supplier quotes. All compliant pricing options for 240 units exceed EUR 35,712, placing this firmly in the AT-002 tier. The requester cannot waive this requirement unilaterally.",
        "action_required": "Procurement policy AT-002 must be applied. Two quotes are required and a deviation requires Procurement Manager approval."
      },
      {
        "issue_id": "V-003",
        "severity": "high",
        "type": "lead_time_infeasible",
        "description": "Required delivery date 2026-03-20 is 6 days from request creation (2026-03-14). All suppliers' standard lead times for Docking Stations exceed 20 days. Even expedited lead times (17–19 days) do not meet the 6-day window.",
        "action_required": "Requester must confirm whether the delivery date is a hard constraint. If so, no compliant supplier can meet it and an escalation is required."
      }
    ]
  },
  "policy_evaluation": {
    "approval_threshold": {
      "rule_applied": "AT-002",
      "basis": "All valid pricing options place total contract value between EUR 35,712 and EUR 37,200 — above the EUR 25,000 AT-002 threshold.",
      "quotes_required": 2,
      "approvers": ["business", "procurement"],
      "deviation_approval": "Procurement Manager",
      "note": "Stated budget of EUR 25,199.55 falls just above the AT-001 ceiling (EUR 24,999.99), so requester may have believed this was a single-quote scenario. However, stated budget cannot cover the required quantity. The actual procurement value falls in AT-002 regardless."
    },
    "preferred_supplier": {
      "supplier": "Dell Enterprise Europe",
      "status": "eligible",
      "is_preferred": true,
      "covers_delivery_country": true,
      "is_restricted": false,
      "policy_note": "Dell is a preferred supplier for Docking Stations in DE. Preferred status means Dell should be included in the comparison — it does not mandate single-source selection, particularly where AT-002 requires 2 quotes."
    },
    "restricted_suppliers": {
      "SUP-0008_Computacenter_Devices": {
        "restricted": false,
        "note": "Computacenter is not restricted for Docking Stations. However, preferred=False and risk_score=34 (highest of eligible suppliers) — excluded from shortlist on risk grounds."
      }
    }
  },
  "supplier_shortlist": [
    {
      "rank": 1,
      "supplier_id": "SUP-0007",
      "supplier_name": "Bechtle Workplace Solutions",
      "preferred": true,
      "incumbent": true,
      "pricing_tier_applied": "100–499 units",
      "unit_price_eur": 148.80,
      "total_price_eur": 35712.00,
      "standard_lead_time_days": 26,
      "expedited_lead_time_days": 18,
      "expedited_unit_price_eur": 160.70,
      "expedited_total_eur": 38568.00,
      "quality_score": 82,
      "risk_score": 19,
      "esg_score": 72,
      "policy_compliant": true,
      "covers_delivery_country": true,
      "recommendation_note": "Lowest total price at EUR 35,712. Incumbent supplier with established DE delivery capability. Preferred status confirmed. Both standard (26d) and expedited (18d) lead times exceed the 6-day requirement — see escalation."
    },
    {
      "rank": 2,
      "supplier_id": "SUP-0001",
      "supplier_name": "Dell Enterprise Europe",
      "preferred": true,
      "incumbent": false,
      "pricing_tier_applied": "100–499 units",
      "unit_price_eur": 155.00,
      "total_price_eur": 37200.00,
      "standard_lead_time_days": 22,
      "expedited_lead_time_days": 17,
      "expedited_unit_price_eur": 167.40,
      "expedited_total_eur": 40176.00,
      "quality_score": 85,
      "risk_score": 15,
      "esg_score": 73,
      "policy_compliant": true,
      "covers_delivery_country": true,
      "recommendation_note": "Requester's preferred supplier. Highest quality score (85) and lowest risk score (15) of eligible options. Total price EUR 37,200 — EUR 1,488 above Bechtle. Standard lead time 22d also infeasible; expedited 17d also infeasible for 6-day requirement."
    },
    {
      "rank": 3,
      "supplier_id": "SUP-0002",
      "supplier_name": "HP Enterprise Devices",
      "preferred": true,
      "incumbent": false,
      "pricing_tier_applied": "100–499 units",
      "unit_price_eur": 153.45,
      "total_price_eur": 36828.00,
      "standard_lead_time_days": 23,
      "expedited_lead_time_days": 19,
      "expedited_unit_price_eur": 165.73,
      "expedited_total_eur": 39775.20,
      "quality_score": 83,
      "risk_score": 26,
      "esg_score": 66,
      "policy_compliant": true,
      "covers_delivery_country": true,
      "recommendation_note": "Mid-range price at EUR 36,828. Higher risk score (26) than Bechtle and Dell. Expedited lead time (19d) does not meet the 6-day requirement."
    }
  ],
  "suppliers_excluded": [
    {
      "supplier_id": "SUP-0008",
      "supplier_name": "Computacenter Devices",
      "reason": "preferred=False, risk_score=34 (highest of eligible set). Not policy-restricted for this category/country combination, but excluded from shortlist on risk grounds."
    }
  ],
  "escalations": [
    {
      "escalation_id": "ESC-001",
      "rule": "ER-001",
      "trigger": "Budget is insufficient to fulfil the stated quantity at any compliant supplier price. Requester must confirm revised budget or reduced quantity before sourcing can proceed.",
      "escalate_to": "Requester Clarification",
      "blocking": true
    },
    {
      "escalation_id": "ESC-002",
      "rule": "AT-002",
      "trigger": "Policy conflict: requester instruction 'no exception' cannot override AT-002. All valid contract values exceed EUR 25,000, requiring 2 quotes and Procurement Manager approval for any deviation.",
      "escalate_to": "Procurement Manager",
      "blocking": true
    },
    {
      "escalation_id": "ESC-003",
      "rule": "ER-004",
      "trigger": "Lead time infeasible: required delivery 2026-03-20 (6 days). All suppliers' expedited lead times are 17–19 days. No compliant supplier can meet the stated deadline.",
      "escalate_to": "Head of Category",
      "blocking": true
    }
  ],
  "recommendation": {
    "status": "cannot_proceed",
    "reason": "Three blocking issues prevent autonomous award: insufficient budget, policy conflict with requester's single-supplier instruction, and infeasible delivery timeline. All three require human resolution before sourcing can continue.",
    "preferred_supplier_if_resolved": "Bechtle Workplace Solutions",
    "preferred_supplier_rationale": "Bechtle is the incumbent and lowest-cost option at EUR 35,712. Dell (requester preference) is a valid alternative at EUR 37,200 with a higher quality score — both should be included in the compliant shortlist once budget is confirmed.",
    "minimum_budget_required": 35712.00,
    "minimum_budget_currency": "EUR"
  },
  "audit_trail": {
    "policies_checked": ["AT-001", "AT-002", "CR-001", "ER-001", "ER-004"],
    "supplier_ids_evaluated": ["SUP-0001", "SUP-0002", "SUP-0007", "SUP-0008"],
    "pricing_tiers_applied": "100–499 units (EU region, EUR currency)",
    "data_sources_used": ["requests.json", "suppliers.csv", "pricing.csv", "policies.json"],
    "historical_awards_consulted": true,
    "historical_award_note": "AWD-000009 through AWD-000011 show this request was previously awarded to Dell (rank 1, EUR 37,200) with Bechtle and HP as alternatives. Escalation was required (Head of Category). Prior decision used for pattern context only."
  }
};

function getSeverityColor(severity: string) {
  switch (severity) {
    case 'critical': return 'text-red-700 border-red-300 bg-red-50';
    case 'high': return 'text-orange-700 border-orange-300 bg-orange-50';
    case 'medium': return 'text-yellow-700 border-yellow-300 bg-yellow-50';
    default: return 'text-gray-700 border-gray-300 bg-gray-50';
  }
}

// Map from mock output "escalate_to" labels to backend escalation types
const ESCALATION_TYPE_MAP: Record<string, string> = {
  'Requester Clarification': 'requester_clarification',
  'Procurement Manager': 'procurement_manager',
  'Head of Category': 'category_head',
  'Compliance': 'compliance',
};

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface MyEscalation { id: string; request_id: string; type: string; status: string; }
interface AuditEntry { id: string; actor_id: string; action: string; notes: string | null; created_at: string; }

const AUDIT_ACTION_COLORS: Record<string, string> = {
  submitted: 'bg-blue-500',
  approved: 'bg-emerald-500',
  rejected: 'bg-red-500',
  reviewed: 'bg-indigo-500',
  escalated: 'bg-orange-500',
  clarified: 'bg-amber-500',
  withdrawn: 'bg-gray-400',
};

export default function AnalysisPage() {
  const [loading, setLoading] = useState(true);
  const [sentEscalations, setSentEscalations] = useState<Record<string, boolean>>({});
  const [myEscalation, setMyEscalation] = useState<MyEscalation | null>(null);
  const [dialogAction, setDialogAction] = useState<DialogAction>(null);
  const [auditTrail, setAuditTrail] = useState<AuditEntry[]>([]);
  const searchParams = useSearchParams();
  const requestId = searchParams.get('id');
  const { user } = useAuth();
  const isReviewer = user && user.role !== 'requester';

  useEffect(() => { setLoading(false); }, []);

  useEffect(() => {
    if (!requestId || !isReviewer) return;
    fetch(`${API}/escalations/me`, { credentials: 'include' })
      .then((r) => r.json())
      .then((data: MyEscalation[]) => {
        const match = data.find((e) => e.request_id === requestId && e.status === 'pending');
        setMyEscalation(match ?? null);
      })
      .catch(() => {});
  }, [requestId, isReviewer]);

  useEffect(() => {
    if (!requestId) return;
    fetch(`${API}/requests/${requestId}/audit`, { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => Array.isArray(data) && setAuditTrail(data))
      .catch(() => {});
  }, [requestId]);

  const handleConfirm = async (notes: string) => {
    if (!requestId || !dialogAction) return;
    const action = dialogAction;
    setDialogAction(null);
    try {
      const endpoint = action === 'review' ? 'review' : action;
      const res = await fetch(`${API}/requests/${requestId}/${endpoint}`, {
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
      setMyEscalation(null);
    } catch {
      toast.error('Action failed');
    }
  };

  const sendEscalation = async (escalateTo: string, trigger: string, rule: string) => {
    if (!requestId) {
      toast.error('No request ID — open this page from the cases list.');
      return;
    }
    const type = ESCALATION_TYPE_MAP[escalateTo];
    if (!type) {
      toast.error(`Unknown escalation target: ${escalateTo}`);
      return;
    }
    try {
      const res = await fetch(`${API}/escalations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          request_id: requestId,
          type,
          message: `[${rule}] ${trigger}`,
        }),
      });
      if (!res.ok) throw new Error('Failed');
      setSentEscalations((prev) => ({ ...prev, [escalateTo]: true }));
      toast.success(`Escalation sent to ${escalateTo}`);
    } catch {
      toast.error(`Failed to send escalation to ${escalateTo}`);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[80vh] w-full flex-col items-center justify-center space-y-4">
        <div className="h-12 w-12 animate-spin rounded-full border-b-2 border-primary"></div>
        <p className="text-muted-foreground animate-pulse">Running Procurement Rules Audit...</p>
      </div>
    );
  }

  const { validation, escalations, recommendation, request_interpretation, supplier_shortlist, suppliers_excluded, policy_evaluation, audit_trail } = MOCK_OUTPUT;

  return (
    <div className="py-6 space-y-6 max-w-6xl mx-auto">
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analysis Report for {MOCK_OUTPUT.request_id}</h1>
          <p className="text-muted-foreground mt-1">Processed at: {new Date(MOCK_OUTPUT.processed_at).toLocaleString()}</p>
        </div>
        <Badge variant={recommendation.status === 'cannot_proceed' ? 'destructive' : 'default'} className="text-sm px-3 py-1 uppercase">
          {recommendation.status.replace('_', ' ')}
        </Badge>
      </div>

      {/* Action panel for reviewers with a pending escalation on this request */}
      {myEscalation && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="flex-1">
            <p className="font-semibold text-amber-800">Action required — this request is assigned to you for review.</p>
            <p className="text-sm text-amber-700 mt-0.5">Approve, reject, or mark as reviewed without changing the request status.</p>
          </div>
          <div className="flex gap-2 shrink-0">
            <Button size="sm" className="gap-1 bg-emerald-600 hover:bg-emerald-700" onClick={() => setDialogAction('approve')}>
              <ThumbsUp className="h-3 w-3" /> Approve
            </Button>
            <Button size="sm" variant="destructive" className="gap-1" onClick={() => setDialogAction('reject')}>
              <ThumbsDown className="h-3 w-3" /> Reject
            </Button>
            <Button size="sm" variant="outline" className="gap-1" onClick={() => setDialogAction('review')}>
              <CheckCircle className="h-3 w-3" /> Mark Reviewed
            </Button>
          </div>
        </div>
      )}

      {recommendation.status === 'cannot_proceed' && (
        <div className="bg-destructive/10 border-l-4 border-destructive p-4 rounded-r-lg flex gap-4 items-start">
          <ShieldAlert className="text-destructive h-6 w-6 shrink-0 mt-0.5" />
          <div>
            <h3 className="text-destructive font-semibold">Recommendation: Cannot Proceed</h3>
            <p className="text-sm text-destructive mt-1">{recommendation.reason}</p>
            <div className="mt-3 grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-destructive/70 text-xs uppercase">Preferred Supplier (if resolved)</span>
                <p className="font-medium text-destructive">{recommendation.preferred_supplier_if_resolved}</p>
              </div>
              <div>
                <span className="text-destructive/70 text-xs uppercase">Minimum Budget Required</span>
                <p className="font-mono font-medium text-destructive">{recommendation.minimum_budget_currency} {recommendation.minimum_budget_required.toLocaleString()}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Left Column */}
        <div className="md:col-span-2 space-y-6">
          
          {/* Validation Issues */}
          <Card className="border-red-200">
            <CardHeader className="bg-red-50/50 dark:bg-red-950/20 border-b border-red-100 dark:border-red-900/30">
              <CardTitle className="text-red-700 dark:text-red-400 flex items-center gap-2">
                <AlertTriangle className="h-5 w-5" />
                Rule Violations Detected ({validation.issues_detected.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-6 space-y-4">
              {validation.issues_detected.map((issue) => (
                <div key={issue.issue_id} className="border rounded-lg p-4 bg-background">
                  <div className="flex justify-between items-start mb-2">
                    <h4 className="font-semibold text-foreground flex items-center gap-2">
                      <Badge variant="outline" className={getSeverityColor(issue.severity)}>{issue.severity.toUpperCase()}</Badge>
                      {issue.type.replace(/_/g, ' ').toUpperCase()}
                    </h4>
                    <span className="text-xs text-muted-foreground font-mono">{issue.issue_id}</span>
                  </div>
                  <p className="text-sm my-3">{issue.description}</p>
                  <div className="bg-muted p-2 rounded text-sm border-l-2 border-primary">
                    <span className="font-semibold mr-2">Action Required:</span>
                    {issue.action_required}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Supplier Shortlist */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Truck className="h-5 w-5 text-primary" />
                Supplier Shortlist
              </CardTitle>
              <CardDescription>Ranked by total price — all suppliers cover delivery to {request_interpretation.delivery_country}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="text-xs text-muted-foreground uppercase bg-muted/50 border-b">
                    <tr>
                      <th className="px-4 py-3">Rank</th>
                      <th className="px-4 py-3">Supplier</th>
                      <th className="px-4 py-3 text-right">Unit Price</th>
                      <th className="px-4 py-3 text-right">Total</th>
                      <th className="px-4 py-3 text-center">Lead Time</th>
                      <th className="px-4 py-3 text-center">Quality</th>
                      <th className="px-4 py-3 text-center">Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {supplier_shortlist.map((supplier) => (
                      <tr key={supplier.rank} className="border-b last:border-0 hover:bg-muted/30">
                        <td className="px-4 py-3 font-semibold">#{supplier.rank}</td>
                        <td className="px-4 py-3">
                          <div className="font-medium flex items-center gap-2">
                            {supplier.supplier_name}
                            {supplier.preferred && <Badge variant="outline" className="text-[10px] text-blue-600 border-blue-200 bg-blue-50">Preferred</Badge>}
                            {supplier.incumbent && <Badge variant="outline" className="text-[10px] text-green-600 border-green-200 bg-green-50">Incumbent</Badge>}
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 max-w-[300px]" title={supplier.recommendation_note}>
                            {supplier.recommendation_note}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right font-mono">€{supplier.unit_price_eur.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right font-mono font-medium">€{supplier.total_price_eur.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                        <td className="px-4 py-3 text-center">
                          <div>{supplier.standard_lead_time_days}d</div>
                          <div className="text-xs text-muted-foreground">exp: {supplier.expedited_lead_time_days}d</div>
                        </td>
                        <td className="px-4 py-3 text-center font-mono">{supplier.quality_score}</td>
                        <td className="px-4 py-3 text-center font-mono">{supplier.risk_score}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Excluded Suppliers */}
          {suppliers_excluded.length > 0 && (
            <Card className="border-gray-200">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <Ban className="h-5 w-5 text-muted-foreground" />
                  Excluded Suppliers
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {suppliers_excluded.map((sup) => (
                  <div key={sup.supplier_id} className="flex items-start gap-3 text-sm border-b pb-3 last:border-0 last:pb-0">
                    <Badge variant="outline" className="shrink-0 font-mono text-xs">{sup.supplier_id}</Badge>
                    <div>
                      <p className="font-medium">{sup.supplier_name}</p>
                      <p className="text-muted-foreground mt-1">{sup.reason}</p>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* Audit Trail */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                <ClipboardList className="h-5 w-5 text-primary" />
                Audit Trail
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground uppercase mb-1">Policies Checked</p>
                  <div className="flex flex-wrap gap-1">
                    {audit_trail.policies_checked.map((p) => (
                      <Badge key={p} variant="secondary" className="text-xs">{p}</Badge>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground uppercase mb-1">Suppliers Evaluated</p>
                  <div className="flex flex-wrap gap-1">
                    {audit_trail.supplier_ids_evaluated.map((s) => (
                      <Badge key={s} variant="secondary" className="text-xs">{s}</Badge>
                    ))}
                  </div>
                </div>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase mb-1">Pricing Tiers Applied</p>
                <p>{audit_trail.pricing_tiers_applied}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase mb-1">Data Sources</p>
                <div className="flex flex-wrap gap-1">
                  {audit_trail.data_sources_used.map((d) => (
                    <Badge key={d} variant="outline" className="text-xs font-mono">{d}</Badge>
                  ))}
                </div>
              </div>
              {audit_trail.historical_awards_consulted && (
                <div className="bg-muted p-3 rounded text-sm">
                  <p className="font-semibold mb-1">Historical Awards</p>
                  <p className="text-muted-foreground">{audit_trail.historical_award_note}</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Request Audit Timeline */}
          {auditTrail.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <ClipboardList className="h-5 w-5 text-violet-500" />
                  Request Timeline
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="relative border-l border-border ml-3 space-y-4">
                  {auditTrail.map((entry, i) => (
                    <li key={entry.id} className="ml-4">
                      <span className={`absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-background ${AUDIT_ACTION_COLORS[entry.action] ?? 'bg-gray-400'}`} />
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="text-sm font-medium capitalize">{entry.action}</p>
                        <time className="text-xs text-muted-foreground whitespace-nowrap">
                          {new Date(entry.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </time>
                      </div>
                      {entry.notes && (
                        <p className="text-xs text-muted-foreground mt-1 italic">"{entry.notes}"</p>
                      )}
                      {i === auditTrail.length - 1 && (
                        <p className="text-xs text-muted-foreground mt-0.5">Latest action</p>
                      )}
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )}

        </div>

        {/* Right Column */}
        <div className="space-y-6">
          
          {/* Request Interpretation */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                <FileText className="h-5 w-5 text-blue-500" />
                Request Summary
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <p className="text-xs text-muted-foreground uppercase">Category</p>
                <p className="font-medium">{request_interpretation.category_l1} / {request_interpretation.category_l2}</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Budget</p>
                  <p className="font-mono">{request_interpretation.currency} {request_interpretation.budget_amount.toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Quantity</p>
                  <p className="font-mono">{request_interpretation.quantity} {request_interpretation.unit_of_measure}s</p>
                </div>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase">Target Date</p>
                <p className="font-medium">{request_interpretation.required_by_date} <span className="text-red-500 text-xs text-semibold">({request_interpretation.days_until_required} days)</span></p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase">Preferred Supplier</p>
                <p className="font-medium">{request_interpretation.preferred_supplier_stated}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase">Incumbent Supplier</p>
                <p className="font-medium">{request_interpretation.incumbent_supplier}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase">Requester Instruction</p>
                <p className="text-sm italic border-l-2 pl-2 text-muted-foreground my-1">"{request_interpretation.requester_instruction}"</p>
              </div>
            </CardContent>
          </Card>

          {/* Policy Evaluation */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                <ShieldAlert className="h-5 w-5 text-amber-500" />
                Policy Evaluation
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div>
                <p className="text-xs text-muted-foreground uppercase mb-1">Approval Threshold</p>
                <Badge variant="outline" className="font-mono">{policy_evaluation.approval_threshold.rule_applied}</Badge>
                <p className="mt-2 text-muted-foreground">{policy_evaluation.approval_threshold.basis}</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Quotes Required</p>
                  <p className="font-mono font-medium">{policy_evaluation.approval_threshold.quotes_required}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground uppercase">Deviation Approval</p>
                  <p className="font-medium">{policy_evaluation.approval_threshold.deviation_approval}</p>
                </div>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase mb-1">Approvers</p>
                <div className="flex gap-1">
                  {policy_evaluation.approval_threshold.approvers.map((a) => (
                    <Badge key={a} variant="secondary" className="capitalize text-xs">{a}</Badge>
                  ))}
                </div>
              </div>
              {policy_evaluation.approval_threshold.note && (
                <p className="text-xs text-muted-foreground bg-muted p-2 rounded">{policy_evaluation.approval_threshold.note}</p>
              )}
            </CardContent>
          </Card>

          {/* Escalations */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                <Anchor className="h-5 w-5 text-orange-500" />
                Required Escalations
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {escalations.map((esc) => {
                const sent = sentEscalations[esc.escalate_to];
                return (
                  <div key={esc.escalation_id} className="text-sm border-b pb-3 last:border-0 last:pb-0">
                    <div className="flex justify-between mb-1">
                      <span className="font-semibold">{esc.rule}</span>
                      <Badge variant={esc.blocking ? 'secondary' : 'outline'} className="text-[10px]">
                        {esc.blocking ? 'BLOCKING' : 'INFO'}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground mb-2">{esc.trigger}</p>
                    <div className="flex items-center justify-between">
                      <p className="font-medium text-xs">Escalate to: <span className="text-primary">{esc.escalate_to}</span></p>
                      {sent ? (
                        <span className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
                          <CheckCircle className="h-3 w-3" /> Sent
                        </span>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-xs gap-1"
                          onClick={() => sendEscalation(esc.escalate_to, esc.trigger, esc.rule)}
                        >
                          <Send className="h-3 w-3" />
                          Send
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

        </div>
      </div>

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
        description="This will mark the request as reviewed without approving or rejecting it."
        confirmLabel="Mark Reviewed"
        onConfirm={handleConfirm}
      />
    </div>
  );
}
