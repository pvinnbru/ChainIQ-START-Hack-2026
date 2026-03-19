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
    The gap between position #1 and position #2 is < 0.05. The top choice
    is not meaningfully better than the runner-up; minor price fluctuations
    would flip the order. Requires ≥ 2 surviving suppliers.

NARROW_RANK_SPREAD
    ≥ 3 surviving suppliers whose full score range (position #1 minus last)
    is < 0.10. The scoring cannot meaningfully differentiate the field —
    all suppliers are effectively equivalent from a ranking perspective.

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

PREFERRED_BONUS_DECISIVE
    The organisational preferred-supplier bonus (10% soft squash) changed who
    ranked #1.  Without the bonus the top-ranked supplier would have scored
    below the runner-up.  Requires ≥ 2 surviving suppliers.  Severity: warning
    — the procurement decision was influenced by policy preference, not merit
    alone, and should be subject to independent review.

QUANTITY_EXCEEDS_TIER_MAXIMUM
    One or more suppliers were excluded because the requested quantity exceeds
    every available pricing tier in the master pricing sheet.  The exclusion
    reduces the competitive pool; consider splitting the order or negotiating
    an off-tier quote.
"""

from __future__ import annotations

import math
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

# INDISTINGUISHABLE_RANKS: min suppliers; max gap between #1 and #2
INDISTINGUISHABLE_MIN_SUPPLIERS: int   = 2
INDISTINGUISHABLE_MAX_GAP:       float = 0.05

# NARROW_RANK_SPREAD: min suppliers; max full range (first minus last)
NARROW_RANK_SPREAD_MIN_SUPPLIERS: int   = 3
NARROW_RANK_SPREAD_THRESHOLD:     float = 0.10

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


@dataclass
class ConfidenceAssessment:
    """
    How much to trust the ranking produced for this evaluation.

    ``score`` is in [0, 1].  It is NOT the same as normalized_rank: it
    expresses *reliability of the ordering*, not which supplier is better.

    Dimensions (each 0–1, independently interpretable):
    - input_completeness:    Are quantity / budget / category all present?
    - market_coverage:       Enough competing suppliers; low exclusion rate.
    - ranking_decisiveness:  Clear gap between #1 and #2; scores not all low.
    - data_reliability:      Historical baseline is solid; z-score used.
    - compliance_quality:    Top supplier (and ideally all) cleanly pass policy.

    label:       "high" | "medium" | "low" | "very_low"
    explanation: One-sentence human-readable summary of the main limiting factor.

    Dimensions (each 0–1, independently interpretable):
    - input_completeness:    Are quantity / budget / category all present?
    - market_coverage:       Enough competing suppliers; low exclusion rate.
    - ranking_decisiveness:  Clear gap between #1 and #2; scores not all low.
    - data_reliability:      Historical baseline is solid; z-score used.
    - compliance_quality:    Top supplier (and ideally all) cleanly pass policy.
    - temporal_validity:     Request is not overdue and has sufficient lead time.
                             Overdue or same-day requests produce stale rankings
                             because supplier availability and pricing may have
                             changed since the request was created.
    """
    score:                float
    label:                str
    breakdown:            dict
    explanation:          str


# ---------------------------------------------------------------------------
# Individual flag assessors
# ---------------------------------------------------------------------------

def _flag_budget_insufficient(
    supplier_results: list[tuple],
    budget: float,
) -> ResultFlag | None:
    if budget <= 0 or not supplier_results:
        return None

    # ISSUE-013: only count suppliers where cost_total was actually computed
    # (positive, non-None value).  A zero/None cost_total means the action
    # pipeline failed to compute it — treating those as 0 would make all
    # suppliers appear to be within budget, suppressing the flag.
    valid_costs: list[float] = []
    for _, _, fs in supplier_results:
        raw = fs.get("cost_total")
        if raw is None:
            continue
        try:
            v = float(raw)
            if v > 0:
                valid_costs.append(v)
        except (TypeError, ValueError):
            pass

    if not valid_costs:
        return None  # cannot determine budget status without computed costs

    all_over = all(c > budget * (1 + BUDGET_OVERAGE_THRESHOLD) for c in valid_costs)
    if not all_over:
        return None

    min_cost = min(valid_costs)
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
    # Requires at least 2 suppliers so there is a #1/#2 gap to measure.
    if len(supplier_results) < INDISTINGUISHABLE_MIN_SUPPLIERS:
        return None

    rank_1 = float(supplier_results[0][2].get("normalized_rank") or 0)
    rank_2 = float(supplier_results[1][2].get("normalized_rank") or 0)
    gap = rank_1 - rank_2

    if gap >= INDISTINGUISHABLE_MAX_GAP:
        return None

    top_name    = supplier_results[0][0].get("supplier_name", "?")
    second_name = supplier_results[1][0].get("supplier_name", "?")
    return ResultFlag(
        flag_id="INDISTINGUISHABLE_RANKS",
        severity="warning",
        description=(
            f"The gap between #1 '{top_name}' ({rank_1:.4f}) and "
            f"#2 '{second_name}' ({rank_2:.4f}) is {gap:.4f} "
            f"(threshold: {INDISTINGUISHABLE_MAX_GAP}). "
            f"The top choice is not meaningfully better than the runner-up — "
            f"minor price or data changes would flip the order."
        ),
        details={
            "rank_1": round(rank_1, 4),
            "rank_2": round(rank_2, 4),
            "gap":    round(gap, 4),
            "top_supplier":    top_name,
            "second_supplier": second_name,
        },
    )


def _flag_narrow_rank_spread(supplier_results: list[tuple]) -> ResultFlag | None:
    # Requires at least 3 suppliers — spread across a field of 2 is already
    # captured by INDISTINGUISHABLE_RANKS.
    if len(supplier_results) < NARROW_RANK_SPREAD_MIN_SUPPLIERS:
        return None

    ranks = [float(fs.get("normalized_rank") or 0) for _, _, fs in supplier_results]
    spread = max(ranks) - min(ranks)

    if spread >= NARROW_RANK_SPREAD_THRESHOLD:
        return None

    return ResultFlag(
        flag_id="NARROW_RANK_SPREAD",
        severity="warning",
        description=(
            f"{len(ranks)} suppliers span only {spread:.4f} rank points "
            f"(threshold: {NARROW_RANK_SPREAD_THRESHOLD}). "
            f"The scoring cannot meaningfully differentiate this supplier field — "
            f"all quotes are effectively equivalent."
        ),
        details={
            "n_suppliers": len(ranks),
            "rank_spread": round(spread, 4),
            "rank_max":    round(max(ranks), 4),
            "rank_min":    round(min(ranks), 4),
        },
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


# ISSUE-008: minimum character length to guard against false positives from
# short or common name fragments (e.g. "tech" matching "supertech").
_MIN_MATCH_LEN: int = 4


def _names_match(mentioned: str, candidate: str) -> bool:
    """Case-insensitive substring match: *mentioned* must appear in *candidate*.

    Uses a unidirectional check (mentioned ⊆ candidate) to prevent spurious
    matches where a short candidate name happens to be a substring of the
    mentioned name.  Very short mentions (< _MIN_MATCH_LEN chars) require an
    exact match to prevent single-character or common-word false positives.
    """
    a = _normalize_name(mentioned)
    b = _normalize_name(candidate)
    if len(a) < _MIN_MATCH_LEN:
        return a == b  # require exact match for very short names
    return a in b


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

    # ISSUE-010: check ALL matches — if any matching supplier survived evaluation,
    # the preferred supplier was not actually excluded.  Only report EXCLUDED when
    # every matching supplier was excluded (e.g. two subsidiaries both failed gates).
    surviving_matches = [s for s in matches if not s.get("excluded")]
    if surviving_matches:
        # At least one match is active — use the first surviving one for further checks
        sl = surviving_matches[0]
    else:
        # All matches were excluded — report the first excluded match
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
# Additional flag assessors
# ---------------------------------------------------------------------------

def _flag_quantity_exceeds_tier(all_supplier_logs: list[dict]) -> ResultFlag | None:
    """ISSUE-011: Fire when suppliers were excluded because the order quantity exceeds
    every available pricing tier.  Without this flag the requester only sees
    INSUFFICIENT_SUPPLIERS with no explanation of the root cause."""
    tier_exceeded = [
        sl for sl in all_supplier_logs
        if sl.get("excluded")
        and "quantity exceeds all available pricing tiers" in (sl.get("exclusion_reason") or "")
    ]
    if not tier_exceeded:
        return None
    return ResultFlag(
        flag_id="QUANTITY_EXCEEDS_TIER_MAXIMUM",
        severity="warning",
        description=(
            f"{len(tier_exceeded)} supplier(s) were excluded because the requested quantity "
            f"exceeds all available pricing tiers. Consider reducing the order size or "
            f"splitting the order across multiple requests."
        ),
        details={"n_excluded": len(tier_exceeded)},
    )


def _flag_preferred_bonus_decisive(supplier_results: list[tuple]) -> ResultFlag | None:
    """ISSUE-021: Fire when the preferred-supplier 10% bonus was the deciding factor
    in placing a supplier at rank #1 — i.e. on a level playing field (no bonuses
    applied to either supplier) they would have ranked below the runner-up.

    Comparison logic
    ----------------
    #1's without-bonus score  vs.  #2's without-bonus score (when #2 also received
    a bonus) or #2's actual score (when #2 received no bonus).

    Using #2's with-bonus score when #2 is also preferred would cause the flag to
    over-fire: if #1 loses to #2's already-boosted score but would have beaten #2
    without any bonus, the bonus was not the decisive factor — both suppliers were
    treated equally and #1 still emerges on top when both are stripped of their
    bonuses.
    """
    if len(supplier_results) < 2:
        return None

    top_identity, _, top_state   = supplier_results[0]
    _,            _, second_state = supplier_results[1]

    if not top_state.get("preferred_supplier_bonus_applied"):
        return None

    rank_with    = float(top_state.get("normalized_rank") or 0)
    rank_without = float(top_state.get("rank_without_preferred_bonus") or rank_with)

    # Use #2's without-bonus score when it also received the bonus so the
    # comparison is symmetric: both suppliers evaluated without any preference lift.
    second_rank_actual   = float(second_state.get("normalized_rank") or 0)
    second_rank_no_bonus = float(
        second_state.get("rank_without_preferred_bonus") or second_rank_actual
    )

    if rank_without >= second_rank_no_bonus:
        return None  # #1 would have ranked first regardless of the bonus

    gap = round(second_rank_no_bonus - rank_without, 4)
    return ResultFlag(
        flag_id="PREFERRED_BONUS_DECISIVE",
        severity="warning",
        description=(
            f"The organisational preferred-supplier bonus changed the ranking outcome. "
            f"'{top_identity.get('supplier_name', '?')}' scored {rank_without:.4f} on merit "
            f"but {rank_with:.4f} after the bonus — {gap:.4f} points below the runner-up "
            f"({second_rank_no_bonus:.4f}) on a level playing field. "
            f"The #1 position was determined by policy preference, not merit alone. "
            f"Independent review of the award decision is recommended."
        ),
        details={
            "top_supplier":            top_identity.get("supplier_name"),
            "rank_with_bonus":         rank_with,
            "rank_without_bonus":      rank_without,
            "runner_up_rank_no_bonus": second_rank_no_bonus,
            "merit_gap":               gap,
        },
    )


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

def compute_confidence_score(
    request:           dict[str, Any],
    supplier_results:  list[tuple],   # (identity, rank, final_state), sorted DESC
    n_total_suppliers: int,           # category-matched candidates (before exclusions)
    n_excluded:        int,           # excluded by hard compliance gates
    hist_n_data_points: int | None,  # number of historical awards for this category
    hist_std_dev:       float | None, # std dev of historical unit prices (None = no data)
    hist_avg:           float | None, # historical average unit price (None = no data)
) -> ConfidenceAssessment:
    """
    Compute a 0–1 confidence score expressing how much to trust the ranking.

    Parameters
    ----------
    request:
        The normalised request dict (same as passed to evaluate_flags).
    supplier_results:
        Surviving suppliers sorted by normalized_rank DESC.
    n_total_suppliers:
        Category-matched candidate count before compliance exclusions.
    n_excluded:
        Number excluded by hard compliance gates.
    hist_n_data_points:
        How many historical awards exist for this category pair. None / 0 means
        the cost score used the fallback ratio instead of the z-score sigmoid.
    hist_std_dev:
        Standard deviation of historical unit prices. None / 0 means no variance
        data — z-score is unstable or the fallback formula was used.
    hist_avg:
        Historical average unit price. Used to gauge whether the std_dev is
        reasonable relative to the market (coefficient of variation check).

    Returns
    -------
    ConfidenceAssessment with ``score``, ``label``, ``breakdown``, ``explanation``.
    """

    # ------------------------------------------------------------------
    # Dimension 1 — Input Completeness (weight 0.25)
    # ------------------------------------------------------------------
    # Cost scoring relies on quantity (cost_total = qty * unit_price) and
    # budget (penalty function).  Missing either degrades ranking quality.
    # Category is needed for historical lookup and supplier filtering.

    qty = request.get("quantity")
    try:
        qty_val = float(qty) if qty is not None else 0.0
    except (TypeError, ValueError):
        qty_val = 0.0

    budget = float(request.get("budget") or 0)
    category_l2 = request.get("category_l2")

    quantity_ok  = 1.0 if qty_val > 0 else 0.0  # most impactful: cost_total breaks
    budget_ok    = 1.0 if budget > 0 else 0.0
    category_ok  = 1.0 if category_l2 else 0.0

    input_completeness = 0.50 * quantity_ok + 0.30 * budget_ok + 0.20 * category_ok

    # ------------------------------------------------------------------
    # Dimension 2 — Market Coverage (weight 0.25)
    # ------------------------------------------------------------------
    # Confidence in the *ranking* is only meaningful when multiple suppliers
    # competed.  A high exclusion rate also signals the pool is mis-matched.

    n_surviving = len(supplier_results)

    # Monotone step function: more survivors → higher coverage factor
    _count_table = {0: 0.00, 1: 0.20, 2: 0.50, 3: 0.72, 4: 0.86}
    count_score = _count_table.get(n_surviving, 1.0)  # 5+ → 1.0

    # Exclusion rate: 0% excluded → 1.0, 100% excluded → 0.0
    if n_total_suppliers > 0:
        excl_fraction = min(1.0, n_excluded / n_total_suppliers)
    else:
        excl_fraction = 0.0
    exclusion_factor = 1.0 - excl_fraction

    market_coverage = 0.70 * count_score + 0.30 * exclusion_factor

    # ------------------------------------------------------------------
    # Dimension 3 — Ranking Decisiveness (weight 0.25)
    # ------------------------------------------------------------------
    # A ranking is most valuable when #1 leads #2 by a comfortable margin
    # and the absolute score level is not uniformly poor.

    if n_surviving == 0:
        ranking_decisiveness = 0.0
    elif n_surviving == 1:
        # Single supplier: winner by default, no competitive signal
        rank_1 = float(supplier_results[0][2].get("normalized_rank") or 0)
        ranking_decisiveness = 0.30 * min(1.0, rank_1 / 0.50)
    else:
        rank_1 = float(supplier_results[0][2].get("normalized_rank") or 0)
        rank_2 = float(supplier_results[1][2].get("normalized_rank") or 0)
        gap    = rank_1 - rank_2

        # Gap score: a 0.15 gap = full confidence; sub-0.05 gaps are near-ties
        gap_score = min(1.0, gap / 0.15)

        # Level score: if even the winner scores below 0.50 the category is
        # likely budget-constrained or compliance-restricted — less trustworthy
        level_score = min(1.0, rank_1 / 0.50)

        ranking_decisiveness = 0.60 * gap_score + 0.40 * level_score

    # ------------------------------------------------------------------
    # Dimension 4 — Data Reliability (weight 0.15)
    # ------------------------------------------------------------------
    # The z-score sigmoid is the preferred cost scoring method.  It requires
    # a historical average AND a non-zero std_dev.  When either is missing the
    # system falls back to a simple ratio (blended_avg / unit_price), which is
    # less discriminating.  More data points → more stable baseline.

    n_hist = hist_n_data_points or 0

    if n_hist == 0:
        hist_factor = 0.25   # fallback ratio used — low reliability
    elif n_hist < 5:
        hist_factor = 0.25 + 0.08 * n_hist          # 0.33 … 0.57
    elif n_hist < 15:
        hist_factor = 0.57 + 0.03 * (n_hist - 5)   # 0.57 … 0.87
    elif n_hist < 30:
        hist_factor = 0.87 + 0.008 * (n_hist - 15) # 0.87 … 0.99
    else:
        hist_factor = min(1.0, 0.99 + 0.002 * (n_hist - 30))

    # Std-dev stability check: coefficient of variation (CV) outside a
    # reasonable range makes z-scores noisy (very high CV) or degenerate
    # (very low CV — tiny price differences flip rankings).
    if hist_std_dev and hist_std_dev > 0 and hist_avg and hist_avg > 0:
        cv = hist_std_dev / hist_avg
        if 0.02 <= cv <= 0.50:
            std_factor = 1.0   # healthy variance
        elif cv > 0.50:
            std_factor = 0.75  # high variance → z-score noisy
        else:
            std_factor = 0.70  # near-zero variance → scores unstable
    else:
        std_factor = 0.65  # no std_dev → fallback formula used

    data_reliability = hist_factor * std_factor

    # ------------------------------------------------------------------
    # Dimension 5 — Compliance Quality (weight 0.10)
    # ------------------------------------------------------------------
    # If the top supplier was compliance-penalized, our confidence in
    # recommending it should be lower.  Systemic penalization (all suppliers
    # penalized) is an additional signal that the policy may be misconfigured.

    if not supplier_results:
        compliance_quality = 0.0
    else:
        top_compliance = max(0.0, min(1.0, float(
            supplier_results[0][2].get("compliance_score") or 1.0
        )))

        all_penalized = all(
            float(fs.get("compliance_score") or 1.0) < 1.0
            for _, _, fs in supplier_results
        )

        if all_penalized:
            # Average compliance of the pool, further discounted for systemic issues
            avg_compliance = sum(
                float(fs.get("compliance_score") or 1.0)
                for _, _, fs in supplier_results
            ) / len(supplier_results)
            compliance_quality = 0.70 * avg_compliance
        else:
            compliance_quality = top_compliance

    # ------------------------------------------------------------------
    # Dimension 6 — Temporal Validity (weight 0.10)
    # ------------------------------------------------------------------
    # An overdue request is effectively stale: delivery has already been missed,
    # supplier availability and spot pricing may have changed, and any lead-time
    # comparison in the ranking is meaningless.  Near-deadline requests also
    # carry elevated uncertainty because few suppliers can respond in time.
    #
    # Scoring table (days_until_required):
    #   None / missing → 1.00  (unknown delivery date — no penalty)
    #   > 7 days       → 1.00  (comfortable window — full confidence)
    #   4–7 days       → 0.80  (tight but likely achievable)
    #   1–3 days       → 0.60  (very urgent; most lead times unmet)
    #   0 days         → 0.40  (on-deadline or clipped past date)
    #   < 0 days       → linear decay from 0.40 at days=0 to 0.15 at days=-14,
    #                    floor at 0.15 for any further overdue
    #
    # The floor (0.15) reflects that some signal remains in the ranking even for
    # badly overdue requests (supplier pool validity, price benchmarks), but the
    # temporal context is no longer reliable for sourcing decisions.

    _TV_TIGHT_DAYS          = 7    # days ≤ this: tight window
    _TV_URGENT_DAYS         = 3    # days ≤ this: very urgent
    _TV_OVERDUE_BASE_SCORE  = 0.40 # score at days = 0 (on-deadline / clipped)
    _TV_OVERDUE_SEVERE_DAYS = 14   # days overdue at which the floor is reached
    _TV_OVERDUE_FLOOR_SCORE = 0.15 # minimum score for any overdue request

    days_raw = request.get("days_until_required")
    try:
        days_val: float | None = float(days_raw) if days_raw is not None else None
    except (TypeError, ValueError):
        days_val = None

    if days_val is None:
        temporal_validity = 1.00
    elif days_val > _TV_TIGHT_DAYS:
        temporal_validity = 1.00
    elif days_val > _TV_URGENT_DAYS:
        temporal_validity = 0.80
    elif days_val > 0:
        temporal_validity = 0.60
    elif days_val == 0:
        temporal_validity = _TV_OVERDUE_BASE_SCORE
    else:
        # days_val < 0: linear decay from _TV_OVERDUE_BASE_SCORE at 0 to
        # _TV_OVERDUE_FLOOR_SCORE at -_TV_OVERDUE_SEVERE_DAYS, then flat floor.
        decay = ((_TV_OVERDUE_BASE_SCORE - _TV_OVERDUE_FLOOR_SCORE)
                 / _TV_OVERDUE_SEVERE_DAYS)
        temporal_validity = max(
            _TV_OVERDUE_FLOOR_SCORE,
            _TV_OVERDUE_BASE_SCORE + days_val * decay,
        )

    # ------------------------------------------------------------------
    # Composite score + label
    # ------------------------------------------------------------------
    # Weights sum to 1.0.  input_completeness and market_coverage each
    # contributed 0.025 to fund the new temporal_validity dimension (0.10),
    # keeping all other dimension weights unchanged.
    WEIGHTS = {
        "input_completeness":   0.20,
        "market_coverage":      0.20,
        "ranking_decisiveness": 0.25,
        "data_reliability":     0.15,
        "compliance_quality":   0.10,
        "temporal_validity":    0.10,
    }
    dimensions = {
        "input_completeness":   round(input_completeness,   4),
        "market_coverage":      round(market_coverage,      4),
        "ranking_decisiveness": round(ranking_decisiveness, 4),
        "data_reliability":     round(data_reliability,     4),
        "compliance_quality":   round(compliance_quality,   4),
        "temporal_validity":    round(temporal_validity,    4),
    }
    score = sum(WEIGHTS[k] * dimensions[k] for k in WEIGHTS)
    score = round(min(1.0, max(0.0, score)), 4)

    if score >= 0.75:
        label = "high"
    elif score >= 0.50:
        label = "medium"
    elif score >= 0.25:
        label = "low"
    else:
        label = "very_low"

    # ------------------------------------------------------------------
    # One-line explanation: surface the weakest dimension
    # ------------------------------------------------------------------
    # Use raw (un-weighted) scores so a low-weight dimension that scores 1.0
    # is not flagged as the "worst" just because its weight is small.
    worst_dim = min(dimensions, key=dimensions.get)  # type: ignore[arg-type]

    _explanations = {
        "input_completeness": (
            "Request is missing quantity, budget, or category — cost-based "
            "ranking is unreliable."
        ),
        "market_coverage": (
            "Too few suppliers qualified or the exclusion rate is high — "
            "competitive pricing cannot be guaranteed."
        ),
        "ranking_decisiveness": (
            "Suppliers' scores are tightly clustered or uniformly low — "
            "the ranking adds little decision value."
        ),
        "data_reliability": (
            "Insufficient historical pricing data for this category — "
            "the cost score uses a less accurate fallback formula."
        ),
        "compliance_quality": (
            "The top-ranked supplier carries a compliance penalty — "
            "manual review of policy fit is recommended."
        ),
        "temporal_validity": (
            "The request delivery date has passed or is imminent — supplier "
            "quotes and lead-time commitments may no longer be valid."
        ),
    }
    explanation = _explanations[worst_dim]

    return ConfidenceAssessment(
        score=score,
        label=label,
        breakdown={
            "dimensions": dimensions,
            "weights":    WEIGHTS,
            "worst_dimension": worst_dim,
            "meta": {
                "n_surviving_suppliers":   n_surviving,
                "n_excluded":              n_excluded,
                "n_hist_data_points":      n_hist,
                "hist_std_dev_available":  hist_std_dev is not None and hist_std_dev > 0,
                "used_zscore_sigmoid":     bool(hist_std_dev and hist_std_dev > 0),
                "days_until_required":     days_val,
            },
        },
        explanation=explanation,
    )


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
        _flag_narrow_rank_spread(supplier_results),
        _flag_dominant_supplier(supplier_results),
        _flag_all_compliance_penalized(supplier_results),
        _flag_high_exclusion_rate(n_total_suppliers, n_excluded),
        _flag_preferred_supplier_restricted(request, supplier_results, logs),
        _flag_quantity_exceeds_tier(logs),
        _flag_preferred_bonus_decisive(supplier_results),
    ]

    # ISSUE-018: suppress LOW_RANK_CLUSTER when a more specific spread flag fired.
    #
    # NARROW_RANK_SPREAD (spread < 0.10) already tells reviewers the field is
    # undifferentiated; LOW_RANK_CLUSTER (spread < 0.10 AND all ranks < 0.30)
    # would repeat the same spread signal with no net new information.
    #
    # INDISTINGUISHABLE_RANKS now fires on the #1/#2 gap (< 0.05) rather than
    # the full spread, so it no longer implies anything about LOW_RANK_CLUSTER's
    # spread condition — keep both when only INDISTINGUISHABLE_RANKS fires.
    fired_ids = {f.flag_id for f in candidates if f is not None}
    if "NARROW_RANK_SPREAD" in fired_ids:
        candidates = [f for f in candidates if f is None or f.flag_id != "LOW_RANK_CLUSTER"]

    for f in candidates:
        if f is not None:
            flags.append(f)

    # Sort: warnings before info
    flags.sort(key=lambda f: (f.severity != "warning", f.flag_id))

    assessment = FlagAssessment(flags=flags)
    assessment.has_warnings = any(f.severity == "warning" for f in flags)
    return assessment
