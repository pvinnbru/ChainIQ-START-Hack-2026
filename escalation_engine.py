"""
escalation_engine.py — Rank-sensitivity-based escalation assessment.

Escalation decisions are driven by a single metric: how much would the final
supplier ranking change if an uncertain factor resolved differently?  This is
called the Rank Impact Delta (RID).

Three trigger types are currently handled:

1. missing_field
   A fix_in field is absent/None at evaluation time.  Impact is estimated from
   a pre-built static dependency map: for each fix_in key, what is the maximum
   rank-relevant output that any action gated on that key writes to?  This map
   is built once at store-build time from the sorted action list — zero extra
   LLM calls at request evaluation time.

2. min_quotes_gap
   The required number of supplier quotes (min_supplier_quotes) forces inclusion
   of a supplier whose normalized_rank is significantly lower than the one above
   it.  Impact = rank gap at the required boundary.  Escalation means "consider
   a policy deviation for this specific request".

3. insufficient_suppliers
   Fewer surviving suppliers exist than required by policy.  Always blocking.

Severity levels:
  blocking  — rank_impact >= BLOCKING_THRESHOLD  → must escalate
  advisory  — rank_impact >= ADVISORY_THRESHOLD  → recommend escalation
  (below ADVISORY_THRESHOLD the trigger is still logged but not escalated)

The escalation_rules store (list[dict] with keys rule_id, trigger_condition,
escalate_to, applies_when) is used purely as a routing table: given a trigger
type, find who to escalate to.  The matching is keyword-based over
trigger_condition strings so no extra LLM call is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sort_actions import _get_write

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

BLOCKING_THRESHOLD:    float = 0.70   # force escalation
ADVISORY_THRESHOLD:    float = 0.25   # recommend escalation
MIN_QUOTES_GAP:        float = 0.20   # rank gap that makes extra quote ceremonial

# ---------------------------------------------------------------------------
# Context modifiers
# ---------------------------------------------------------------------------

# days_until_required: if this many days or fewer remain, there is no time to
# collect additional information, so missing_field and min_quotes_gap triggers
# are suppressed (downgraded to "logged").  insufficient_suppliers is never
# suppressed — it is a structural problem regardless of urgency.
URGENT_DAYS_THRESHOLD: int = 1

# Budget scaling: higher budget → lower effective thresholds → escalation
# fires on smaller rank impacts.  Computed as a multiplier on both thresholds:
#   multiplier = clamp((REFERENCE_BUDGET / budget)^BUDGET_SCALE_EXPONENT,
#                      MIN_THRESHOLD_MULTIPLIER, MAX_THRESHOLD_MULTIPLIER)
# At REFERENCE_BUDGET the multiplier is 1.0 (no adjustment).
REFERENCE_BUDGET:          float = 25_000.0  # CHF — mid-range procurement
BUDGET_SCALE_EXPONENT:     float = 0.5       # sqrt scaling: 4× budget → 0.5× thresholds
MIN_THRESHOLD_MULTIPLIER:  float = 0.25      # floor: never below 25 % of base threshold
MAX_THRESHOLD_MULTIPLIER:  float = 3.00      # ceiling: never above 3× base threshold

# Impact of writing to a specific output key on the final normalized_rank.
# Keys not listed are looked up by prefix pattern below.
_DIRECT_IMPACT: dict[str, float] = {
    "excluded":           1.0,
    "rank":               1.0,
    "cost_total":         0.90,
    "compliance_score":   0.80,
    "cost_rank_score":    0.70,
    "reputation_score":   0.30,
    "min_supplier_quotes": 0.50,
}

_PREFIX_IMPACT: list[tuple[str, float]] = [
    ("escalate_to_", 0.40),
    ("requires_",    0.20),
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EscalationTrigger:
    """A single evaluated escalation condition."""
    trigger_id:        str
    trigger_type:      str    # "missing_field" | "min_quotes_gap" | "insufficient_suppliers"
    severity:          str    # "blocking" | "advisory" | "logged"
    rank_impact:       float  # 0.0 – 1.0
    description:       str
    escalate_to:       str | None  # role / team from escalation_rules store
    details:           dict = field(default_factory=dict)
    suppression_reason: str | None = None  # set when urgency or context suppresses this trigger


@dataclass
class EscalationAssessment:
    """All escalation triggers for a single request evaluation."""
    triggers:          list[EscalationTrigger] = field(default_factory=list)
    should_escalate:   bool = False   # True if any blocking or advisory trigger exists
    has_blocking:      bool = False
    has_advisory:      bool = False
    context_notes:     list[str] = field(default_factory=list)  # human-readable modifier log


# ---------------------------------------------------------------------------
# Static dependency map  (built at store-build time, not per request)
# ---------------------------------------------------------------------------

def _output_impact(key: str | None) -> float:
    """Return the rank-impact score for an action writing to *key*."""
    if key is None:
        return 0.0
    if key in _DIRECT_IMPACT:
        return _DIRECT_IMPACT[key]
    for prefix, impact in _PREFIX_IMPACT:
        if key.startswith(prefix):
            return impact
    return 0.15  # unknown free key — minimal but non-zero


def _when_fix_in_refs(action: tuple, fix_in_keys: set[str]) -> set[str]:
    """
    Return the subset of fix_in keys that appear in this action's WHEN condition
    or as direct input parameters.

    Unlike sort_actions._get_reads, this intentionally INCLUDES fix_in keys
    because we need to know which actions break when a fix_in field is missing.
    """
    refs: set[str] = set()

    in1 = action[1] if len(action) > 1 else "_"
    in2 = action[2] if len(action) > 2 else "_"
    typ = action[0] if action else ""

    if in1 != "_" and in1 in fix_in_keys:
        refs.add(in1)
    if typ != "ALI" and in2 != "_" and in2 in fix_in_keys:
        refs.add(in2)

    # WHEN condition tokens
    if len(action) > 5:
        when_str = str(action[5])
        cleaned = re.sub(r'[>=<!+\-*/(),"\']+', " ", when_str)
        _KEYWORDS = {"AND", "OR", "XOR", "NOT", "WHEN", "True", "False", "true", "false"}
        for token in cleaned.split():
            if token in _KEYWORDS:
                continue
            try:
                float(token)
                continue
            except ValueError:
                pass
            if token in fix_in_keys:
                refs.add(token)

    return refs


def build_field_impact_map(
    actions: list[tuple],
    fix_in_keys: set[str],
) -> dict[str, float]:
    """
    Pre-compute the maximum rank impact for each fix_in field.

    For every action that references a fix_in key (in parameters or WHEN), the
    rank impact of the action's output key is recorded.  The map stores the
    maximum across all actions for each field.

    Call this once when building the action store; pass the result into
    evaluate_escalations() at request time.

    Returns: dict[fix_in_key → max_impact (0.0–1.0)]
    """
    impact_map: dict[str, float] = {}

    for action in actions:
        output_key = _get_write(action)
        action_impact = _output_impact(output_key)
        if action_impact == 0.0:
            continue

        for fx_key in _when_fix_in_refs(action, fix_in_keys):
            current = impact_map.get(fx_key, 0.0)
            impact_map[fx_key] = max(current, action_impact)

    return impact_map


# ---------------------------------------------------------------------------
# Escalation rule routing
# ---------------------------------------------------------------------------

# Maps trigger types to keyword signatures in escalation rule trigger_conditions.
# Checked in order; first match wins.
_TRIGGER_KEYWORDS: list[tuple[str, list[str]]] = [
    ("missing_field",          ["missing", "absent", "required", "incomplete"]),
    ("insufficient_suppliers", ["no compliant", "no supplier", "insufficient supplier"]),
    ("min_quotes_gap",         ["deviation", "policy deviation", "quotes", "minimum supplier"]),
]


def _find_escalation_target(
    trigger_type: str,
    escalation_rules: list[dict],
) -> str | None:
    """
    Return the escalate_to value for the escalation rule most semantically
    matched to the given trigger_type, using keyword matching over
    trigger_condition strings.  Returns None if no match found.
    """
    keywords = next(
        (kw for tt, kw in _TRIGGER_KEYWORDS if tt == trigger_type),
        [],
    )
    if not keywords:
        return None

    for rule in escalation_rules:
        condition = rule.get("trigger_condition", "").lower()
        if any(kw in condition for kw in keywords):
            return rule.get("escalate_to")

    return None


# ---------------------------------------------------------------------------
# Individual trigger assessors
# ---------------------------------------------------------------------------

def assess_missing_fields(
    request: dict[str, Any],
    fix_in_keys: set[str],
    field_impact_map: dict[str, float],
    escalation_rules: list[dict],
) -> list[EscalationTrigger]:
    """
    Identify fix_in fields that are None/empty in the request and assess how
    much their absence could change the final ranking.
    """
    escalate_to = _find_escalation_target("missing_field", escalation_rules)
    triggers: list[EscalationTrigger] = []

    for field_name in sorted(fix_in_keys):  # sorted for deterministic output
        value = request.get(field_name)
        is_missing = (
            value is None
            or value == ""
            or (isinstance(value, list) and len(value) == 0)
        )
        if not is_missing:
            continue

        impact = field_impact_map.get(field_name, 0.0)
        if impact < 0.15:
            # Negligible — this field has no meaningful action dependencies
            continue

        severity = (
            "blocking" if impact >= BLOCKING_THRESHOLD
            else "advisory" if impact >= ADVISORY_THRESHOLD
            else "logged"
        )
        triggers.append(EscalationTrigger(
            trigger_id=f"MISSING_{field_name.upper()}",
            trigger_type="missing_field",
            severity=severity,
            rank_impact=impact,
            description=(
                f"Required field '{field_name}' is absent. Actions whose WHEN conditions "
                f"reference this field may produce incorrect results (estimated rank impact: "
                f"{impact:.2f})."
            ),
            escalate_to=escalate_to if severity in ("blocking", "advisory") else None,
            details={"field": field_name, "estimated_rank_impact": impact},
        ))

    return triggers


def assess_min_quotes_gap(
    supplier_results: list[tuple],
    min_supplier_quotes: int,
    escalation_rules: list[dict],
) -> list[EscalationTrigger]:
    """
    Check whether the min_supplier_quotes policy can be fulfilled and whether
    fulfilling it forces inclusion of a significantly inferior supplier.

    supplier_results: list of (identity_dict, normalized_rank, final_state_dict),
                      already sorted by normalized_rank DESC.
    """
    if min_supplier_quotes <= 1:
        return []

    n_available = len(supplier_results)

    # Case 1: can't fulfill the required number of quotes
    if n_available < min_supplier_quotes:
        return [EscalationTrigger(
            trigger_id="INSUFFICIENT_SUPPLIERS",
            trigger_type="insufficient_suppliers",
            severity="blocking",
            rank_impact=1.0,
            description=(
                f"Only {n_available} qualifying supplier(s) found but policy requires "
                f"{min_supplier_quotes} quotes."
            ),
            escalate_to=_find_escalation_target("insufficient_suppliers", escalation_rules),
            details={
                "available_suppliers": n_available,
                "required_quotes":     min_supplier_quotes,
            },
        )]

    # Case 2: the last-required supplier has a significantly lower rank
    # Suppliers are sorted DESC so index 0 = best.
    scores = [float(fs.get("normalized_rank") or 0) for _, _, fs in supplier_results]

    # Gap at the required boundary: compare last-required vs. one above it
    idx_last_required = min_supplier_quotes - 1       # 0-indexed
    idx_preceding     = min_supplier_quotes - 2       # 0-indexed

    if idx_preceding >= 0:
        gap = scores[idx_preceding] - scores[idx_last_required]
    else:
        gap = 0.0

    if gap < MIN_QUOTES_GAP:
        return []

    last_identity = supplier_results[idx_last_required][0]
    prec_identity = supplier_results[idx_preceding][0]

    return [EscalationTrigger(
        trigger_id="MIN_QUOTES_RANK_GAP",
        trigger_type="min_quotes_gap",
        severity="advisory",
        rank_impact=min(gap, 1.0),
        description=(
            f"Policy requires {min_supplier_quotes} quotes. "
            f"Supplier #{min_supplier_quotes} ({last_identity.get('supplier_name', '?')}, "
            f"rank={scores[idx_last_required]:.3f}) is {gap:.3f} rank points below "
            f"#{idx_preceding + 1} ({prec_identity.get('supplier_name', '?')}, "
            f"rank={scores[idx_preceding]:.3f}). "
            f"The additional quote may be ceremonial — consider a policy deviation."
        ),
        escalate_to=_find_escalation_target("min_quotes_gap", escalation_rules),
        details={
            "required_position":  min_supplier_quotes,
            "required_supplier":  last_identity.get("supplier_name"),
            "required_rank":      scores[idx_last_required],
            "preceding_rank":     scores[idx_preceding],
            "rank_gap":           round(gap, 4),
        },
    )]


# ---------------------------------------------------------------------------
# Context-based adjustments
# ---------------------------------------------------------------------------

def _threshold_multiplier(budget: float) -> float:
    """
    Return an effective threshold multiplier based on total budget.

    Higher budget → multiplier < 1.0 → lower effective thresholds → escalation
    fires on smaller rank impacts.  At REFERENCE_BUDGET the multiplier is 1.0.

    Formula: clamp((REFERENCE_BUDGET / budget) ^ BUDGET_SCALE_EXPONENT,
                   MIN_THRESHOLD_MULTIPLIER, MAX_THRESHOLD_MULTIPLIER)
    """
    if budget <= 0:
        return MAX_THRESHOLD_MULTIPLIER  # no budget info → conservative (don't over-escalate)
    raw = (REFERENCE_BUDGET / budget) ** BUDGET_SCALE_EXPONENT
    return max(MIN_THRESHOLD_MULTIPLIER, min(MAX_THRESHOLD_MULTIPLIER, raw))


def _apply_context_adjustments(
    triggers: list[EscalationTrigger],
    days_until_required: int | float | None,
    budget: float,
) -> tuple[list[EscalationTrigger], list[str]]:
    """
    Post-process raw triggers with two context adjustments and return the
    modified list plus a list of human-readable context notes.

    1. **Urgency suppression** (days_until_required):
       If days_until_required ≤ URGENT_DAYS_THRESHOLD, missing_field and
       min_quotes_gap triggers are downgraded to "logged" — there is no time
       left to act on additional information.  insufficient_suppliers is never
       suppressed because it reflects a structural availability problem.

    2. **Budget scaling** (applied before urgency, which always wins):
       Recompute each trigger's severity using budget-adjusted thresholds.
       Higher budget lowers effective thresholds so even moderate rank impacts
       trigger escalation.  The raw rank_impact is unchanged; only severity
       classification is reconsidered.
    """
    notes: list[str] = []

    # --- Budget-scaled thresholds ---
    mult = _threshold_multiplier(budget)
    eff_blocking = BLOCKING_THRESHOLD * mult
    eff_advisory = ADVISORY_THRESHOLD * mult

    if abs(mult - 1.0) > 0.01:
        direction = "lowered" if mult < 1.0 else "raised"
        notes.append(
            f"Budget-adjusted escalation thresholds (budget={budget:,.0f}, "
            f"multiplier={mult:.2f}): blocking≥{eff_blocking:.3f}, "
            f"advisory≥{eff_advisory:.3f} ({direction} from baseline "
            f"{BLOCKING_THRESHOLD}/{ADVISORY_THRESHOLD})."
        )

    # --- Urgency flag ---
    is_urgent: bool = (
        days_until_required is not None
        and days_until_required <= URGENT_DAYS_THRESHOLD
    )
    if is_urgent:
        notes.append(
            f"Urgency suppression active: days_until_required={days_until_required} "
            f"≤ {URGENT_DAYS_THRESHOLD}. Missing-field and min-quotes-gap escalations "
            f"suppressed — no time to collect additional information."
        )

    # --- Apply to each trigger ---
    _TIME_SENSITIVE = {"missing_field", "min_quotes_gap"}

    adjusted: list[EscalationTrigger] = []
    for t in triggers:
        # 1. Re-classify severity with budget-adjusted thresholds.
        #    insufficient_suppliers is always blocking regardless.
        if t.trigger_type == "insufficient_suppliers":
            new_severity = "blocking"
        elif t.rank_impact >= eff_blocking:
            new_severity = "blocking"
        elif t.rank_impact >= eff_advisory:
            new_severity = "advisory"
        else:
            new_severity = "logged"

        # 2. Urgency suppression overrides (always wins against budget upgrade).
        suppression_reason: str | None = None
        if is_urgent and t.trigger_type in _TIME_SENSITIVE:
            new_severity = "logged"
            suppression_reason = (
                f"Suppressed: {days_until_required} day(s) remain — "
                f"no time to act on this information."
            )

        adjusted.append(EscalationTrigger(
            trigger_id=t.trigger_id,
            trigger_type=t.trigger_type,
            severity=new_severity,
            rank_impact=t.rank_impact,
            description=t.description,
            escalate_to=t.escalate_to if new_severity in ("blocking", "advisory") else None,
            details=t.details,
            suppression_reason=suppression_reason,
        ))

    return adjusted, notes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate_escalations(
    request:            dict[str, Any],
    outcome:            dict[str, Any],
    fix_in_keys:        set[str],
    field_impact_map:   dict[str, float],
    escalation_rules:   list[dict],
) -> EscalationAssessment:
    """
    Run all escalation assessors and return a consolidated EscalationAssessment.

    Parameters
    ----------
    request:
        The normalised request dict (fix_in fields).
    outcome:
        The dict returned by run_procurement_evaluation — expected keys
        ``supplier_results`` (list) and ``global_outputs`` (dict).
    fix_in_keys:
        Set of fix_in field names from the schema.
    field_impact_map:
        Pre-computed per-field rank-impact scores from build_field_impact_map().
    escalation_rules:
        Natural-language escalation rule records from the escalation_rules store.
    """
    triggers: list[EscalationTrigger] = []

    # --- Missing fields ---
    triggers.extend(
        assess_missing_fields(request, fix_in_keys, field_impact_map, escalation_rules)
    )

    # --- Min quotes gap ---
    supplier_results = outcome.get("supplier_results", [])
    global_outputs   = outcome.get("global_outputs", {})
    min_quotes = int(global_outputs.get("min_supplier_quotes") or 1)
    triggers.extend(
        assess_min_quotes_gap(supplier_results, min_quotes, escalation_rules)
    )

    # --- Context adjustments: urgency + budget scaling ---
    days_raw = request.get("days_until_required")
    try:
        days_until_required: int | float | None = float(days_raw) if days_raw is not None else None
    except (TypeError, ValueError):
        days_until_required = None

    budget = float(request.get("budget") or 0)
    triggers, context_notes = _apply_context_adjustments(triggers, days_until_required, budget)

    # Sort: blocking first, then by descending rank_impact
    triggers.sort(key=lambda t: (t.severity != "blocking", -t.rank_impact))

    assessment = EscalationAssessment(triggers=triggers, context_notes=context_notes)
    assessment.has_blocking = any(t.severity == "blocking" for t in triggers)
    assessment.has_advisory = any(t.severity == "advisory" for t in triggers)
    assessment.should_escalate = assessment.has_blocking or assessment.has_advisory

    return assessment
