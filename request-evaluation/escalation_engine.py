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
# Confidence-based escalation thresholds (CR-C rules)
# ---------------------------------------------------------------------------

# CR-C01: below this confidence score the evaluation always escalates (blocking)
CONFIDENCE_FLOOR_BLOCKING: float = 0.25

# CR-C02: "false certainty" — top rank looks decisive but the scoring is unreliable
CONFIDENCE_FALSE_CERTAINTY_SCORE:   float = 0.35  # confidence ceiling
CONFIDENCE_FALSE_CERTAINTY_RANK:    float = 0.75  # normalized_rank floor

# CR-C03: low input_completeness → requester clarification
CONFIDENCE_INPUT_COMPLETENESS_MIN: float = 0.50

# CR-C04: low market_coverage → head of category
CONFIDENCE_MARKET_COVERAGE_MIN: float = 0.40

# CR-C05: low data_reliability → sourcing excellence
CONFIDENCE_DATA_RELIABILITY_MIN: float = 0.40

# CR-C06: fast-track suppression — flag if eligible AND confidence is low
CONFIDENCE_FAST_TRACK_MIN: float = 0.50

# Confidence severity boost: when overall confidence is below this level,
# promote existing advisory triggers to blocking
CONFIDENCE_SEVERITY_BOOST_THRESHOLD: float = 0.40

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
class EscalationRecord:
    """
    A single actionable escalation item returned to the caller.

    Structurally agnostic to specific roles: person_to_escalate_to is a plain
    string derived at runtime from policy rules or the engine, not a hardcoded
    field name.  Any escalate_to_<role> action output or engine trigger maps to
    one of these records regardless of what role names the policy uses.
    """
    person_to_escalate_to:  str   # e.g. "CPO", "Procurement Manager", "Requester"
    reason_for_escalation:  str   # human-readable explanation of why escalation is needed
    task_for_escalation:    str   # what the escalation recipient is expected to do
    severity:               str   # "blocking" | "advisory"
    source:                 str   # trigger_id or action key, e.g. "escalate_to_cpo" / "CR_C01_..."
    source_type:            str   # "policy_rule" | "engine" | "confidence"


@dataclass
class EscalationTrigger:
    """
    Internal intermediate used during engine evaluation.
    Converted to EscalationRecord before being returned to the caller.
    """
    trigger_id:        str
    trigger_type:      str    # "missing_field" | "min_quotes_gap" | "insufficient_suppliers" | "confidence"
    severity:          str    # "blocking" | "advisory" | "logged"
    rank_impact:       float  # 0.0 – 1.0
    description:       str
    escalate_to:       str | None  # role / team from escalation_rules store
    details:           dict = field(default_factory=dict)
    suppression_reason: str | None = None  # set when urgency or context suppresses this trigger


@dataclass
class EscalationAssessment:
    """All escalation records for a single request evaluation."""
    needs_escalation:  bool = False   # True if any blocking or advisory record exists
    has_blocking:      bool = False
    has_advisory:      bool = False
    records:           list[EscalationRecord] = field(default_factory=list)
    context_notes:     list[str] = field(default_factory=list)  # human-readable modifier log


# ---------------------------------------------------------------------------
# Action-escalation metadata  (enrichment config — not schema)
# ---------------------------------------------------------------------------
# Maps escalate_to_<role> action output keys to human-readable metadata.
# The engine works with ANY key matching the escalate_to_* prefix — this dict
# only provides richer person names, reasons, and tasks for known roles.
# Unknown keys are handled dynamically by _key_to_person() and generic defaults.
#
# To add a new escalation role: add an entry here.  No schema change required.
# ---------------------------------------------------------------------------

