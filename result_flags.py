"""
result_flags.py — Result-quality warning flags for procurement evaluations.

Flags are attached to the ranking output when certain conditions in the
result indicate that the evaluation outcome may be unreliable, misleading,
or require human review beyond what the escalation engine captures.

Unlike escalation triggers (which fire on *input* conditions — missing fields,
policy deviations), flags fire on *output* conditions — what the computed
ranking actually looks like.

Flag taxonomy
─────────────
BUDGET_INSUFFICIENT
    Every supplier's total cost exceeds budget by more than 20 %. The budget
    is too low for this category/quantity combination at current market prices.

LOW_RANK_CLUSTER
    All surviving suppliers have a normalized_rank below 0.30 AND their
    ranks span less than 0.10. The request may not be fulfillable as
    specified — scores are uniformly low and indistinguishable.

INDISTINGUISHABLE_RANKS
    ≥ 3 suppliers whose ranks span < 0.05. Any of them could be selected;
    minor price fluctuations would flip the order. The ranking adds little
    decision value.

SINGLE_QUALIFIED_SUPPLIER
    Fewer than 2 suppliers survived evaluation. Market coverage is
    insufficient for competitive pricing.

DOMINANT_SUPPLIER
    The top supplier's rank exceeds #2 by more than 0.40 points. The
    decision is trivially clear — verify this is not a data anomaly.

ALL_COMPLIANCE_PENALIZED
    Every surviving supplier has a compliance_score < 1.0. The category
    requirements may be systematically over-specified or the supplier pool
    is poorly matched to the category.

ZERO_OR_MISSING_QUANTITY
    The request quantity is 0, missing, or non-positive. Cost totals are
    unreliable; ranking by cost is meaningless.

HIGH_EXCLUSION_RATE
    More than half of the candidate suppliers were excluded by hard
    compliance gates. The policy may be filtering out legitimate suppliers.

PREFERRED_SUPPLIER_EXCLUDED
    The requester named a specific supplier (preferred_supplier_mentioned)
    that was excluded by a hard compliance gate or pre-evaluation filter.
    Labelled: "Preferred supplier restricted due to: <reason>".

PREFERRED_SUPPLIER_COMPLIANCE_CONCERN
    The named preferred supplier survived but the text compliance check
    flagged it with a score below 0.50 — the request text may contain a
    conflicting directive (e.g. "use X with no exception" while other
    requirements rule X out).

PREFERRED_SUPPLIER_NOT_FOUND
    The named preferred supplier does not appear in the evaluated pool at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Thresholds (tweak these without touching the logic)
# ---------------------------------------------------------------------------

# BUDGET_INSUFFICIENT: fraction by which cost_total must exceed budget
BUDGET_OVERAGE_THRESHOLD: float = 0.20

# LOW_RANK_CLUSTER: max rank AND max spread for the cluster warning
LOW_RANK_MAX:    float = 0.30
LOW_RANK_SPREAD: float = 0.10

# INDISTINGUISHABLE_RANKS: min suppliers and max spread
INDISTINGUISHABLE_MIN_SUPPLIERS: int   = 3
INDISTINGUISHABLE_MAX_SPREAD:    float = 0.05

# DOMINANT_SUPPLIER: min rank gap between #1 and #2
DOMINANT_GAP: float = 0.40

# ALL_COMPLIANCE_PENALIZED: threshold below which a score is "penalized"
COMPLIANCE_PENALTY_THRESHOLD: float = 1.0

# HIGH_EXCLUSION_RATE: fraction of total candidates that were excluded
HIGH_EXCLUSION_FRACTION: float = 0.50

# PREFERRED_SUPPLIER_RESTRICTED: text compliance score below which a name-match
# is considered "penalized" by the text compliance check
PREFERRED_TEXT_COMPLIANCE_THRESHOLD: float = 0.50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ResultFlag:
    """A single result-quality warning attached to an evaluation outcome."""
    flag_id:     str   # e.g. "BUDGET_INSUFFICIENT"
    severity:    str   # "warning" | "info"
    description: str
    details:     dict = field(default_factory=dict)


@dataclass
class FlagAssessment:
    """All result flags for a single evaluation."""
    flags:        list[ResultFlag] = field(default_factory=list)
    has_warnings: bool = False   # True if any "warning"-severity flag fired


# ---------------------------------------------------------------------------
# Individual flag assessors
# ---------------------------------------------------------------------------

def _flag_budget_insufficient(
    supplier_results: list[tuple],
    budget: float,
) -> ResultFlag | None:
    if budget <= 0 or not supplier_results:
        return None

    all_over = all(
        float(fs.get("cost_total") or 0) > budget * (1 + BUDGET_OVERAGE_THRESHOLD)
        for _, _, fs in supplier_results
    )
    if not all_over:
        return None

    min_cost = min(float(fs.get("cost_total") or 0) for _, _, fs in supplier_results)
    pct_over = round((min_cost - budget) / budget * 100, 1)
    return ResultFlag(
        flag_id="BUDGET_INSUFFICIENT",
        severity="warning",
        description=(
            f"All supplier quotes exceed the budget by more than "
            f"{BUDGET_OVERAGE_THRESHOLD * 100:.0f}% "
            f"(cheapest quote is {pct_over}% over budget). "
            f"The budget may be too low for this category or quantity."
        ),
        details={"budget": budget, "min_cost_total": round(min_cost, 2), "pct_over": pct_over},
    )


def _flag_low_rank_cluster(supplier_results: list[tuple]) -> ResultFlag | None:
    if not supplier_results:
        return None

    ranks = [float(fs.get("normalized_rank") or 0) for _, _, fs in supplier_results]
    max_rank = max(ranks)
    spread = max_rank - min(ranks)

    if max_rank >= LOW_RANK_MAX or spread >= LOW_RANK_SPREAD:
        return None

    return ResultFlag(
        flag_id="LOW_RANK_CLUSTER",
        severity="warning",
        description=(
            f"All suppliers rank below {LOW_RANK_MAX:.0%} and are clustered within "
            f"{spread:.3f} rank points of each other. The request may not be fulfillable "
            f"at current specifications, or there is insufficient data to differentiate suppliers."
        ),
        details={"max_rank": round(max_rank, 4), "rank_spread": round(spread, 4)},
    )


def _flag_indistinguishable_ranks(supplier_results: list[tuple]) -> ResultFlag | None:
    if len(supplier_results) < INDISTINGUISHABLE_MIN_SUPPLIERS:
        return None

    ranks = [float(fs.get("normalized_rank") or 0) for _, _, fs in supplier_results]
    spread = max(ranks) - min(ranks)

    if spread >= INDISTINGUISHABLE_MAX_SPREAD:
        return None

    return ResultFlag(
        flag_id="INDISTINGUISHABLE_RANKS",
        severity="warning",
        description=(
            f"{len(ranks)} suppliers are ranked within {spread:.4f} points of each other "
            f"(threshold: {INDISTINGUISHABLE_MAX_SPREAD}). "
            f"The ranking adds little decision value — any quote may be equivalent."
        ),
        details={"n_suppliers": len(ranks), "rank_spread": round(spread, 4)},
    )


def _flag_single_qualified_supplier(supplier_results: list[tuple]) -> ResultFlag | None:
    n = len(supplier_results)
    if n >= 2:
        return None

    return ResultFlag(
        flag_id="SINGLE_QUALIFIED_SUPPLIER",
        severity="warning",
        description=(
            f"Only {n} supplier(s) qualified after evaluation. "
            f"Competitive pricing cannot be guaranteed without market alternatives."
        ),
        details={"n_qualified": n},
    )


def _flag_dominant_supplier(supplier_results: list[tuple]) -> ResultFlag | None:
    if len(supplier_results) < 2:
        return None

    # supplier_results is sorted DESC by rank
    top_name = supplier_results[0][0].get("supplier_name", "?")
    rank_1 = float(supplier_results[0][2].get("normalized_rank") or 0)
    rank_2 = float(supplier_results[1][2].get("normalized_rank") or 0)
    gap = rank_1 - rank_2

    if gap < DOMINANT_GAP:
        return None

    return ResultFlag(
        flag_id="DOMINANT_SUPPLIER",
        severity="info",
        description=(
            f"'{top_name}' leads the ranking by {gap:.3f} points (rank {rank_1:.3f} vs "
            f"{rank_2:.3f} for #2). The selection is unambiguous — verify this is not a "
            f"data or configuration anomaly before issuing a single quote."
        ),
        details={"top_supplier": top_name, "rank_1": rank_1, "rank_2": rank_2, "gap": round(gap, 4)},
    )


def _flag_all_compliance_penalized(supplier_results: list[tuple]) -> ResultFlag | None:
    if not supplier_results:
        return None

    penalized = [
        float(fs.get("compliance_score") or 1.0) < COMPLIANCE_PENALTY_THRESHOLD
        for _, _, fs in supplier_results
    ]
    if not all(penalized):
        return None

    scores = [round(float(fs.get("compliance_score") or 1.0), 3) for _, _, fs in supplier_results]
    return ResultFlag(
        flag_id="ALL_COMPLIANCE_PENALIZED",
        severity="warning",
        description=(
            f"Every qualifying supplier has a compliance penalty (compliance_score < 1.0). "
            f"The category requirements may be over-specified, or the supplier pool is "
            f"poorly matched to this category."
        ),
        details={"compliance_scores": scores},
    )


def _flag_zero_quantity(request: dict[str, Any]) -> ResultFlag | None:
    qty = request.get("quantity")
    try:
        qty_val = float(qty) if qty is not None else 0.0
    except (TypeError, ValueError):
        qty_val = 0.0

    if qty_val > 0:
        return None

    return ResultFlag(
        flag_id="ZERO_OR_MISSING_QUANTITY",
        severity="warning",
        description=(
            f"Request quantity is {qty!r}. Cost totals depend on quantity — "
            f"rankings by cost are unreliable when quantity is zero or absent."
        ),
        details={"quantity": qty},
    )


def _flag_high_exclusion_rate(
    n_total: int,
    n_excluded: int,
) -> ResultFlag | None:
    if n_total == 0 or n_excluded == 0:
        return None

    fraction = n_excluded / n_total
    if fraction < HIGH_EXCLUSION_FRACTION:
        return None

    return ResultFlag(
        flag_id="HIGH_EXCLUSION_RATE",
        severity="warning",
        description=(
            f"{n_excluded} of {n_total} candidate suppliers ({fraction:.0%}) were excluded by "
            f"hard compliance gates. The policy may be filtering out legitimate suppliers, or "
            f"the supplier pool is not appropriate for this category."
        ),
        details={"n_total": n_total, "n_excluded": n_excluded, "exclusion_fraction": round(fraction, 3)},
    )


def _normalize_name(name: str) -> str:
    return name.lower().strip()


def _names_match(mentioned: str, candidate: str) -> bool:
    """Case-insensitive substring match in either direction."""
    a = _normalize_name(mentioned)
    b = _normalize_name(candidate)
    return a in b or b in a


def _flag_preferred_supplier_restricted(
    request:          dict[str, Any],
    supplier_results: list[tuple],
    all_supplier_logs: list[dict],
) -> ResultFlag | None:
    """
    Emit an INFO flag when the requester named a preferred supplier
    (preferred_supplier_mentioned) that was excluded, penalized by the text
    compliance check, or absent from the supplier pool entirely.

    all_supplier_logs: list of dicts with keys:
        supplier_name, supplier_id, excluded, exclusion_reason,
        normalized_rank, text_compliance_score (may be absent)
    """
    mentioned: str | None = request.get("preferred_supplier_mentioned") or None
    if not mentioned:
        return None

    # Find matching supplier log(s) by name
    matches = [
        sl for sl in all_supplier_logs
        if _names_match(mentioned, sl.get("supplier_name", ""))
    ]

    if not matches:
        return ResultFlag(
            flag_id="PREFERRED_SUPPLIER_NOT_FOUND",
            severity="info",
            description=(
                f"Requester specified '{mentioned}' as preferred supplier, "
                f"but no matching supplier was found in the evaluated pool. "
                f"The supplier may not serve this category or delivery country."
            ),
            details={"mentioned": mentioned},
        )

    # Use the first name match (names should be unique)
    sl = matches[0]
    supplier_name = sl.get("supplier_name", mentioned)

    if sl.get("excluded"):
        reason = sl.get("exclusion_reason") or "compliance rule"
        return ResultFlag(
            flag_id="PREFERRED_SUPPLIER_EXCLUDED",
            severity="info",
            description=(
                f"Preferred supplier restricted due to: '{supplier_name}' was "
                f"explicitly requested but excluded — {reason}."
            ),
            details={"supplier_name": supplier_name, "exclusion_reason": reason},
        )

    # Supplier survived but text compliance check found issues
    text_score = sl.get("text_compliance_score")
    if text_score is not None and text_score >= PREFERRED_TEXT_COMPLIANCE_THRESHOLD:
        # Score is fine — no flag needed
        return None

    if text_score is not None and text_score < PREFERRED_TEXT_COMPLIANCE_THRESHOLD:
        return ResultFlag(
            flag_id="PREFERRED_SUPPLIER_COMPLIANCE_CONCERN",
            severity="info",
            description=(
                f"Preferred supplier restricted due to: '{supplier_name}' was "
                f"explicitly requested but the text compliance check flagged a concern "
                f"(text_compliance_score={text_score:.2f}). "
                f"The request text may contain conflicting or limiting conditions."
            ),
            details={"supplier_name": supplier_name, "text_compliance_score": text_score},
        )

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate_flags(
    request:           dict[str, Any],
    supplier_results:  list[tuple],    # (identity, rank, final_state), sorted DESC
    n_total_suppliers: int,            # total candidates before exclusions
    n_excluded:        int,            # suppliers excluded by hard gates
    all_supplier_logs: list[dict] | None = None,  # all suppliers incl. excluded
) -> FlagAssessment:
    """
    Run all flag assessors against the evaluation outcome.

    Parameters
    ----------
    request:
        The normalised request dict.
    supplier_results:
        Surviving (non-excluded) suppliers as list of (identity, rank, final_state),
        already sorted by normalized_rank DESC.
    n_total_suppliers:
        Total number of candidate suppliers before any exclusions.
    n_excluded:
        Number of suppliers removed by hard compliance gates.
    all_supplier_logs:
        Optional list of dicts covering ALL suppliers (including excluded ones),
        each with keys: supplier_name, supplier_id, excluded, exclusion_reason,
        normalized_rank, text_compliance_score. Used for preferred supplier flags.

    Returns
    -------
    FlagAssessment with all fired flags, sorted warning-first.
    """
    budget = float(request.get("budget") or 0)
    logs = all_supplier_logs or []

    flags: list[ResultFlag] = []

    # Assessors — order doesn't affect output (all independent)
    candidates: list[ResultFlag | None] = [
        _flag_zero_quantity(request),
        _flag_single_qualified_supplier(supplier_results),
        _flag_budget_insufficient(supplier_results, budget),
        _flag_low_rank_cluster(supplier_results),
        _flag_indistinguishable_ranks(supplier_results),
        _flag_dominant_supplier(supplier_results),
        _flag_all_compliance_penalized(supplier_results),
        _flag_high_exclusion_rate(n_total_suppliers, n_excluded),
        _flag_preferred_supplier_restricted(request, supplier_results, logs),
    ]

    for f in candidates:
        if f is not None:
            flags.append(f)

    # Sort: warnings before info
    flags.sort(key=lambda f: (f.severity != "warning", f.flag_id))

    assessment = FlagAssessment(flags=flags)
    assessment.has_warnings = any(f.severity == "warning" for f in flags)
    return assessment
