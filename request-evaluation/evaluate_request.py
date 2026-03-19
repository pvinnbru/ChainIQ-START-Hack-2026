"""
evaluate_request.py — Single-function entry point for the ChainIQ procurement pipeline.

Accepts a JSON string already normalized to the pipeline's request format (see INPUT FORMAT
below) and returns a JSON string containing all evaluation outputs plus the full execution
log (see OUTPUT FORMAT at the bottom of this file).

The pipeline (schema, suppliers, pricing, actions) is initialized once and cached at module
level; subsequent calls reuse it without reloading from disk.

==========================================================================================
INPUT FORMAT
==========================================================================================

The input JSON must be a single object with the following fields.  Field types and
semantics match start_dict.csv exactly.

REQUIRED fix_in fields (request-level — supplier-matrix fields are loaded from data/):
----------
  request_id                  string   Unique request identifier, e.g. "REQ-000042"
  category_l1                 string   L1 procurement category
                                       Allowed values: "IT" | "Facilities" |
                                       "Professional Services" | "Marketing"
  category_l2                 string   L2 procurement subcategory, e.g. "Laptops",
                                       "Cloud Compute", "Office Chairs"
  budget                      number   Total budget amount available (in the stated currency)
  currency                    string   Budget currency: "EUR" | "CHF" | "USD"
  quantity                    number   Number of units or service days requested
  amount_unit                 string   Unit of measurement for quantity, e.g.
                                       "devices" | "hours" | "days"
  delivery_country            string   Target delivery / service country as ISO-2 code,
                                       e.g. "DE" | "CH" | "US"
  days_until_required         integer  Calendar days from today until required delivery or
                                       service start date.  Must be pre-computed from
                                       required_by_date before calling this function.
                                       Use 0 for overdue / immediate requests.
  preferred_supplier_mentioned string | null  Supplier name explicitly stated by the
                                       requester; null if none mentioned
  incumbent_supplier           string | null  Current incumbent supplier for this
                                       category and requester; null if none
  data_residency_constraint    boolean  true if the request involves data that must remain
                                       within a specific jurisdiction
  esg_requirement              boolean  true if the requester has stated ESG or
                                       sustainability requirements

OPTIONAL meta fields (not used in rule logic but passed through to the log):
----------
  request_text                string   Free-text request description; used by the text
                                       compliance module when present

Example minimal input:
{
  "request_id": "REQ-000099",
  "category_l1": "IT",
  "category_l2": "Laptops",
  "budget": 50000,
  "currency": "EUR",
  "quantity": 100,
  "amount_unit": "devices",
  "delivery_country": "DE",
  "days_until_required": 14,
  "preferred_supplier_mentioned": null,
  "incumbent_supplier": null,
  "data_residency_constraint": false,
  "esg_requirement": false
}
==========================================================================================
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file so the function works regardless of cwd)
# ---------------------------------------------------------------------------

_ROOT          = Path(__file__).parent.parent  # project root (data/, stores/ live here)
_HERE          = Path(__file__).parent         # request-evaluation/ (start_dict.csv lives here)
_DATA_DIR      = _ROOT / "data"
_STORE_DIR     = _ROOT / "stores"
_SCHEMA_PATH   = _HERE / "start_dict.csv"
_SUPPLIERS_PATH = _DATA_DIR / "suppliers.csv"
_PRICING_PATH  = _DATA_DIR / "pricing.csv"
_RANKING_STORE_PATH = _STORE_DIR / "ranking_actions.json"

# ---------------------------------------------------------------------------
# Module-level pipeline cache — initialized on first call, reused thereafter
# ---------------------------------------------------------------------------

_pipeline: dict | None = None


def _strip_quotes(actions: list[tuple]) -> list[tuple]:
    def _unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s
    return [tuple(_unquote(str(f)) for f in action) for action in actions]


def _load_ranking_store_cached(schema: list[tuple], data_hash: str) -> tuple[list[tuple], dict]:
    """Load ranking actions from the JSON store.  Triggers an LLM rebuild on hash mismatch."""
    if _RANKING_STORE_PATH.exists():
        with open(_RANKING_STORE_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        if raw.get("data_hash") == data_hash:
            actions = _strip_quotes([tuple(a) for a in raw["ranking_actions"]])
            attribution = {int(k): v for k, v in raw.get("attribution", {}).items()}
            return actions, attribution

    # Cache miss — rebuild via LLM (requires AZURE_OPENAI_* env vars)
    from openai import AzureOpenAI
    from supplier_matrix import generate_ranking_actions

    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )
    actions_raw, attribution = generate_ranking_actions(schema, client)
    actions = _strip_quotes(actions_raw)

    _RANKING_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_hash": data_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ranking_actions": [list(a) for a in actions],
        "attribution": {str(k): v for k, v in attribution.items()},
    }
    with open(_RANKING_STORE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    return actions, attribution


def _build_pipeline() -> dict:
    """Initialize and cache the full evaluation pipeline once."""
    from actions_store import get_or_build_actions_store, hash_data_folder
    from supplier_matrix import (
        add_ranking_schema_entries,
        build_full_action_pipeline,
        load_pricing_index,
        load_schema,
        load_suppliers,
    )

    schema, fix_in_keys = load_schema(str(_SCHEMA_PATH))
    schema = add_ranking_schema_entries(schema)

    data_hash = hash_data_folder(_DATA_DIR)

    approval_store   = get_or_build_actions_store("approval_thresholds")
    category_store   = get_or_build_actions_store("category_rules")
    escalation_store = get_or_build_actions_store("escalation_rules")

    rules_actions = (
        list(approval_store["sorted_actions"])
        + list(category_store["sorted_actions"])
        + list(escalation_store["sorted_actions"])
    )

    rules_attribution: dict = {}
    offset = 0
    for store in (approval_store, category_store, escalation_store):
        for k, v in store.get("attribution", {}).items():
            rules_attribution[int(k) + offset] = v
        offset += len(store["sorted_actions"])

    ranking_actions, ranking_attribution = _load_ranking_store_cached(schema, data_hash)

    sorted_actions, is_low_confidence, combined_attribution = build_full_action_pipeline(
        ranking_actions, rules_actions, fix_in_keys,
        ranking_attribution=ranking_attribution,
        rules_attribution=rules_attribution,
    )

    suppliers     = load_suppliers(str(_SUPPLIERS_PATH), [])
    pricing_index = load_pricing_index(str(_PRICING_PATH))

    from escalation_engine import build_field_impact_map
    field_impact_map = build_field_impact_map(sorted_actions, fix_in_keys)

    return {
        "schema":            schema,
        "fix_in_keys":       fix_in_keys,
        "sorted_actions":    sorted_actions,
        "is_low_confidence": is_low_confidence,
        "attribution":       combined_attribution,
        "suppliers":         suppliers,
        "pricing_index":     pricing_index,
        "field_impact_map":  field_impact_map,
        "escalation_rules":  escalation_store.get("raw_rules", []),
    }


def _get_pipeline() -> dict:
    global _pipeline
    if _pipeline is None:
        _pipeline = _build_pipeline()
    return _pipeline


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------

def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses, tuples, and sets to JSON-safe types."""
    import dataclasses
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_serializable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, tuple):
        return [_to_serializable(x) for x in obj]
    if isinstance(obj, (list,)):
        return [_to_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, set):
        return sorted(_to_serializable(x) for x in obj)
    return obj