_ACTION_ESCALATION_METADATA: dict[str, dict] = {
    "escalate_to_requester": {
        "person":        "Requester",
        "reason_prefix": "Requester clarification is needed before sourcing can continue",
        "task":          "Provide or confirm the missing or ambiguous request information so sourcing can proceed.",
        "severity":      "advisory",
    },
    "escalate_to_procurement_manager": {
        "person":        "Procurement Manager",
        "reason_prefix": "Procurement Manager approval or policy deviation is required",
        "task":          "Review and approve the sourcing decision, or grant a policy deviation if appropriate.",
        "severity":      "blocking",
    },
    "escalate_to_head_of_category": {
        "person":        "Head of Category",
        "reason_prefix": "Head of Category sign-off is required for this procurement",
        "task":          "Confirm category strategy alignment and approve the sourcing approach.",
        "severity":      "blocking",
    },
    "escalate_to_head_of_strategic_sourcing": {
        "person":        "Head of Strategic Sourcing",
        "reason_prefix": "Head of Strategic Sourcing approval is required for this high-value contract",
        "task":          "Approve the sourcing decision in line with strategic procurement policy.",
        "severity":      "blocking",
    },
    "escalate_to_cpo": {
        "person":        "CPO",
        "reason_prefix": "CPO executive approval is required for this procurement",
        "task":          "Grant executive approval per governance policy before any commitment is made.",
        "severity":      "blocking",
    },
    "escalate_to_security_compliance": {
        "person":        "Security & Compliance",
        "reason_prefix": "Security and compliance review is required",
        "task":          "Review data residency or security requirements and validate supplier eligibility.",
        "severity":      "blocking",
    },
    "escalate_to_sourcing_excellence": {
        "person":        "Sourcing Excellence Lead",
        "reason_prefix": "Sourcing Excellence review is required",
        "task":          "Assess single-supplier risk and advise on market engagement strategy.",
        "severity":      "advisory",
    },
    "escalate_to_marketing_governance": {
        "person":        "Marketing Governance Lead",
        "reason_prefix": "Marketing Governance review is required for this category",
        "task":          "Review brand safety concerns and approve supplier engagement for the marketing category.",
        "severity":      "blocking",
    },
    "escalate_to_regional_compliance": {
        "person":        "Regional Compliance Lead",
        "reason_prefix": "Regional compliance approval is required",
        "task":          "Validate supplier registration and sanction screening for the delivery country.",
        "severity":      "blocking",
    },
}

# Default tasks per engine trigger type (used when converting EscalationTrigger → EscalationRecord)
_TRIGGER_TASKS: dict[str, str] = {
    "missing_field":          "Supply or confirm the missing information before the sourcing process continues.",
    "min_quotes_gap":         "Review whether a policy deviation is appropriate, or confirm the additional quote is still required.",
    "insufficient_suppliers": "Expand the approved supplier pool or review scope and category mapping for this request.",
    "confidence":             "Manually review the ranking output before issuing a purchase order or advancing the sourcing process.",
}


def _key_to_person(key: str) -> str:
    """
    Dynamically derive a human-readable role name from an escalate_to_* key.

    escalate_to_procurement_manager  →  "Procurement Manager"
    escalate_to_cpo                  →  "CPO"
    escalate_to_head_of_category     →  "Head Of Category"

    Used as a fallback when the key is not in _ACTION_ESCALATION_METADATA.
    """
    stem = key.removeprefix("escalate_to_").replace("_", " ")
    return " ".join(w.capitalize() for w in stem.split())


def _build_action_reason(key: str, meta: dict, request: dict[str, Any]) -> str:
    """Build a reason string for an action-pipeline escalation record."""
    prefix = meta.get("reason_prefix") or f"{_key_to_person(key)} approval required"
    budget   = request.get("budget")
    currency = request.get("currency", "")
    cat      = request.get("category_l2") or request.get("category_l1") or ""

    parts = [prefix]
    if budget and currency:
        try:
            parts.append(f"(budget: {currency} {float(budget):,.0f})")
        except (TypeError, ValueError):
            pass
    if cat:
        parts.append(f"in category '{cat}'")
    return " ".join(parts) + "."


def build_action_escalations(
    global_outputs: dict[str, Any],
    request: dict[str, Any],
) -> list[EscalationRecord]:
    """
    Convert escalate_to_* boolean flags in global_outputs into EscalationRecords.

    Works with any key matching the escalate_to_* prefix — no prior knowledge of
    specific role names is required.  _ACTION_ESCALATION_METADATA provides richer
    text for known roles; unknown roles fall back to dynamic key-name parsing.
    """
    records: list[EscalationRecord] = []
    for key, val in global_outputs.items():
        if not key.startswith("escalate_to_"):
            continue
        # Treat True, "True", 1 as active — anything falsy is ignored
        try:
            active = bool(val) and str(val).lower() not in ("false", "0", "none", "")
        except Exception:
            active = False
        if not active:
            continue

        meta     = _ACTION_ESCALATION_METADATA.get(key, {})
        person   = meta.get("person") or _key_to_person(key)
        task     = meta.get("task") or "Review and take appropriate action for this procurement request."
        severity = meta.get("severity", "blocking")
        reason   = _build_action_reason(key, meta, request)

        records.append(EscalationRecord(
            person_to_escalate_to=person,
            reason_for_escalation=reason,
            task_for_escalation=task,
            severity=severity,
            source=key,
            source_type="policy_rule",
        ))
    return records


def _trigger_to_record(trigger: EscalationTrigger) -> EscalationRecord:
    """Convert an internal EscalationTrigger into a public EscalationRecord."""
    source_type = "confidence" if trigger.trigger_type == "confidence" else "engine"
    task = _TRIGGER_TASKS.get(trigger.trigger_type, "Review and take appropriate action.")
    return EscalationRecord(
        person_to_escalate_to=trigger.escalate_to or "Procurement Manager",
        reason_for_escalation=trigger.description,
        task_for_escalation=task,
        severity=trigger.severity,
        source=trigger.trigger_id,
        source_type=source_type,
    )


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
    ("confidence",             ["confidence", "unreliable", "uncertain", "data quality"]),
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
# Confidence-based trigger assessors  (CR-C01 … CR-C06)
# ---------------------------------------------------------------------------

# Fallback routing targets when the escalation_rules store has no match.
# These mirror the standard ER-rule routing for the same concern areas.
_CONFIDENCE_FALLBACK_ROUTES: dict[str, str] = {
    "floor":            "Procurement Manager",
    "false_certainty":  "Procurement Manager",
    "input":            "Requester",
    "market":           "Head of Category",
    "data":             "Sourcing Excellence Lead",
    "fast_track":       "Procurement Manager",
}


def _confidence_route(dimension_key: str, escalation_rules: list[dict]) -> str:
    """Return the escalate_to target for a confidence-based trigger.

    Tries the rules store first; falls back to _CONFIDENCE_FALLBACK_ROUTES.
    """
    from_store = _find_escalation_target("confidence", escalation_rules)
    return from_store or _CONFIDENCE_FALLBACK_ROUTES.get(dimension_key, "Procurement Manager")