# ---------------------------------------------------------------------------
# Rank explanation builder
# ---------------------------------------------------------------------------

def _build_rank_explanation(pos: int, fs: dict, request: dict | None = None) -> str:
    """
    Build a plain-English explanation of a supplier's rank position by
    interpreting the key scoring signals stored in *fs* (final_state).

    The explanation is ordered by signal weight / impact:
      1. Cost position vs. market average      (80% of raw_rank)
      2. Budget penalty                        (explicitly shown for any non-trivial case)
      3. Preferred supplier bonus              (with decisiveness language when it matters)
      4. Compliance penalty                    (multiplicative on rank)
      5. Reputation                            (10% of raw_rank, only if notable)
      6. Historical track record               (10% of raw_rank; absent data called out)
      7. Overdue request                       (sourced from request.days_until_required)
      8. Low-confidence warning                (action evaluation errors)

    Parameters
    ----------
    pos:
        0-based rank position (0 = best).
    fs:
        The supplier's final_state dict from run_procurement_evaluation.
    request:
        The original request dict; used to surface days_until_required < 0.

    Returns a string of the form:
        "Ranked #N: <clause>, <clause>, ...."
    """
    clauses: list[str] = []

    # --- 1. Cost position (vs. blended market average) ---
    try:
        up  = float(fs["unit_price"])             if fs.get("unit_price")             is not None else None
        avg = float(fs["blended_avg_unit_price"])  if fs.get("blended_avg_unit_price")  is not None else None
    except (TypeError, ValueError):
        up = avg = None

    if up and avg and avg > 0:
        pct = (avg - up) / avg * 100
        if pct >= 5.0:
            clauses.append(f"strong cost position ({pct:.0f}% below market avg)")
        elif pct >= 1.0:
            clauses.append(f"{pct:.0f}% below market avg")
        elif pct > -1.0:
            clauses.append("at market average")
        elif pct > -5.0:
            clauses.append(f"{abs(pct):.0f}% above market avg")
        else:
            clauses.append(f"expensive relative to market ({abs(pct):.0f}% above market avg)")
    else:
        clauses.append("no market benchmark available")

    # --- 2. Budget penalty ---
    # The logistic curve produces bp = 0.85 for a supplier priced exactly at
    # budget, rising toward 1.0 for prices well below budget.  Any bp below
    # the at-budget baseline (≈ 0.87 after the curve levels off) means the
    # supplier's total cost is pressing against or exceeding the budget and the
    # penalty is actively dragging the cost_score down.
    try:
        bp = float(fs["budget_penalty"]) if fs.get("budget_penalty") is not None else None
    except (TypeError, ValueError):
        bp = None

    if bp is not None:
        if bp < 0.30:
            clauses.append("significantly over budget (heavy penalty applied)")
        elif bp < 0.55:
            clauses.append("over budget (penalty applied)")
        elif bp < 0.82:
            clauses.append("slightly over budget (penalty applied)")
        elif bp < 0.87:
            # Supplier is right at the budget limit; the logistic penalty is at
            # its inflection point (≈ 0.85) and is explicitly affecting the score.
            clauses.append("at budget limit (logistic penalty applied)")
        # bp ≥ 0.87: supplier is comfortably within budget — omit for conciseness

    # --- 3. Preferred supplier bonus ---
    # When rank_without_preferred_bonus is available, quantify the bonus delta.
    # Mark the bonus "decisive" when the supplier is #1 and the delta alone
    # (≥ 0.03 pts) would plausibly have changed the winner.
    if fs.get("preferred_supplier_bonus_applied"):
        try:
            rank_now    = float(fs["normalized_rank"])            if fs.get("normalized_rank")            is not None else None
            rank_before = float(fs["rank_without_preferred_bonus"]) if fs.get("rank_without_preferred_bonus") is not None else None
        except (TypeError, ValueError):
            rank_now = rank_before = None

        if rank_now is not None and rank_before is not None:
            delta = round(rank_now - rank_before, 3)
            if pos == 0 and delta >= 0.03:
                clauses.append(
                    f"preferred supplier bonus decisive for top ranking "
                    f"(+{delta:.3f} pts; score without bonus: {rank_before:.3f})"
                )
            else:
                clauses.append(
                    f"preferred supplier bonus applied (+{delta:.3f} pts)"
                )
        else:
            clauses.append("preferred supplier bonus applied")

    # --- 4. Compliance penalty ---
    try:
        cs = float(fs["compliance_score"]) if fs.get("compliance_score") is not None else None
    except (TypeError, ValueError):
        cs = None

    if cs is not None and cs < 1.0:
        clauses.append(f"compliance penalty ({cs:.0%} score)")

    # --- 5. Reputation (only mention if notably high or low) ---
    try:
        rep = float(fs["reputation_score"]) if fs.get("reputation_score") is not None else None
    except (TypeError, ValueError):
        rep = None

    if rep is not None:
        if rep >= 75:
            clauses.append(f"strong reputation ({rep:.0f}/100)")
        elif rep < 40:
            clauses.append(f"below-average reputation ({rep:.0f}/100)")

    # --- 6. Historical track record ---
    # _historic_score_is_dummy=True means no store entry exists for this
    # (supplier, category) pair — the score is the neutral prior 0.5 by
    # default, not a computed value from real awards data.
    # Values outside [0.45, 0.55] with is_dummy=False reflect actual history.
    try:
        hs = float(fs["_historic_score"]) if fs.get("_historic_score") is not None else None
    except (TypeError, ValueError):
        hs = None

    if fs.get("_historic_score_is_dummy"):
        clauses.append("no historical data (scored as neutral)")
    elif hs is not None:
        if hs >= 0.65:
            clauses.append("strong historical track record")
        elif hs <= 0.35:
            clauses.append("weak historical track record")
        elif 0.45 <= hs <= 0.55:
            clauses.append("limited historical data (score near neutral prior)")

    # --- 7. Overdue request ---
    # days_until_required < 0 means the delivery date has already passed.
    # Surface this in every supplier explanation so the reader knows the
    # ranking was produced under urgency constraints.
    if request is not None:
        try:
            days = request.get("days_until_required")
            if days is not None and float(days) < 0:
                clauses.append(f"request {abs(int(days))} day(s) overdue")
        except (TypeError, ValueError):
            pass

    # --- 8. Low-confidence warning ---
    if fs.get("is_low_confidence"):
        clauses.append("some rule evaluation errors occurred")

    return f"Ranked #{pos + 1}: {', '.join(clauses)}."


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_ALLOWED_CURRENCIES: frozenset[str] = frozenset({"EUR", "CHF", "USD"})