def assess_confidence_triggers(
    confidence_assessment: Any,
    supplier_results: list[tuple],
    global_outputs: dict[str, Any],
    escalation_rules: list[dict],
) -> list[EscalationTrigger]:
    """
    Evaluate confidence-based escalation rules (CR-C01 … CR-C06).

    These fire on *output* conditions — the computed confidence score and its
    per-dimension breakdown — rather than on request input fields.  They are
    never suppressed by urgency (trigger_type = "confidence" is intentionally
    excluded from _TIME_SENSITIVE in _apply_context_adjustments).

    CR-C01  confidence_floor       — overall score < 0.25: always block.
    CR-C02  false_certainty        — top rank high but confidence low: block.
    CR-C03  input_incomplete       — input_completeness dim < 0.50: requester.
    CR-C04  market_coverage_poor   — market_coverage dim < 0.40: head of category.
    CR-C05  data_sparse            — data_reliability dim < 0.40: sourcing excellence.
    CR-C06  fast_track_risk        — fast_track eligible AND confidence < 0.50: advisory.
    """
    if confidence_assessment is None:
        return []

    score      = float(getattr(confidence_assessment, "score", 1.0))
    label      = getattr(confidence_assessment, "label", "high")
    breakdown  = getattr(confidence_assessment, "breakdown", {})
    dims       = breakdown.get("dimensions", {})
    explanation = getattr(confidence_assessment, "explanation", "")

    triggers: list[EscalationTrigger] = []

    # CR-C01 — Confidence floor (very_low): always block regardless of other conditions.
    if score < CONFIDENCE_FLOOR_BLOCKING:
        triggers.append(EscalationTrigger(
            trigger_id="CR_C01_CONFIDENCE_FLOOR",
            trigger_type="confidence",
            severity="blocking",
            rank_impact=1.0 - score,
            description=(
                f"Overall ranking confidence is {label} ({score:.2f}). "
                f"The evaluation output is too uncertain to act on without human review. "
                f"Primary limiting factor: {explanation}"
            ),
            escalate_to=_confidence_route("floor", escalation_rules),
            details={"confidence_score": score, "label": label, "dimensions": dims},
        ))

    # CR-C02 — False certainty: high normalized_rank but low confidence.
    # This is the most dangerous case — the output *looks* decisive but the
    # scoring is built on unreliable data.
    if supplier_results and score < CONFIDENCE_FALSE_CERTAINTY_SCORE:
        top_rank = float(supplier_results[0][2].get("normalized_rank") or 0)
        if top_rank >= CONFIDENCE_FALSE_CERTAINTY_RANK:
            top_name = supplier_results[0][0].get("supplier_name", "?")
            triggers.append(EscalationTrigger(
                trigger_id="CR_C02_FALSE_CERTAINTY",
                trigger_type="confidence",
                severity="blocking",
                rank_impact=top_rank * (1.0 - score),
                description=(
                    f"'{top_name}' ranks #{1} with normalized_rank={top_rank:.3f} (looks decisive) "
                    f"but confidence is {label} ({score:.2f}). "
                    f"The high rank may not be reliable — verify before issuing a PO. "
                    f"Limiting factor: {explanation}"
                ),
                escalate_to=_confidence_route("false_certainty", escalation_rules),
                details={
                    "top_supplier":      top_name,
                    "normalized_rank":   top_rank,
                    "confidence_score":  score,
                    "confidence_label":  label,
                },
            ))

    # CR-C03 — Input incompleteness: key scoring inputs were absent.
    # Route to requester so they can provide quantity / budget / category.
    input_dim = float(dims.get("input_completeness", 1.0))
    if input_dim < CONFIDENCE_INPUT_COMPLETENESS_MIN and score >= CONFIDENCE_FLOOR_BLOCKING:
        # Skip if CR-C01 already fired — that's already blocking at a higher level.
        triggers.append(EscalationTrigger(
            trigger_id="CR_C03_INPUT_INCOMPLETE",
            trigger_type="confidence",
            severity="advisory",
            rank_impact=1.0 - input_dim,
            description=(
                f"Input completeness is low ({input_dim:.2f}): quantity, budget, or category "
                f"may be absent or zero, making the cost-based ranking unreliable. "
                f"Requester should confirm or supply the missing fields."
            ),
            escalate_to=_confidence_route("input", escalation_rules),
            details={"input_completeness": input_dim},
        ))

    # CR-C04 — Poor market coverage: too few suppliers or high exclusion rate.
    # Route to head of category — the supplier pool itself needs attention.
    market_dim = float(dims.get("market_coverage", 1.0))
    if market_dim < CONFIDENCE_MARKET_COVERAGE_MIN and score >= CONFIDENCE_FLOOR_BLOCKING:
        meta = breakdown.get("meta", {})
        triggers.append(EscalationTrigger(
            trigger_id="CR_C04_MARKET_COVERAGE_POOR",
            trigger_type="confidence",
            severity="advisory",
            rank_impact=1.0 - market_dim,
            description=(
                f"Market coverage is low ({market_dim:.2f}): only "
                f"{meta.get('n_surviving_suppliers', '?')} supplier(s) qualified "
                f"({meta.get('n_excluded', '?')} excluded). "
                f"Competitive pricing cannot be confirmed — Head of Category should "
                f"review the supplier pool."
            ),
            escalate_to=_confidence_route("market", escalation_rules),
            details={
                "market_coverage":       market_dim,
                "n_surviving_suppliers": meta.get("n_surviving_suppliers"),
                "n_excluded":            meta.get("n_excluded"),
            },
        ))

    # CR-C05 — Sparse historical data: z-score unreliable or fallback ratio used.
    # Route to sourcing excellence — they own historical award data.
    data_dim = float(dims.get("data_reliability", 1.0))
    if data_dim < CONFIDENCE_DATA_RELIABILITY_MIN and score >= CONFIDENCE_FLOOR_BLOCKING:
        meta = breakdown.get("meta", {})
        used_zscore = meta.get("used_zscore_sigmoid", False)
        triggers.append(EscalationTrigger(
            trigger_id="CR_C05_DATA_SPARSE",
            trigger_type="confidence",
            severity="advisory",
            rank_impact=1.0 - data_dim,
            description=(
                f"Historical data reliability is low ({data_dim:.2f}): "
                f"{meta.get('n_hist_data_points', 0)} historical award(s) found, "
                f"{'z-score sigmoid used' if used_zscore else 'fallback price-ratio formula used — less discriminating'}. "
                f"Sourcing Excellence should validate that the cost score reflects "
                f"actual market rates for this category."
            ),
            escalate_to=_confidence_route("data", escalation_rules),
            details={
                "data_reliability":    data_dim,
                "n_hist_data_points":  meta.get("n_hist_data_points"),
                "used_zscore_sigmoid": used_zscore,
            },
        ))

    # CR-C06 — Fast-track risk: policy says fast-track is eligible but confidence
    # is too low to safely skip competitive quoting.
    fast_track_eligible = global_outputs.get("fast_track_eligible")
    if fast_track_eligible and score < CONFIDENCE_FAST_TRACK_MIN:
        triggers.append(EscalationTrigger(
            trigger_id="CR_C06_FAST_TRACK_CONFIDENCE_RISK",
            trigger_type="confidence",
            severity="advisory",
            rank_impact=CONFIDENCE_FAST_TRACK_MIN - score,
            description=(
                f"Fast-track is policy-eligible for this request, but ranking confidence "
                f"is {label} ({score:.2f}). Skipping competitive quoting on an unreliable "
                f"ranking carries risk — Procurement Manager should approve any fast-track "
                f"deviation explicitly."
            ),
            escalate_to=_confidence_route("fast_track", escalation_rules),
            details={
                "confidence_score":      score,
                "confidence_label":      label,
                "fast_track_eligible":   fast_track_eligible,
            },
        ))

    return triggers


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
    confidence_score: float | None = None,
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

    3. **Confidence severity boost**:
       When overall confidence is below CONFIDENCE_SEVERITY_BOOST_THRESHOLD,
       existing advisory triggers (from missing_field / min_quotes_gap) are
       promoted to blocking.  Confidence triggers (type="confidence") are never
       suppressed by urgency — they fire independently of time pressure.
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

    # --- Confidence severity boost ---
    low_confidence = (
        confidence_score is not None
        and confidence_score < CONFIDENCE_SEVERITY_BOOST_THRESHOLD
    )
    if low_confidence:
        notes.append(
            f"Confidence severity boost active (confidence={confidence_score:.2f} "
            f"< {CONFIDENCE_SEVERITY_BOOST_THRESHOLD}): advisory triggers from "
            f"missing_field and min_quotes_gap are promoted to blocking."
        )

    # --- Urgency flag ---
    # ISSUE-007: Only suppress for requests that are upcoming (0 ≤ days ≤ threshold).
    # Overdue requests (negative days) are NOT suppressed — they may still need remediation.
    is_urgent: bool = (
        days_until_required is not None
        and 0 <= days_until_required <= URGENT_DAYS_THRESHOLD
    )
    if is_urgent:
        notes.append(
            f"Urgency suppression active: days_until_required={days_until_required} "
            f"≤ {URGENT_DAYS_THRESHOLD}. Missing-field and min-quotes-gap escalations "
            f"suppressed — no time to collect additional information."
        )
    elif days_until_required is not None and days_until_required < 0:
        notes.append(
            f"Request is overdue by {abs(days_until_required)} day(s) "
            f"(days_until_required={days_until_required}). Escalations are NOT suppressed — "
            f"overdue requests may still require remediation."
        )

    # --- Apply to each trigger ---
    # "confidence" triggers are intentionally excluded from urgency suppression:
    # low confidence is a structural problem independent of time pressure.
    _TIME_SENSITIVE = {"missing_field", "min_quotes_gap"}

    adjusted: list[EscalationTrigger] = []
    for t in triggers:
        # 1. Re-classify severity with budget-adjusted thresholds.
        #    insufficient_suppliers and confidence triggers are always at least
        #    their originally-computed severity (confidence triggers set their own).
        if t.trigger_type in ("insufficient_suppliers", "confidence"):
            new_severity = t.severity
        elif t.rank_impact >= eff_blocking:
            new_severity = "blocking"
        elif t.rank_impact >= eff_advisory:
            new_severity = "advisory"
        else:
            new_severity = "logged"

        # 2. Confidence severity boost: promote advisory → blocking for
        #    non-confidence trigger types when confidence is globally low.
        if low_confidence and new_severity == "advisory" and t.trigger_type in _TIME_SENSITIVE:
            new_severity = "blocking"

        # 3. Urgency suppression: overrides everything for time-sensitive types,
        #    but NEVER suppresses confidence triggers even under time pressure.
        suppression_reason: str | None = None
        if is_urgent and t.trigger_type in _TIME_SENSITIVE:
            if low_confidence:
                # Confidence boost vetoes urgency suppression for advisory→blocking cases.
                # Urgency still suppresses triggers that would otherwise be "logged".
                if new_severity == "logged":
                    suppression_reason = (
                        f"Suppressed: {days_until_required} day(s) remain — "
                        f"no time to act on this information."
                    )
            else:
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
    request:               dict[str, Any],
    outcome:               dict[str, Any],
    fix_in_keys:           set[str],
    field_impact_map:      dict[str, float],
    escalation_rules:      list[dict],
    confidence_assessment: Any | None = None,
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
    confidence_assessment:
        Optional ConfidenceAssessment from result_flags.compute_confidence_score().
        When provided, enables CR-C01..CR-C06 confidence-based triggers and
        severity boosting of existing triggers.
    """
    triggers: list[EscalationTrigger] = []

    supplier_results = outcome.get("supplier_results", [])
    global_outputs   = outcome.get("global_outputs", {})

    # --- Missing fields ---
    triggers.extend(
        assess_missing_fields(request, fix_in_keys, field_impact_map, escalation_rules)
    )

    # --- Min quotes gap ---
    # O-6: use `or 1` only when the value is absent (None), not when it is 0.
    # A deliberately-set 0 means "no minimum" and should not be overridden to 1.
    _min_quotes_raw = global_outputs.get("min_supplier_quotes")
    min_quotes = int(_min_quotes_raw) if _min_quotes_raw is not None else 1
    triggers.extend(
        assess_min_quotes_gap(supplier_results, min_quotes, escalation_rules)
    )

    # --- Confidence-based triggers (CR-C01 … CR-C06) ---
    triggers.extend(
        assess_confidence_triggers(
            confidence_assessment, supplier_results, global_outputs, escalation_rules
        )
    )

    # --- Context adjustments: urgency + budget scaling + confidence severity boost ---
    days_raw = request.get("days_until_required")
    try:
        days_until_required: int | float | None = float(days_raw) if days_raw is not None else None
    except (TypeError, ValueError):
        days_until_required = None

    budget = float(request.get("budget") or 0)
    confidence_score = (
        float(getattr(confidence_assessment, "score", 1.0))
        if confidence_assessment is not None else None
    )
    triggers, context_notes = _apply_context_adjustments(
        triggers, days_until_required, budget, confidence_score
    )

    # --- Convert engine triggers → EscalationRecord (blocking + advisory only) ---
    # "logged" triggers are suppressed and excluded from the public record list.
    engine_records: list[EscalationRecord] = [
        _trigger_to_record(t)
        for t in sorted(triggers, key=lambda t: (t.severity != "blocking", -t.rank_impact))
        if t.severity in ("blocking", "advisory")
    ]

    # --- Convert action-pipeline escalate_to_* booleans → EscalationRecord ---
    # These come from AT/ER rule actions evaluated during the supplier pipeline.
    # They are role-agnostic: any escalate_to_<anything> key is handled dynamically.
    action_records = build_action_escalations(global_outputs, request)

    # Merge: policy-rule records first (they fire from explicit thresholds),
    # then engine records (structural / confidence issues).
    all_records = action_records + engine_records

    assessment = EscalationAssessment(records=all_records, context_notes=context_notes)
    assessment.has_blocking    = any(r.severity == "blocking"  for r in all_records)
    assessment.has_advisory    = any(r.severity == "advisory"  for r in all_records)
    assessment.needs_escalation = assessment.has_blocking or assessment.has_advisory

    return assessment