# days_until_required: values above this are non-blocking but flagged as
# likely data-entry mistakes (a year-plus planning horizon is unusual).
_DAYS_WARNING_THRESHOLD: int = 365


def _validate_request(
    request: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Validate the parsed request dict against the pipeline's field contracts.

    Returns ``(errors, warnings)``:

    errors   — blocking violations; a non-empty list causes the request to be
                rejected with status='error'.  Each entry is
                ``{"field": str, "reason": str}``.

    warnings — non-blocking observations; the request still proceeds but the
                caller should surface these to the requester.  Same structure
                as errors.

    Hard checks (errors):
      - budget              must be a number > 0
      - quantity            must be a number > 0
      - category_l1 / l2   must be non-empty strings
      - currency            must be one of EUR | CHF | USD
      - days_until_required if present, must be a number (not bool, not string)

    Soft checks (warnings):
      - days_until_required > _DAYS_WARNING_THRESHOLD (365)  →  likely a
        data-entry error (hours entered as days, or wrong date arithmetic).
    """
    errors:   list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # --- budget > 0 ---
    budget = request.get("budget")
    if budget is None:
        errors.append({"field": "budget", "reason": "required field is missing"})
    elif isinstance(budget, bool) or not isinstance(budget, (int, float)):
        errors.append({"field": "budget", "reason": f"must be a number, got {type(budget).__name__!r}"})
    elif budget <= 0:
        errors.append({"field": "budget", "reason": f"must be > 0, got {budget}"})

    # --- quantity > 0 ---
    quantity = request.get("quantity")
    if quantity is None:
        errors.append({"field": "quantity", "reason": "required field is missing"})
    elif isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        errors.append({"field": "quantity", "reason": f"must be a number, got {type(quantity).__name__!r}"})
    elif quantity <= 0:
        errors.append({"field": "quantity", "reason": f"must be > 0, got {quantity}"})

    # --- category_l1 non-empty string ---
    cat_l1 = request.get("category_l1")
    if not isinstance(cat_l1, str) or not cat_l1.strip():
        errors.append({"field": "category_l1", "reason": "must be a non-empty string"})

    # --- category_l2 non-empty string ---
    cat_l2 = request.get("category_l2")
    if not isinstance(cat_l2, str) or not cat_l2.strip():
        errors.append({"field": "category_l2", "reason": "must be a non-empty string"})

    # --- currency in allowed set ---
    currency = request.get("currency")
    if not isinstance(currency, str) or currency.upper() not in _ALLOWED_CURRENCIES:
        allowed = " | ".join(sorted(_ALLOWED_CURRENCIES))
        errors.append({
            "field":  "currency",
            "reason": f"must be one of {allowed}, got {currency!r}",
        })

    # --- days_until_required: type check (hard) + range check (soft) ---
    days = request.get("days_until_required")
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, (int, float)):
            errors.append({
                "field":  "days_until_required",
                "reason": f"must be a number, got {type(days).__name__!r}",
            })
        elif days > _DAYS_WARNING_THRESHOLD:
            warnings.append({
                "field":  "days_until_required",
                "reason": (
                    f"value {days} exceeds {_DAYS_WARNING_THRESHOLD} days — "
                    f"this may be a data-entry error (e.g. hours entered as days "
                    f"or an incorrect required-by date)."
                ),
            })

    return errors, warnings


# ---------------------------------------------------------------------------
# Post-process display filters
# ---------------------------------------------------------------------------

# Suppliers whose normalized_rank is below this threshold are suppressed from output.
_RANK_DISPLAY_MIN: float = 0.05

# If the overall confidence_assessment score is below this level, all supplier
# suggestions are suppressed — the ranking is too unreliable to act on.
_CONFIDENCE_DISPLAY_MIN: float = 0.20

# A supplier whose standard_lead_time_days exceeds days_until_required by more than
# this many days is suppressed (cannot meet the required delivery window).
_DELIVERY_SLACK_DAYS: int = 1


def _apply_post_process_filters(
    ranked_suppliers: list[dict],
    supplier_results: list[tuple],
    request: dict[str, Any],
    confidence_assessment: Any | None,
) -> tuple[list[dict], list[str]]:
    """
    Apply post-process display filters to the ranked supplier list.

    Three filters are applied in order:

    1. Global confidence check (suppresses ALL suppliers):
       confidence_assessment.score < _CONFIDENCE_DISPLAY_MIN (0.20)

    2. Per-supplier rank threshold:
       normalized_rank < _RANK_DISPLAY_MIN (0.05) → suppress that supplier.

    3. Per-supplier delivery feasibility:
       standard_lead_time_days > days_until_required + _DELIVERY_SLACK_DAYS (1)
       → suppress that supplier.  Only applied when days_until_required >= 0
       (overdue requests bypass this filter because the window has already passed).

    Returns (filtered_suppliers, suppression_notes).
    Positions in the filtered list are re-numbered starting from 1.
    """
    notes: list[str] = []

    # Filter 1 — global confidence gate (all-or-nothing)
    if confidence_assessment is not None:
        conf_score = float(getattr(confidence_assessment, "score", 1.0))
        if conf_score < _CONFIDENCE_DISPLAY_MIN:
            notes.append(
                f"All suppliers suppressed: overall ranking confidence "
                f"({conf_score:.2f}) is below the minimum display threshold "
                f"({_CONFIDENCE_DISPLAY_MIN}). Human review is required before "
                f"any supplier recommendation can be issued."
            )
            return [], notes

    # Resolve delivery deadline (None or negative → skip delivery filter)
    days_raw = request.get("days_until_required")
    try:
        days_int: int | None = int(float(days_raw)) if days_raw is not None else None
    except (TypeError, ValueError):
        days_int = None

    apply_delivery_filter = days_int is not None and days_int >= 0

    filtered: list[dict] = []
    for s, (_, _, fs) in zip(ranked_suppliers, supplier_results):
        name = s.get("supplier_name", "?")

        # Filter 2 — rank score too low
        rank = s.get("normalized_rank")
        try:
            rank_f = float(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank_f = None

        if rank_f is not None and rank_f < _RANK_DISPLAY_MIN:
            notes.append(
                f"{name} suppressed: normalized_rank {rank_f:.3f} is below "
                f"minimum display threshold ({_RANK_DISPLAY_MIN})."
            )
            continue

        # Filter 3 — lead time exceeds deadline
        if apply_delivery_filter:
            lead_time = fs.get("standard_lead_time_days")
            try:
                lead_int = int(lead_time) if lead_time is not None else None
            except (TypeError, ValueError):
                lead_int = None

            if lead_int is not None and lead_int > days_int + _DELIVERY_SLACK_DAYS:
                notes.append(
                    f"{name} suppressed: standard lead time {lead_int} day(s) "
                    f"exceeds required delivery window of {days_int} day(s) "
                    f"(allowed slack: {_DELIVERY_SLACK_DAYS} day(s))."
                )
                continue

        filtered.append(s)

    # Re-number positions after filtering
    for i, s in enumerate(filtered):
        s["position"] = i + 1

    return filtered, notes


def _inject_no_supplier_escalation(
    outcome: dict[str, Any],
    filter_notes: list[str],
) -> None:
    """
    Inject a blocking EscalationRecord into the existing EscalationAssessment
    when no displayable suppliers remain after post-process filtering.

    Modifies ``outcome["escalation_assessment"]`` in place so the serialized
    output reflects the correct state.  If no assessment object exists, a
    minimal one is created.
    """
    from escalation_engine import EscalationAssessment, EscalationRecord

    assessment = outcome.get("escalation_assessment")
    if assessment is None:
        assessment = EscalationAssessment()
        outcome["escalation_assessment"] = assessment

    reason = (
        "No qualifying suppliers remain after post-process display filters were applied. "
        + (" ".join(filter_notes) if filter_notes else "")
    ).strip()

    no_supplier_record = EscalationRecord(
        person_to_escalate_to="Procurement Manager",
        reasons=[reason],
        tasks=[
            "Review the filter criteria and the original supplier pool. "
            "Either expand the approved supplier list, relax the delivery or "
            "ranking thresholds, or obtain emergency sourcing approval."
        ],
        severity="blocking",
        sources=["POST_PROCESS_NO_SUPPLIER"],
        source_types=["engine"],
    )

    # Prepend so it appears first (most critical)
    assessment.records.insert(0, no_supplier_record)
    assessment.has_blocking = True
    assessment.needs_escalation = True
    assessment.context_notes.append(
        "Post-process filters removed all suppliers — no recommendation can be issued."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_request(request_json: str) -> str:
    """
    Evaluate a single procurement request end-to-end.

    Parameters
    ----------
    request_json:
        JSON string conforming to the INPUT FORMAT documented at the top of
        this file (normalized request dict with all fix_in fields present).

    Returns
    -------
    str
        JSON string conforming to the OUTPUT FORMAT documented at the bottom
        of this file.  Always returns valid JSON; errors are captured in the
        ``status`` and ``error`` fields rather than raised as exceptions.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    request_id: str = "<unknown>"

    try:
        request: dict[str, Any] = json.loads(request_json)
        request_id = str(request.get("request_id", "<unknown>"))
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({
            "status":              "error",
            "request_id":          request_id,
            "timestamp":           timestamp,
            "error":               f"Invalid JSON input: {exc}",
            "validation_errors":   [],
            "validation_warnings": [],
            "global_outputs":        {},
            "ranked_suppliers":       [],
            "escalation":            None,
            "flag_assessment":       None,
            "confidence_assessment": None,
            "execution_log":         None,
        }, indent=2)

    validation_errors, validation_warnings = _validate_request(request)
    if validation_errors:
        fields = ", ".join(e["field"] for e in validation_errors)
        missing_field_names = ", ".join(e["field"] for e in validation_errors)
        _validation_escalation = {
            "needs_escalation": True,
            "has_blocking":     True,
            "has_advisory":     False,
            "records": [
                {
                    "person_to_escalate_to": "Procurement Manager",
                    "reasons": [
                        f"This procurement request is incomplete and cannot be evaluated. "
                        f"The following required fields are missing or invalid: {missing_field_names}."
                    ],
                    "tasks": [
                        "Return the request to the requester for completion. "
                        "All required fields (budget, quantity, category_l1, category_l2, currency) "
                        "must be provided before sourcing evaluation can proceed."
                    ],
                    "severity":      "blocking",
                    "sources":       ["input_validation"],
                    "source_types":  ["engine"],
                }
            ],
            "context_notes": [
                "Request failed input validation — no supplier evaluation was performed."
            ],
        }
        return json.dumps({
            "status":              "error",
            "request_id":          request_id,
            "timestamp":           timestamp,
            "error":               f"Request validation failed: {fields}",
            "validation_errors":   validation_errors,
            "validation_warnings": validation_warnings,
            "global_outputs":        {},
            "ranked_suppliers":       [],
            "escalation":            _validation_escalation,
            "flag_assessment":       None,
            "confidence_assessment": None,
            "execution_log":         None,
        }, indent=2)

    try:
        pipeline = _get_pipeline()

        from supplier_matrix import run_procurement_evaluation, save_log

        outcome, exec_log = run_procurement_evaluation(
            request=request,
            schema=pipeline["schema"],
            sorted_actions=pipeline["sorted_actions"],
            suppliers=pipeline["suppliers"],
            fix_in_keys=pipeline["fix_in_keys"],
            pricing_index=pipeline["pricing_index"],
            attribution=pipeline.get("attribution"),
            field_impact_map=pipeline.get("field_impact_map"),
            escalation_rules=pipeline.get("escalation_rules", []),
        )

        # Persist log to stores/execution_logs/
        logs_dir = _STORE_DIR / "execution_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        save_log(exec_log, str(logs_dir / request_id))

        # Build ranked supplier list
        supplier_results = outcome.get("supplier_results", [])
        ranked_suppliers = [
            {
                "position":        pos + 1,
                "supplier_id":     identity.get("supplier_id"),
                "supplier_name":   identity.get("supplier_name"),
                "category_l2":     identity.get("category_l2"),
                "normalized_rank": fs.get("normalized_rank"),
                "rank_explanation": _build_rank_explanation(pos, fs, request),
                "cost_total":      fs.get("cost_total"),
                "unit_price":      fs.get("unit_price"),
                "currency":        fs.get("currency"),
                "reputation_score":  fs.get("reputation_score"),
                "cost_rank_score":   fs.get("cost_rank_score"),
                "compliance_score":  fs.get("compliance_score"),
                "budget_penalty":    fs.get("budget_penalty"),
                "historic_score":    fs.get("historic_score"),
                "cost_score":        fs.get("cost_score"),
                "reputation_norm":   fs.get("reputation_norm"),
                "preferred_supplier": fs.get("preferred_supplier"),
                "preferred_supplier_bonus_applied": fs.get("preferred_supplier_bonus_applied", False),
                "rank_without_preferred_bonus":     fs.get("rank_without_preferred_bonus"),
            }
            for pos, (identity, _, fs) in enumerate(supplier_results)
        ]

        # Apply post-process display filters
        ranked_suppliers, filter_notes = _apply_post_process_filters(
            ranked_suppliers,
            supplier_results,
            request,
            outcome.get("confidence_assessment"),
        )

        # If no suppliers survive the filters, escalate and suppress recommendations.
        # Nothing else should be acted on — the escalation record is the only output.
        if not ranked_suppliers:
            _inject_no_supplier_escalation(outcome, filter_notes)

        result = {
            "status":      "ok",
            "request_id":  request_id,
            "timestamp":   exec_log.timestamp,
            "error":       None,
            "validation_errors":   [],
            "validation_warnings": validation_warnings,
            "global_outputs":    outcome.get("global_outputs", {}),
            "ranked_suppliers":  ranked_suppliers,
            "escalation":        _to_serializable(outcome.get("escalation_assessment")),
            "flag_assessment":   _to_serializable(outcome.get("flag_assessment")),
            "confidence_assessment": _to_serializable(outcome.get("confidence_assessment")),
            "execution_log":     _to_serializable(exec_log),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("evaluate_request failed for %s", request_id)
        result = {
            "status":              "error",
            "request_id":          request_id,
            "timestamp":           timestamp,
            "error":               f"{type(exc).__name__}: {exc}",
            "validation_errors":   [],
            "validation_warnings": [],
            "global_outputs":        {},
            "ranked_suppliers":       [],
            "escalation":            None,
            "flag_assessment":       None,
            "confidence_assessment": None,
            "execution_log":         None,
        }

    return json.dumps(result, indent=2, default=str)


# ==========================================================================================
# OUTPUT FORMAT
# ==========================================================================================
#
# The return value is always a JSON object with the following top-level fields:
#
# status          string   "ok" if the evaluation completed without exception,
#                          "error" if an unrecoverable error occurred.
#
# request_id      string   Echoed from the input (or "<unknown>" if parsing failed).
#
# timestamp       string   ISO-8601 UTC timestamp of when the evaluation ran,
#                          e.g. "2026-03-19T15:04:22.831792+00:00"
#
# error           string | null
#                          Present and non-null only when status = "error".
#                          Contains the exception type and message, or a
#                          summary of which fields failed validation.
#
# validation_errors  array  Always present.  Empty list on a clean run.
#                          When status = "error" due to invalid input, contains
#                          one object per violation with:
#                            field   string  Name of the failing request field
#                            reason  string  Human-readable explanation, including
#                                            the received value where safe to echo
#
# global_outputs  object   Policy-level fix_out fields aggregated across all
#                          evaluated suppliers.  Keys (all may be absent if no
#                          supplier matched the request):
#
#   min_supplier_quotes           integer   Minimum compliant quotes required (AT-001..015)
#   fast_track_eligible           boolean   Single-quote fast-track permitted (CR-003)
#   requires_security_review      boolean   Security architecture review required (CR-005)
#   requires_engineering_review   boolean   Engineering/CAD review required (CR-002)
#   requires_design_signoff       boolean   Business design sign-off required (CR-006)
#   requires_cv_review            boolean   Named consultant CVs required (CR-007)
#   requires_certification_check  boolean   Supplier certification check required (CR-008)
#   requires_brand_safety_review  boolean   Brand safety review required (CR-010/ER-007)
#   requires_performance_baseline boolean   SEM performance baseline required (CR-009)
#
#   NOTE: escalate_to_* boolean fields are no longer present in global_outputs.
#   They are converted to structured EscalationRecord objects in the ``escalation``
#   field, where each record carries person_to_escalate_to, reason_for_escalation,
#   task_for_escalation, and severity.
#
# ranked_suppliers  array   Suppliers that passed all gates, sorted by normalized_rank
#                           descending (best match first).  Each element:
#
#   position                  integer   1-based rank position
#   supplier_id               string    Unique supplier identifier
#   supplier_name             string    Supplier display name
#   category_l2               string    Matched L2 category
#   normalized_rank           number    Cross-comparable score in [0, 1]; higher = better.
#   rank_explanation          string    Plain-English summary of the key scoring drivers,
#                                       e.g. "Ranked #1: strong cost position (12% below
#                                       market avg), preferred supplier bonus applied,
#                                       limited historical data."  Clauses are omitted
#                                       when the signal is neutral or expected.
#                                       Composed of cost score (95%), reputation (2.5%),
#                                       and historic score (2.5%), multiplied by the
#                                       compliance_score penalty multiplier.
#   cost_total                number    Total estimated cost (quantity × unit_price ± rule
#                                       adjustments) in the supplier's pricing currency
#   unit_price                number    Resolved unit price from pricing tier
#   currency                  string    Pricing currency for this supplier/region
#   reputation_score          number    Composite quality/risk/ESG score (0–100)
#   cost_rank_score           number    Per-supplier inverted cost score (0–100); higher
#                                       means cheaper relative to an implicit reference
#   compliance_score          number    Multiplicative penalty in [0, 1]; 1.0 = fully
#                                       compliant, lower values indicate soft violations
#   budget_penalty            number    Exponential budget-overage penalty in [0, 1];
#                                       1.0 = within budget, 0.0 = ≥ 5% over budget
#   preferred_supplier        boolean | null  True when on the org's preferred list
#   preferred_supplier_bonus_applied  boolean  True when the 10% preferred-list boost
#                                       was applied to this supplier's normalized_rank
#   rank_without_preferred_bonus      number | null  normalized_rank before the bonus;
#                                       present only when the bonus was applied
#
# escalation  object | null   Unified escalation decision (null when no field_impact_map
#                              is configured).  Structurally agnostic to specific roles —
#                              records are generated dynamically from policy rule outputs
#                              and engine triggers, with no hardcoded role field names.
#
#   needs_escalation  boolean   True if any blocking or advisory record exists.
#   has_blocking      boolean   True if at least one blocking record is present.
#   has_advisory      boolean   True if at least one advisory record is present.
#   records           array    Each record:
#     person_to_escalate_to  string  Role or person responsible for resolving this item.
#                                    Derived dynamically from policy rule keys or engine
#                                    routing — never hardcoded in the output schema.
#     reason_for_escalation  string  Why this escalation is needed (what condition fired).
#     task_for_escalation    string  What the recipient is expected to do.
#     severity               string  "blocking" | "advisory"
#     source                 string  Rule key or trigger ID that produced this record,
#                                    e.g. "escalate_to_cpo", "CR_C01_CONFIDENCE_FLOOR",
#                                    "INSUFFICIENT_SUPPLIERS"
#     source_type            string  "policy_rule" (from action pipeline) |
#                                    "engine" (structural / missing field) |
#                                    "confidence" (CR-C rule)
#   context_notes     array[string]  Contextual modifiers applied (urgency, budget scaling,
#                                    confidence boost).
#
# flag_assessment  object | null  Result-quality flags.  Shape:
#
#   flags   array   Each flag:
#     flag_id      string   Stable identifier, e.g. "NO_COMPLIANT_SUPPLIERS",
#                           "LOW_RANK_CLUSTER", "INDISTINGUISHABLE_RANKS",
#                           "HIGH_EXCLUSION_RATE", "BUDGET_INSUFFICIENT",
#                           "PREFERRED_SUPPLIER_RESTRICTED",
#                           "PREFERRED_BONUS_DECISIVE",
#                           "QUANTITY_EXCEEDS_TIER_MAXIMUM"
#     severity     string   "warning" | "info"
#     description  string   Human-readable explanation of the flag
#
# confidence_assessment  object | null  How much to trust the ranking.
#                        Distinct from normalized_rank: expresses *reliability of the
#                        ordering*, not which supplier is better.  Shape:
#
#   score        number   Composite reliability score in [0, 1].
#   label        string   "high" (≥0.75) | "medium" (≥0.50) | "low" (≥0.25) | "very_low"
#   explanation  string   One-sentence summary of the main limiting factor.
#   breakdown    object   Per-dimension detail:
#     dimensions   object  Scores for each of the five dimensions (each 0–1):
#       input_completeness    quantity / budget / category present and valid
#       market_coverage       competing suppliers survived; low exclusion rate
#       ranking_decisiveness  gap between #1 and #2; absolute rank level
#       data_reliability      historical data points; z-score vs fallback
#       compliance_quality    top supplier compliance; systemic penalty if all penalized
#     weights      object  Fixed weights applied to each dimension (sum to 1.0)
#     worst_dimension  string  The dimension with the lowest raw score
#     meta         object  Supporting stats: n_surviving_suppliers, n_excluded,
#                          n_hist_data_points, hist_std_dev_available, used_zscore_sigmoid
#
# execution_log  object   Full RequestExecutionLog serialized to JSON.
#                         Contains everything needed to reproduce or audit the run:
#
#   request_id                string   Echoed from input
#   timestamp                 string   ISO-8601 UTC evaluation timestamp
#   global_context_snapshot   object   fix_in fields extracted from the request
#   supplier_logs             array    One entry per supplier (including excluded ones):
#
#     supplier_id             string
#     supplier_name           string
#     category_l2             string
#     pricing_resolved        object   Resolved pricing fields (unit_price, currency, …)
#     excluded                boolean  True if the supplier was excluded
#     exclusion_reason        string | null
#     final_cost_rank_score   number | null
#     final_reputation_score  number | null
#     final_compliance_score  number | null
#     final_normalized_rank   number | null
#     final_state             object   Full key→value state after all actions ran
#     action_logs             array    One entry per action tuple evaluated:
#       action_index          integer  0-based position in sorted_actions
#       rule_id               string   e.g. "AT-001", "CR-003", "RANKING"
#       rule_description      string   What this action computes
#       action_type           string   "AL" | "ALI" | "OSLM" | "SRM"
#       action_tuple          array    The raw action tuple as a list
#       when_condition        string | null   Raw WHEN expression (null if unconditional)
#       when_evaluated        boolean  True when a WHEN clause was present and evaluated
#       when_passed           boolean  True when WHEN passed (or no WHEN clause)
#       when_error            string | null  Exception message if WHEN evaluation crashed
#       input_values          object   Snapshot of resolved input values at eval time
#       output_key            string | null  State key written to (null for no-ops)
#       output_value_before   any | null     Value before execution
#       output_value_after    any | null     Value after execution (null if skipped)
#       skipped               boolean  True when the action had no effect
#       action_error          string | null  Exception if the computation itself failed
#       operator_applied      boolean  False when one param was "_" (assignment bypass)
#
#   global_action_logs        array    Reserved for future global (non-supplier) actions
#   escalation_assessment     object | null  Same structure as top-level field above
#   flag_assessment           object | null  Same structure as top-level field above
