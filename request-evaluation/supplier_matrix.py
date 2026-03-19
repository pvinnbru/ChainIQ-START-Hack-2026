"""
supplier_matrix.py — Supplier matrix support for the procurement automation system.

Builds on top of the existing pipeline:
  - rule_ingestion_prompt.py  (LLM → action tuples)
  - sort_actions.py           (DFS topo-sort of action tuples)

Action tuple format (defined in rule_ingestion_prompt.py):
  (TYPE, in_param1, in_param2_or_immediate, operator, out_param [, WHEN condition])

Types: AL, ALI, OSLM, SRM

Schema tuple format (as returned by load_schema and used throughout this module):
  (name, type, description, relevance)

  type:      fix_in | fix_out | meta | free
  relevance: "supplier_matrix" for per-supplier fields, "" for request-level fields
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ingest_historical_awards import get_historical_stats, get_supplier_historic_score, load_historical_store

# ---------------------------------------------------------------------------
# Country-to-region mapping
# ---------------------------------------------------------------------------

# Maps ISO-2 country codes to pricing region strings.
# CH is listed before EU so that Swiss delivery is priced under the CH tier
# rather than the broader EU tier.
COUNTRY_TO_REGION: dict[str, str] = {
    # Switzerland — dedicated CH region (must precede EU in lookup logic)
    "CH": "CH",
    # EU / EEA
    "AT": "EU",
    "BE": "EU",
    "BG": "EU",
    "CY": "EU",
    "CZ": "EU",
    "DE": "EU",
    "DK": "EU",
    "EE": "EU",
    "ES": "EU",
    "FI": "EU",
    "FR": "EU",
    "GR": "EU",
    "HR": "EU",
    "HU": "EU",
    "IE": "EU",
    "IT": "EU",
    "LT": "EU",
    "LU": "EU",
    "LV": "EU",
    "MT": "EU",
    "NL": "EU",
    "PL": "EU",
    "PT": "EU",
    "RO": "EU",
    "SE": "EU",
    "SI": "EU",
    "SK": "EU",
    # Non-EU European countries typically priced in EU tier
    "NO": "EU",
    "IS": "EU",
    "UK": "EU",
    "GB": "EU",
    # Americas
    "US": "Americas",
    "CA": "Americas",
    "MX": "Americas",
    "BR": "Americas",
    "AR": "Americas",
    "CL": "Americas",
    "CO": "Americas",
    "PE": "Americas",
    # APAC
    "AU": "APAC",
    "NZ": "APAC",
    "JP": "APAC",
    "CN": "APAC",
    "KR": "APAC",
    "IN": "APAC",
    "SG": "APAC",
    "HK": "APAC",
    "TW": "APAC",
    "TH": "APAC",
    "MY": "APAC",
    "ID": "APAC",
    "PH": "APAC",
    "VN": "APAC",
    # MEA
    "AE": "MEA",
    "UAE": "MEA",   # non-standard but appears in data
    "SA": "MEA",
    "ZA": "MEA",
    "EG": "MEA",
    "NG": "MEA",
    "KE": "MEA",
    "QA": "MEA",
    "KW": "MEA",
    "BH": "MEA",
    "OM": "MEA",
    "IL": "MEA",
    "TR": "MEA",
    "MA": "MEA",
}

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

# A loaded supplier record: identity holds meta fields, attributes holds
# everything else (typed appropriately).
SupplierRecord = dict[str, Any]   # keys: "identity", "attributes"

# Columns treated as supplier identity / meta — kept out of action-visible dict.
_SUPPLIER_META_COLS: frozenset[str] = frozenset(
    {"supplier_id", "supplier_name", "category_l1", "category_l2", "country_hq", "service_regions"}
)

# ---------------------------------------------------------------------------
# Execution log dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ActionLogEntry:
    """Records the execution of a single action tuple for one supplier."""

    action_index: int
    rule_id: str
    rule_description: str
    action_type: str
    action_tuple: tuple
    when_condition: str | None
    when_evaluated: bool        # True when a WHEN clause was present and evaluated
    when_passed: bool           # True when WHEN passed (or no WHEN clause)
    input_values: dict          # Snapshot of all params read, with actual values
    output_key: str | None
    output_value_before: Any    # Value of output_key before execution (None if skipped)
    output_value_after: Any     # Value of output_key after execution (None if skipped)
    skipped: bool               # True when WHEN did not pass (action had no effect)
    # ISSUE-004/023: set when the WHEN clause threw an exception instead of evaluating.
    # Distinguishes "intentionally False" from "exception during evaluation".
    when_error: str | None = None
    # ISSUE-005: set when the action computation failed (e.g. unknown operator,
    # None state key, ZeroDivisionError).  Distinguishes silent skips from errors.
    action_error: str | None = None
    # ISSUE-014: False when one param was "_" and the operator was not applied
    # (assignment semantics via the None-bypass path).
    operator_applied: bool = True


@dataclass
class SupplierLog:
    """Execution log for a single supplier within one request evaluation."""

    supplier_id: str
    supplier_name: str
    category_l2: str
    pricing_resolved: dict
    action_logs: list[ActionLogEntry]
    final_state: dict
    final_cost_rank_score: float | None
    final_reputation_score: float | None
    final_compliance_score: float | None
    final_normalized_rank: float | None
    excluded: bool
    exclusion_reason: str | None


@dataclass
class RequestExecutionLog:
    """Top-level log capturing everything that happened during one procurement evaluation."""

    request_id: str
    timestamp: str              # ISO-8601
    global_context_snapshot: dict
    supplier_logs: list[SupplierLog]
    global_action_logs: list[ActionLogEntry]   # Reserved for future global (non-supplier) actions
    escalation_assessment: Any | None = None   # EscalationAssessment, set post-evaluation
    flag_assessment:       Any | None = None   # FlagAssessment, set post-evaluation
    confidence_assessment: Any | None = None   # ConfidenceAssessment, set post-evaluation

# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------

def load_schema(csv_path: str) -> tuple[list[tuple], set[str]]:
    """
    Load start_dict.csv and return:

      schema      — list of 4-tuples (name, type, description, relevance) for
                    every row, including meta rows.
      fix_in_keys — set of all fix_in names (both request-level and
                    supplier_matrix).  Used by sort_actions to avoid creating
                    dependency edges on externally-provided keys.

    The relevance field is "supplier_matrix" for per-supplier attributes and
    "" (empty string) for request-level attributes.
    """
    schema: list[tuple] = []
    fix_in_keys: set[str] = set()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row["names"].strip()
            typ = row["type"].strip()
            desc = row["description"].strip()
            relevance = row.get("relevance", "").strip()
            schema.append((name, typ, desc, relevance))
            if typ == "fix_in":
                fix_in_keys.add(name)

    return schema, fix_in_keys


# ---------------------------------------------------------------------------
# Pricing data loading
# ---------------------------------------------------------------------------

def load_pricing_index(pricing_csv_path: str) -> dict:
    """
    Read the pricing CSV and build an index:

        (supplier_id, category_l2, region) -> list[dict]

    Each dict in the list represents one pricing tier with keys:
        min_quantity, max_quantity, unit_price, standard_lead_time_days,
        expedited_lead_time_days, expedited_unit_price, pricing_model, currency
    """
    index: dict[tuple[str, str, str], list[dict]] = {}

    with open(pricing_csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (
                row["supplier_id"].strip(),
                row["category_l2"].strip(),
                row["region"].strip(),
            )
            tier = {
                "min_quantity": int(row["min_quantity"]),
                "max_quantity": int(row["max_quantity"]),
                "unit_price": float(row["unit_price"]),
                "standard_lead_time_days": int(row["standard_lead_time_days"]),
                "expedited_lead_time_days": int(row["expedited_lead_time_days"]),
                "expedited_unit_price": float(row["expedited_unit_price"]),
                "pricing_model": row["pricing_model"].strip(),
                "currency": row["currency"].strip(),
            }
            index.setdefault(key, []).append(tier)

    return index


def resolve_supplier_pricing(
    supplier_identity: dict,
    pricing_index: dict,
    global_context: dict,
) -> dict:
    """
    Resolve the applicable pricing tier for a supplier given the current request.

    Steps:
      1. Map global_context['delivery_country'] to a region via COUNTRY_TO_REGION.
      2. Look up tiers by (supplier_id, category_l2, region).
      3. Find the tier where min_quantity <= quantity <= max_quantity.
      4. Return a dict with resolved pricing fields.

    Returns an empty dict if no matching tier is found (the caller should
    exclude the supplier from shortlisting).
    """
    delivery_country: str = str(global_context.get("delivery_country", ""))
    region: str = COUNTRY_TO_REGION.get(delivery_country, "")
    if not region:
        return {}

    supplier_id: str = supplier_identity.get("supplier_id", "")
    category_l2: str = supplier_identity.get("category_l2", "")
    quantity: int | float = global_context.get("quantity", 0)

    tiers = pricing_index.get((supplier_id, category_l2, region), [])
    for tier in tiers:
        if tier["min_quantity"] <= quantity <= tier["max_quantity"]:
            return {
                "unit_price": tier["unit_price"],
                "standard_lead_time_days": tier["standard_lead_time_days"],
                "expedited_lead_time_days": tier["expedited_lead_time_days"],
                "expedited_unit_price": tier["expedited_unit_price"],
                "pricing_model": tier["pricing_model"],
                "currency": tier["currency"],
            }

    return {}


# ---------------------------------------------------------------------------
# Schema extension for ranking KPIs
# ---------------------------------------------------------------------------

def add_ranking_schema_entries(schema: list[tuple]) -> list[tuple]:
    """
    Programmatically add KPI schema entries needed by the ranking pipeline.

    Entries are appended only if they are not already present.  All new entries
    are tagged as supplier_matrix relevance.

    Levels (dependency depth, lower = more independent):
      0 — unit_price        (fix_in, resolved from pricing index)
      1 — cost_total        (free, quantity * unit_price + modifiers)
      1 — reputation_score  (free, composite quality/risk/ESG)
      2 — cost_rank_score   (free, inverted cost score 0-100)
      3 — rank              (already in schema as fix_out; updated level note only)
    """
    existing = {e[0] for e in schema}
    new_entries: list[tuple] = [
        (
            "unit_price",
            "fix_in",
            "Resolved unit price from applicable pricing tier in supplier currency [level=0] [supplier_matrix]",
            "supplier_matrix",
        ),
        (
            "cost_total",
            "free",
            "Total estimated cost: quantity * unit_price, adjustable by rules (switching costs, surcharges) [level=1] [supplier_matrix]",
            "supplier_matrix",
        ),
        (
            "reputation_score",
            "free",
            "Composite quality/risk/ESG score: 0.5*quality_score + 0.3*(100-risk_score) + 0.2*esg_score [level=1] [supplier_matrix]",
            "supplier_matrix",
        ),
        (
            "cost_rank_score",
            "free",
            "Inverted cost score 0-100 derived from cost_total; higher = cheaper; NOT a cross-supplier normalisation [level=2] [supplier_matrix]",
            "supplier_matrix",
        ),
        (
            "compliance_score",
            "free",
            "Compliance multiplier in [0.0, 1.0] applied multiplicatively to normalized_rank. Starts at 1.0; OSLM actions subtract severity penalties (e.g. 0.05 minor, 0.15 moderate, 0.30 significant) when soft compliance requirements are violated. Hard violations use excluded=True instead. [level=1] [supplier_matrix]",
            "supplier_matrix",
        ),
        (
            "excluded",
            "free",
            "Boolean flag set to True by OSLM actions when a supplier violates a hard compliance requirement (e.g. data residency, category gate). Suppliers where excluded=True are removed from the shortlist after action evaluation. [level=1] [supplier_matrix]",
            "supplier_matrix",
        ),
    ]
    result = list(schema)
    for entry in new_entries:
        if entry[0] not in existing:
            result.append(entry)
    return result


# ---------------------------------------------------------------------------
# LLM action generation
# ---------------------------------------------------------------------------

_RANKING_SYSTEM_PROMPT = """\
You are a procurement rules engine expert.  Your task is to generate \
executable action tuples that compute KPI fields used to rank suppliers.

**Action tuple format**
(TYPE, in_param1, in_param2_or_immediate, operator, out_param [, WHEN condition])

Types:
  AL   — in_param1 op in_param2 → out_param          (both params are dict keys)
  ALI  — in_param1 op immediate → out_param           (in_param2 is a literal constant)
  OSLM — conditional AL/ALI applied to supplier matrix entries (WHEN clause optional)
  SRM  — identical to OSLM; use when out_param is 'rank'

Operators: + - * / = != >= <= > < AND OR XOR
Use '_' for unused params.

**Schema**
The schema is a list of 4-tuples (name, type, description, relevance).
  fix_in  — externally supplied; never write to these
  fix_out — must produce a value for these
  free    — intermediate computed fields; you may read and write them
  meta    — ignore entirely

**Your task**
Generate OSLM and SRM actions (in dependency order, producers before consumers) for:

1. cost_total  (free, level=1)
   Base formula: quantity * unit_price
   IMPORTANT: cost_total is designed to be modified by downstream rules
   (switching costs, surcharges, penalties).  Generate a simple multiplication
   action.  Rules actions will use AL/ALI to add modifiers on top of this base.

2. reputation_score  (free, level=1)
   Weighted composite: 0.5 * quality_score  +  0.3 * (100 - risk_score)  +  0.2 * esg_score
   Decompose into intermediate steps using free keys (prefix with '_rep_').
   quality_score and esg_score are higher = better; risk_score is lower = better.

   CRITICAL — subtraction operand order:
   (TYPE, in_param1, in_param2, -, out) computes in_param1 − in_param2.
   You CANNOT write (OSLM, risk_score, 100, -, x) to get 100−risk_score; that gives
   risk_score−100 (WRONG for low risk_score values, produces large negatives).
   To compute  100 − risk_score  use two steps:
     Step A: OSLM(risk_score, -0.3, *, _rep_risk_neg)  →  −0.3 × risk_score
     Step B: OSLM(_rep_risk_neg, 30, +, _rep_risk)     →  30 − 0.3×risk_score = 0.3×(100−risk_score)
   Never write the same key as both in_param1 and out_param in the same action —
   self-referential actions (e.g. "_rep_risk = _rep_risk * 0.3") are unreliable
   because the topological sort cannot correctly order them.

3. cost_rank_score  (free, level=2)
   LIMITATION: Actions run per-supplier, so true cross-supplier normalisation
   is impossible.  Use an inverted scaling formula against cost_total directly
   (lower cost → higher score, range 0–100).  A reasonable formula is:
     cost_rank_score = 10000000 / (cost_total + 1)  capped at 100
   Since capping requires two steps, use:
     _crs_raw  = 10000000 / (cost_total + 1)   — use ALI with immediate 10000000
     cost_rank_score = min(_crs_raw, 100)       — approximate with ALI: if not \
available use the raw value directly with a comment explaining the limitation.
   Document the limitation: this score is not normalised across the shortlist;
   final lexicographic ordering in run_procurement_evaluation uses
   (cost_rank_score DESC, reputation_score DESC) which effectively does the
   cross-supplier comparison at sort time.

4. rank  (fix_out, level=3)
   Combine cost_rank_score (dominant) and reputation_score (tiebreaker).
   Formula: rank = cost_rank_score * 100 + reputation_score
   This ensures any difference in cost_rank_score outweighs reputation_score
   (max 100), so suppliers with even a small cost advantage always rank higher.
   Use SRM type (out_param = rank).

**Output format** — return ONLY these two sections (no preamble, no DICT section):
ACTIONS: {
  (OSLM, ..., ...),
  ...
  (SRM, ..., ...),
}

ATTRIBUTION: {
  0: {"rule_id": "RANKING", "rule_description": "<brief description of what action 0 computes>"},
  1: {"rule_id": "RANKING", "rule_description": "<brief description of what action 1 computes>"},
  ...
}

Where each ATTRIBUTION index is the 0-based position in the ACTIONS list.
Use "RANKING" as the rule_id for all ranking actions.
"""


def _parse_attribution_from_llm(response_text: str) -> dict:
    """
    Extract the ATTRIBUTION block from an LLM response.

    Expected format::

        ATTRIBUTION: {
          0: {"rule_id": "RANKING", "rule_description": "Compute cost_total"},
          1: {"rule_id": "RANKING", "rule_description": "..."},
        }

    Returns a dict mapping int action_index → {"rule_id": str, "rule_description": str}.
    Returns an empty dict if the block is absent or unparseable.
    """
    attr_start = response_text.find("ATTRIBUTION:")
    if attr_start == -1:
        return {}

    brace_start = response_text.find("{", attr_start)
    if brace_start == -1:
        return {}

    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(response_text)):
        if response_text[i] == "{":
            depth += 1
        elif response_text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break
    else:
        return {}

    block = response_text[brace_start : brace_end + 1]
    result: dict = {}
    for m in re.finditer(r"(\d+)\s*:\s*\{([^}]+)\}", block):
        idx = int(m.group(1))
        inner = m.group(2)
        rid_m = re.search(r'"rule_id"\s*:\s*"([^"]*)"', inner)
        rdesc_m = re.search(r'"rule_description"\s*:\s*"([^"]*)"', inner)
        if rid_m and rdesc_m:
            result[idx] = {
                "rule_id": rid_m.group(1),
                "rule_description": rdesc_m.group(1),
            }

    return result


def _parse_actions_from_llm(response_text: str) -> list[tuple]:
    """
    Extract action tuples from the ACTIONS: { ... } block of an LLM response.

    Each action must appear on its own line as (TYPE, a, b, op, out [, WHEN …]).
    Splitting on ', ' is safe because WHEN clauses do not contain commas.
    """
    actions_start = response_text.find("ACTIONS:")
    if actions_start == -1:
        return []
    dict_start = response_text.find("DICT:", actions_start)
    section = response_text[actions_start: dict_start if dict_start != -1 else None]

    valid_types = {"AL", "ALI", "OSLM", "SRM"}
    actions: list[tuple] = []

    for m in re.finditer(r"\(([^()]+)\)", section):
        content = m.group(1)
        parts = [p.strip() for p in content.split(",")]
        if len(parts) < 5 or parts[0] not in valid_types:
            continue
        if len(parts) > 5:
            action: tuple = tuple(parts[:5]) + (", ".join(parts[5:]),)
        else:
            action = tuple(parts)
        actions.append(action)

    return actions


def generate_ranking_actions(
    schema: list[tuple],
    anthropic_client,
) -> tuple[list[tuple], dict]:
    """
    Make a single LLM call to generate actions that compute cost_total,
    reputation_score, cost_rank_score, and rank.

    The *anthropic_client* argument accepts any client with an
    OpenAI-compatible ``client.chat.completions.create(model, messages)``
    interface (e.g. AzureOpenAI or the Anthropic SDK compatibility shim).

    The model and deployment are read from the AZURE_OPENAI_DEPLOYMENT
    environment variable (or "gpt-4o" as fallback).

    Returns:
        (actions, attribution_dict)
        - actions: parsed list of action tuples
        - attribution_dict: maps action index → {rule_id, rule_description}
    """
    schema_str = "\n".join(f"  {entry}" for entry in schema)
    user_message = f"### Schema\n{schema_str}"

    model = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    response = anthropic_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _RANKING_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )
    response_text: str = response.choices[0].message.content
    actions = _parse_actions_from_llm(response_text)
    attribution = _parse_attribution_from_llm(response_text)
    return actions, attribution


# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------

def save_generated_actions(
    ranking_actions: list[tuple],
    rules_actions: list[tuple],
    path: str,
    ranking_attribution: dict | None = None,
    rules_attribution: dict | None = None,
) -> None:
    """
    Serialise ranking and rules action lists (and optional attribution dicts) to JSON.

    Tuples are stored as JSON arrays.  Attribution dicts use string keys in JSON
    (integer keys are not valid JSON); :func:`load_generated_actions` converts
    them back to int keys on load.

    Load with :func:`load_generated_actions`.
    """
    payload = {
        "ranking_actions": [list(a) for a in ranking_actions],
        "rules_actions": [list(a) for a in rules_actions],
        "ranking_attribution": {str(k): v for k, v in (ranking_attribution or {}).items()},
        "rules_attribution": {str(k): v for k, v in (rules_attribution or {}).items()},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def load_generated_actions(
    path: str,
) -> tuple[list[tuple], list[tuple], dict, dict]:
    """
    Load action lists (and attribution dicts) previously saved by
    :func:`save_generated_actions`.

    JSON arrays are converted back to tuples.  Attribution dict string keys
    are converted back to ints.

    Returns:
        (ranking_actions, rules_actions, ranking_attribution, rules_attribution)
    """
    with open(path, encoding="utf-8") as fh:
        try:
            payload = json.load(fh)
        except json.JSONDecodeError as exc:
            # ISSUE-020: surface a clear error instead of an opaque JSONDecodeError.
            raise ValueError(f"Corrupted action store file {path!r}: {exc}") from exc

    ranking_actions = [tuple(a) for a in payload["ranking_actions"]]
    rules_actions = [tuple(a) for a in payload["rules_actions"]]
    ranking_attribution = {
        int(k): v for k, v in payload.get("ranking_attribution", {}).items()
    }
    rules_attribution = {
        int(k): v for k, v in payload.get("rules_attribution", {}).items()
    }
    return ranking_actions, rules_actions, ranking_attribution, rules_attribution


# ---------------------------------------------------------------------------
# Full action pipeline builder
# ---------------------------------------------------------------------------

def build_full_action_pipeline(
    ranking_actions: list[tuple],
    rules_actions: list[tuple],
    fix_in_keys: set[str],
    ranking_attribution: dict | None = None,
    rules_attribution: dict | None = None,
) -> tuple[list[tuple], bool, dict]:
    """
    Combine ranking and rules action lists, then topologically sort the result.

    Ranking actions are placed first in the combined list so the topo-sort
    sees their outputs as available for rules actions that depend on them.

    Attribution dicts (mapping original action index → {rule_id, rule_description})
    are merged — rules_attribution indices are offset by len(ranking_actions) to
    reflect their position in the combined list.  The returned attribution dict
    is rekeyed to match the sorted positions.

    Returns:
        (sorted_combined, is_low_confidence, rekeyed_attribution)
    """
    from sort_actions import sort_actions  # local import avoids circular deps

    combined = list(ranking_actions) + list(rules_actions)

    # Merge attribution dicts, offsetting rules indices
    combined_attribution: dict = {}
    for k, v in (ranking_attribution or {}).items():
        combined_attribution[int(k)] = v
    offset = len(ranking_actions)
    for k, v in (rules_attribution or {}).items():
        combined_attribution[int(k) + offset] = v

    return sort_actions(combined, fix_in_keys, attribution=combined_attribution)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce(value: str) -> bool | int | float | str:
    """Coerce a raw CSV string to the most specific Python scalar type."""
    low = value.strip().lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_literal(token: str) -> Any:
    """
    Parse *token* as a literal value — never resolves from state.
    Used exclusively for ALI in_param2 (the 'immediate' argument).
    """
    stripped = token.strip()
    # Quoted string
    if len(stripped) >= 2 and (
        (stripped[0] == '"' and stripped[-1] == '"')
        or (stripped[0] == "'" and stripped[-1] == "'")
    ):
        return stripped[1:-1]
    # Numeric
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    # Boolean keyword
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    # Return as plain string literal
    return stripped


def _resolve_value(token: str, state: dict[str, Any]) -> Any:
    """
    Resolve *token* against the current state dict, falling back to
    literal parsing when no matching key is found.
    Used for AL/OSLM/SRM in_param2 and for WHEN condition atoms.
    State lookup takes precedence so callers can reference numeric-named keys.
    """
    stripped = token.strip()
    if stripped in state:
        return state[stripped]
    return _parse_literal(stripped)


def _resolve_param1(token: str, state: dict[str, Any]) -> Any:
    """
    Resolve the in_param1 token, giving numeric/boolean literals priority over
    state-key lookup.

    ISSUE-015: in_param1 has no ALI equivalent, so rule authors sometimes write
    a numeric constant (e.g. "10000000") directly as in_param1.  Without this
    function that constant would be silently shadowed if any rule ever writes a
    state key whose name happens to be the same number.  By trying literal
    parsing first we make numeric/boolean in_param1 values unambiguous.
    String-valued tokens (which cannot be parsed as a number or bool) still fall
    through to the state dict so normal state-key references work as before.
    """
    stripped = token.strip()
    parsed = _parse_literal(stripped)
    if not isinstance(parsed, str):
        return parsed  # numeric or boolean literal — unambiguous
    if stripped in state:
        return state[stripped]
    return parsed


def _apply_operator(lhs: Any, op: str, rhs: Any) -> Any:
    """
    Apply *op* between *lhs* and *rhs* using explicit conditional logic.
    No eval() is used.

    O-3 / ISSUE-014 design note:
      The "=" operator is an equality *comparison*, not assignment.  Assignment
      semantics are achieved by using "_" as a placeholder for one parameter
      (causing the None-bypass path in evaluate_actions), NOT by writing
        ('OSLM', 'source', '_', '=', 'dest').
      Using = with two real operands stores a boolean result in the output key.
    """
    match op:
        case "+":
            return lhs + rhs
        case "-":
            return lhs - rhs
        case "*":
            return lhs * rhs
        case "/":
            if rhs == 0:
                raise ZeroDivisionError(f"Division by zero: {lhs} / {rhs}")
            return lhs / rhs
        case "=":
            return lhs == rhs
        case "!=":
            return lhs != rhs
        case ">=":
            return lhs >= rhs
        case "<=":
            return lhs <= rhs
        case ">":
            return lhs > rhs
        case "<":
            return lhs < rhs
        case "AND":
            return bool(lhs) and bool(rhs)
        case "OR":
            return bool(lhs) or bool(rhs)
        case "XOR":
            return bool(lhs) ^ bool(rhs)
        # ISSUE-001: MIN / MAX operators for clamping without comparison booleans.
        case "MIN":
            return min(lhs, rhs)
        case "MAX":
            return max(lhs, rhs)
        case _:
            raise ValueError(f"Unknown operator: {op!r}")


# ---------------------------------------------------------------------------
# WHEN condition evaluator
# ---------------------------------------------------------------------------

def _tokenize_when(expr: str) -> list[str]:
    """
    Split a WHEN expression into a flat token list.

    Handles:
    - Quoted strings (single or double quotes) as single tokens even when
      they contain spaces, e.g. "Cloud Compute" → one token '"Cloud Compute"'
    - Two-char operators before single-char: >=, <=, !=
    - Single-char operators and parens: > < = ( )
    - Identifiers, keywords, and numeric literals
    """
    tokens: list[str] = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        # Quoted string — scan to closing quote and emit as a single token.
        # ISSUE-017: handle backslash escape sequences so that an escaped quote
        # (e.g. "O\'Brien") does not prematurely terminate the string scan.
        if c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n:
                if expr[j] == "\\" and j + 1 < n:
                    j += 2  # skip escape character and the escaped character
                    continue
                if expr[j] == quote:
                    break
                j += 1
            tokens.append(expr[i : j + 1])  # includes both quote chars
            i = j + 1
            continue
        # Two-char operators must be checked before single-char
        if i + 1 < n and expr[i : i + 2] in (">=", "<=", "!="):
            tokens.append(expr[i : i + 2])
            i += 2
            continue
        # Single-char operators / parens
        if c in (">", "<", "=", "(", ")"):
            tokens.append(c)
            i += 1
            continue
        # Identifier, keyword, or number
        j = i
        while j < n and not expr[j].isspace() and expr[j] not in (
            ">", "<", "=", "(", ")", '"', "'"
        ):
            j += 1
        tokens.append(expr[i:j])
        i = j
    return tokens


def _eval_when(expr: str, state: dict[str, Any]) -> bool:
    """
    Evaluate a WHEN condition boolean expression over *state*.

    Supported:
      - Logical:    AND, OR, NOT  (case-insensitive)
      - Comparison: =  !=  >=  <=  >  <
      - Grouping:   ( ... )
      - Operands:   dict-key identifiers, numeric literals, quoted strings,
                    True / False keywords

    Uses a hand-written recursive-descent parser — no eval().
    """
    # Strip optional leading WHEN keyword
    raw = expr.strip()
    if raw.upper().startswith("WHEN "):
        raw = raw[5:].strip()

    tokens = _tokenize_when(raw)
    pos = [0]  # mutable pointer wrapped in a list so nested funcs can update it

    def peek() -> str | None:
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def consume() -> str:
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def parse_atom() -> bool:
        tok = peek()
        if tok is None:
            raise ValueError(f"Unexpected end in WHEN expression: {expr!r}")

        # Parenthesised sub-expression
        if tok == "(":
            consume()
            val = parse_or()
            if peek() == ")":
                consume()
            return val

        # NOT prefix
        if tok.upper() == "NOT":
            consume()
            return not parse_atom()

        # LHS token — could be start of a comparison or a bare boolean reference
        lhs_tok = consume()
        op_tok = peek()
        _CMP_OPS = {"=", "!=", ">=", "<=", ">", "<"}
        if op_tok in _CMP_OPS:
            consume()
            rhs_tok = consume()
            lhs = _resolve_value(lhs_tok, state)
            rhs = _resolve_value(rhs_tok, state)
            result = _apply_operator(lhs, op_tok, rhs)
            return bool(result)

        # Bare identifier — treat as boolean lookup
        return bool(_resolve_value(lhs_tok, state))

    def parse_and() -> bool:
        left = parse_atom()
        while peek() and peek().upper() == "AND":
            consume()
            right = parse_atom()
            left = left and right
        return left

    def parse_or() -> bool:
        left = parse_and()
        while peek() and peek().upper() == "OR":
            consume()
            right = parse_and()
            left = left or right
        return left

    return parse_or()


# ---------------------------------------------------------------------------
# 1. Supplier data loading
# ---------------------------------------------------------------------------

def load_suppliers(
    supplier_csv_path: str,
    extra_csv_paths: list[str],
) -> list[SupplierRecord]:
    """
    Load the primary supplier CSV, join any extra CSVs on (supplier_id, category_l2),
    and return a list of SupplierRecord dicts.

    Each record contains:
      "identity"   — dict of meta columns (supplier_id, supplier_name, category_l1,
                     category_l2, country_hq, service_regions); service_regions is
                     retained as a raw semicolon-separated string.
      "attributes" — dict of all remaining columns, coerced to bool/int/float/str.
    """
    with open(supplier_csv_path, newline="", encoding="utf-8") as fh:
        primary_rows: list[dict[str, str]] = list(csv.DictReader(fh))

    # Build (supplier_id, category_l2) → extra-field dict from each extras CSV.
    # Later CSVs overwrite earlier ones for the same key.
    extra_lookup: dict[tuple[str, str], dict[str, str]] = {}
    for path in extra_csv_paths:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                key = (row["supplier_id"], row["category_l2"])
                if key not in extra_lookup:
                    extra_lookup[key] = {}
                extra_lookup[key].update(
                    {k: v for k, v in row.items() if k not in ("supplier_id", "category_l2")}
                )

    result: list[SupplierRecord] = []
    for row in primary_rows:
        join_key = (row.get("supplier_id", ""), row.get("category_l2", ""))
        merged: dict[str, str] = dict(row)
        if join_key in extra_lookup:
            merged.update(extra_lookup[join_key])

        identity: dict[str, str] = {
            col: merged[col] for col in _SUPPLIER_META_COLS if col in merged
        }
        attributes: dict[str, Any] = {
            col: _coerce(val)
            for col, val in merged.items()
            if col not in _SUPPLIER_META_COLS
        }

        result.append({"identity": identity, "attributes": attributes})

    return result


# ---------------------------------------------------------------------------
# 2. Request context loading
# ---------------------------------------------------------------------------

def build_global_context(
    request: dict[str, Any],
    schema: list[tuple],
) -> dict[str, Any]:
    """
    Extract the fix_in keys defined in *schema* from *request*.

    Raises KeyError (with a descriptive message listing all missing keys) if
    any fix_in key is absent from *request*.
    """
    context: dict[str, Any] = {}
    missing: list[str] = []

    for entry in schema:
        name: str = entry[0]
        typ: str = entry[1]
        relevance: str = entry[3] if len(entry) > 3 else ""

        if typ != "fix_in":
            continue
        if relevance == "supplier_matrix":
            # Supplier-matrix fix_in fields come from supplier data, not the
            # request.  They are loaded by load_suppliers / filter_suppliers
            # and injected into each supplier's attribute dict before action
            # evaluation — they must NOT be expected in the request dict.
            continue

        if name not in request:
            missing.append(name)
        else:
            context[name] = request[name]

    if missing:
        raise KeyError(
            f"Request is missing required fix_in field(s): {', '.join(missing)}"
        )

    return context


# ---------------------------------------------------------------------------
# 3. Supplier filtering
# ---------------------------------------------------------------------------

def filter_suppliers(
    suppliers: list[SupplierRecord],
    global_context: dict[str, Any],
    pricing_index: dict | None = None,
) -> list[SupplierRecord]:
    """
    Return only the suppliers that pass all gates:

    1. category_l1 and category_l2 match global_context values.
    2. serves_delivery_country — global_context['delivery_country'] appears as
       a token in the semicolon-separated service_regions string.
    3. is_restricted is not True.
    4. If *pricing_index* is provided: a matching pricing tier must exist for
       the supplier's (supplier_id, category_l2) and the request's delivery
       region and quantity.  Suppliers without a matching tier are excluded.

    Each surviving record gets serves_delivery_country=True added to its
    attribute dict.  When *pricing_index* is provided the resolved pricing
    fields (unit_price, standard_lead_time_days, expedited_lead_time_days,
    expedited_unit_price, pricing_model, currency) are also injected as
    fix_in supplier_matrix attributes so they are available to action
    evaluation without further lookup.
    """
    cat_l1: str = str(global_context.get("category_l1", ""))
    cat_l2: str = str(global_context.get("category_l2", ""))
    delivery_country: str = str(global_context.get("delivery_country", ""))

    # ISSUE-012: a None delivery_country coerces to the string "None" which
    # never appears in any supplier's service_regions — silently excluding every
    # supplier.  Return early with an empty list so callers see 0 suppliers.
    if not delivery_country or delivery_country.lower() in ("none", "null"):
        return []

    result: list[SupplierRecord] = []

    for supplier in suppliers:
        identity = supplier["identity"]
        attrs = supplier["attributes"]

        # Gate 1 — category match
        if identity.get("category_l1") != cat_l1:
            continue
        if identity.get("category_l2") != cat_l2:
            continue

        # Gate 2 — delivery-country coverage
        raw_regions: str = identity.get("service_regions", "")
        region_tokens = {r.strip() for r in raw_regions.split(";") if r.strip()}
        if delivery_country not in region_tokens:
            continue

        # Gate 3 — not restricted
        if attrs.get("is_restricted") is True:
            continue

        new_attrs: dict[str, Any] = dict(attrs)
        new_attrs["serves_delivery_country"] = True

        # Gate 4 — pricing resolution (only when a pricing index is supplied)
        if pricing_index is not None:
            pricing = resolve_supplier_pricing(identity, pricing_index, global_context)
            if not pricing:
                # No matching tier → supplier is incomplete for this request
                continue
            new_attrs.update(pricing)

        result.append({"identity": dict(identity), "attributes": new_attrs})

    return result


# ---------------------------------------------------------------------------
# 4. Action evaluator
# ---------------------------------------------------------------------------


def _quantity_exceeds_max_tier(
    supplier_identity: dict,
    pricing_index: dict,
    global_context: dict[str, Any],
) -> bool:
    """ISSUE-011: Return True when pricing tiers exist for this supplier/category/region
    but the requested quantity is above every tier's max_quantity.

    Distinguishes "no tiers for this region" (different root cause) from
    "order is too large for any available tier" so the caller can surface a
    more actionable QUANTITY_EXCEEDS_TIER_MAXIMUM flag.
    """
    delivery_country = str(global_context.get("delivery_country", ""))
    region = COUNTRY_TO_REGION.get(delivery_country, "")
    if not region:
        return False
    supplier_id = str(supplier_identity.get("supplier_id", ""))
    category_l2 = str(supplier_identity.get("category_l2", ""))
    tiers = pricing_index.get((supplier_id, category_l2, region), [])
    if not tiers:
        return False  # no tiers at all — different issue (region/category mismatch)
    quantity = global_context.get("quantity", 0)
    return quantity > max(t["max_quantity"] for t in tiers)


# Exclusion-reason fragments that indicate a supplier is outside the request's
# scope (wrong category or delivery country), as opposed to being within scope
# but failing a compliance/restriction gate.  Used by run_procurement_evaluation
# to compute the correct HIGH_EXCLUSION_RATE denominator.
_SCOPE_EXCLUSION_MARKERS: tuple[str, ...] = (
    "category_l1 mismatch",
    "category_l2 mismatch",
    "does not serve delivery country",
    "delivery_country is null",
)


def _is_scope_exclusion(reason: str | None) -> bool:
    """True when the supplier was excluded because it doesn't serve this category/country."""
    if not reason:
        return False
    return any(marker in reason for marker in _SCOPE_EXCLUSION_MARKERS)


def _check_exclusion(
    supplier: SupplierRecord,
    global_context: dict[str, Any],
    pricing_index: dict | None,
) -> str | None:
    """
    Apply all supplier filter gates and return a human-readable exclusion reason,
    or None when the supplier passes all gates.

    Gates (mirrors filter_suppliers logic):
      1. category_l1 / category_l2 match
      2. delivery_country in service_regions
      3. is_restricted is not True
      4. Pricing tier exists (only when pricing_index is provided)
    """
    identity = supplier["identity"]
    attrs = supplier["attributes"]
    cat_l1 = str(global_context.get("category_l1", ""))
    cat_l2 = str(global_context.get("category_l2", ""))
    delivery_country = str(global_context.get("delivery_country", ""))

    # ISSUE-012: detect None coerced to the string "None" (or empty/null).
    # Without this check every supplier fails the service_regions gate silently,
    # making the request look like a market-availability problem instead of a
    # data-quality problem.
    if not delivery_country or delivery_country.lower() in ("none", "null"):
        return (
            f"delivery_country is null or invalid "
            f"(got {global_context.get('delivery_country')!r}) — cannot determine service region"
        )

    if identity.get("category_l1") != cat_l1:
        return f"category_l1 mismatch ({identity.get('category_l1')!r} != {cat_l1!r})"
    if identity.get("category_l2") != cat_l2:
        return f"category_l2 mismatch ({identity.get('category_l2')!r} != {cat_l2!r})"

    raw_regions: str = identity.get("service_regions", "")
    region_tokens = {r.strip() for r in raw_regions.split(";") if r.strip()}
    if delivery_country not in region_tokens:
        return f"does not serve delivery country {delivery_country!r}"

    if attrs.get("is_restricted") is True:
        return "supplier is restricted"

    if pricing_index is not None:
        pricing = resolve_supplier_pricing(identity, pricing_index, global_context)
        if not pricing:
            # ISSUE-011: distinguish "quantity too large for any tier" from
            # "no tiers exist for this region/category" so a specific flag can fire.
            if _quantity_exceeds_max_tier(identity, pricing_index, global_context):
                return "quantity exceeds all available pricing tiers for this supplier"
            return "no matching pricing tier for this region/quantity"

    return None


def evaluate_actions(
    sorted_actions: list[tuple],
    global_context: dict[str, Any],
    supplier_attrs: dict[str, Any],
    fix_in_keys: set[str],  # noqa: ARG001  kept for API symmetry / future use
    attribution: dict | None = None,
) -> tuple[dict[str, Any], list[ActionLogEntry]]:
    """
    Evaluate all actions for a single supplier, producing an execution log.

    Working state starts as global_context merged with supplier_attrs
    (supplier_attrs take precedence on collision).

    Action semantics:
      ALI   — state[out] = _apply_operator(state[in1], op, literal(in2))
      AL    — state[out] = _apply_operator(state[in1], op, state[in2])
      OSLM  — same as AL/ALI but only executed when the WHEN condition is True
      SRM   — same as AL/ALI but only executed when the WHEN condition is True

    For every action an :class:`ActionLogEntry` is emitted capturing the actual
    runtime values of all parameters read at the moment of evaluation.  When a
    WHEN condition fails the entry has ``skipped=True`` and
    ``output_value_before/after`` are both ``None``.

    Returns:
        (final_state, log_entries)
    """
    state: dict[str, Any] = {**global_context, **supplier_attrs}
    log_entries: list[ActionLogEntry] = []

    for action_index, action in enumerate(sorted_actions):
        typ: str = action[0]
        in1: str = action[1] if len(action) > 1 else "_"
        in2_raw: str = str(action[2]) if len(action) > 2 else "_"
        op: str = action[3] if len(action) > 3 else "="
        out: str = action[4] if len(action) > 4 else "_"
        when_expr: str | None = str(action[5]) if len(action) > 5 else None

        # Attribution lookup for this action
        attr_info = (attribution or {}).get(action_index, {})
        rule_id: str = attr_info.get("rule_id", "UNKNOWN")
        rule_description: str = attr_info.get("rule_description", "")

        # Snapshot inputs at evaluation time (before any state mutation)
        input_values: dict[str, Any] = {}
        if in1 != "_":
            input_values[in1] = state.get(in1)
        if typ == "ALI":
            if in2_raw != "_":
                input_values["immediate"] = _parse_literal(in2_raw)
        else:
            if in2_raw != "_":
                input_values[in2_raw] = state.get(in2_raw)

        when_evaluated = False
        when_passed = True
        skipped = False
        output_key: str | None = out if out != "_" else None
        output_value_before: Any = state.get(out) if out != "_" and out in state else None
        output_value_after: Any = None
        # Initialise here so they are always defined when ActionLogEntry is created,
        # even if the action is a no-op (out == "_") or WHEN suppressed it early.
        when_err: str | None = None
        act_err: str | None = None
        op_applied: bool = True

        if out == "_":
            # No-op action: nothing to write
            skipped = True
        else:
            # WHEN gate — applies to all action types
            if when_expr is not None:
                when_evaluated = True
                try:
                    when_passed = bool(_eval_when(when_expr, state))
                except Exception as exc:
                    # ISSUE-004/023: capture the exception so log consumers can
                    # distinguish "condition evaluated to False" from "exception
                    # during evaluation".  Both result in skipped=True.
                    when_passed = False
                    when_err = str(exc)
                if not when_passed:
                    skipped = True

            if not skipped:
                # ISSUE-015: use _resolve_param1 which gives numeric/boolean literals
                # priority over state-key lookup for in_param1.
                lhs: Any = _resolve_param1(in1, state) if in1 != "_" else None
                if typ == "ALI":
                    rhs: Any = _parse_literal(in2_raw) if in2_raw != "_" else None
                else:
                    rhs = _resolve_value(in2_raw, state) if in2_raw != "_" else None

                # ISSUE-002: if a real (non-"_") parameter resolved to None it
                # means the state key holds None.  Silently assigning rhs/lhs
                # instead of skipping inflates scores (e.g. quality_score=None
                # → _rep_quality = weight constant instead of 0).  Skip instead.
                if in1 != "_" and lhs is None:
                    skipped = True
                    act_err = f"in_param1 key {in1!r} resolved to None in state — action skipped"
                elif in2_raw != "_" and rhs is None:
                    skipped = True
                    act_err = f"in_param2 key {in2_raw!r} resolved to None in state — action skipped"
                elif lhs is None and rhs is None:
                    # Both params are "_" — no-op
                    skipped = True
                else:
                    if lhs is None:
                        # in1 == "_": copy rhs to output (assignment semantics)
                        result: Any = rhs
                        op_applied = False  # ISSUE-014: operator was not applied
                    elif rhs is None:
                        # in2_raw == "_": copy lhs to output (assignment semantics)
                        result = lhs
                        op_applied = False  # ISSUE-014
                    else:
                        try:
                            result = _apply_operator(lhs, op, rhs)
                        except Exception as exc:
                            # ISSUE-005: capture error so the log distinguishes
                            # a silent skip from a real computation failure.
                            skipped = True
                            result = None
                            act_err = str(exc)

                    if not skipped:
                        state[out] = result
                        output_value_after = result

        log_entries.append(ActionLogEntry(
            action_index=action_index,
            rule_id=rule_id,
            rule_description=rule_description,
            action_type=typ,
            action_tuple=action,
            when_condition=when_expr,
            when_evaluated=when_evaluated,
            when_passed=when_passed,
            input_values=input_values,
            output_key=output_key,
            output_value_before=output_value_before if not skipped else None,
            output_value_after=output_value_after,
            skipped=skipped,
            when_error=when_err if when_evaluated else None,
            action_error=act_err,
            operator_applied=op_applied,
        ))

    return state, log_entries


# ---------------------------------------------------------------------------
# 5. Full evaluation pipeline
# ---------------------------------------------------------------------------

def run_procurement_evaluation(
    request: dict[str, Any],
    schema: list[tuple],
    sorted_actions: list[tuple],
    suppliers: list[SupplierRecord],
    fix_in_keys: set[str],
    pricing_index: dict | None = None,
    attribution: dict | None = None,
    field_impact_map: dict | None = None,
    escalation_rules: list[dict] | None = None,
) -> tuple[dict[str, Any], RequestExecutionLog]:
    """
    End-to-end procurement evaluation with full execution logging.

    Steps:
      1. Build global context from request + schema.
      2. For every supplier check exclusion gates and log the result.
      3. For each surviving supplier, run evaluate_actions (returning a log).
      4. Sort results and assemble the RequestExecutionLog.

    Returns:
      (result_dict, execution_log)

      result_dict contains:
        "global_outputs":   dict[str, Any]  — fix_out keys (excl. "rank") from
                            the first evaluated supplier's final state.
        "supplier_results": list of (identity_dict, rank, full_state_dict)
                            sorted by (cost_rank_score DESC, reputation_score DESC).

      execution_log is a :class:`RequestExecutionLog` capturing everything that
      happened — including excluded suppliers and per-action attribution.
    """
    # ISSUE-022: build_global_context raises KeyError when fix_in fields are
    # missing from the request.  Catch the error and build a partial context so
    # the evaluation can still run; the escalation engine will surface the missing
    # fields as missing_field triggers rather than crashing the entire pipeline.
    try:
        global_context = build_global_context(request, schema)
    except KeyError:
        global_context = {
            entry[0]: request[entry[0]]
            for entry in schema
            if entry[1] == "fix_in"
            and (len(entry) <= 3 or entry[3] != "supplier_matrix")
            and entry[0] in request
        }
    fix_out_keys: set[str] = {entry[0] for entry in schema if entry[1] == "fix_out"}

    supplier_results: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
    supplier_logs: list[SupplierLog] = []

    for supplier in suppliers:
        identity = supplier["identity"]
        attrs = supplier["attributes"]
        supplier_id = str(identity.get("supplier_id", ""))
        supplier_name = str(identity.get("supplier_name", ""))
        category_l2 = str(identity.get("category_l2", ""))

        exclusion_reason = _check_exclusion(supplier, global_context, pricing_index)
        if exclusion_reason:
            supplier_logs.append(SupplierLog(
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                category_l2=category_l2,
                pricing_resolved={},
                action_logs=[],
                final_state={},
                final_cost_rank_score=None,
                final_reputation_score=None,
                final_compliance_score=None,
                final_normalized_rank=None,
                excluded=True,
                exclusion_reason=exclusion_reason,
            ))
            continue

        # Build supplier attrs with pricing injected
        new_attrs: dict[str, Any] = dict(attrs)
        new_attrs["serves_delivery_country"] = True
        # Compliance fields — initialized here so OSLM actions can modify them.
        # compliance_score is a multiplicative penalty applied to normalized_rank.
        # excluded signals hard gate violations detected by the action pipeline.
        new_attrs["compliance_score"] = 1.0
        new_attrs["excluded"] = False
        pricing_resolved: dict = {}
        if pricing_index is not None:
            pricing = resolve_supplier_pricing(identity, pricing_index, global_context)
            if pricing:
                new_attrs.update(pricing)
                pricing_resolved = pricing

        final_state, action_logs = evaluate_actions(
            sorted_actions,
            global_context,
            new_attrs,
            fix_in_keys,
            attribution,
        )

        # Check if the action pipeline excluded this supplier (hard compliance gate)
        if final_state.get("excluded") is True:
            supplier_logs.append(SupplierLog(
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                category_l2=category_l2,
                pricing_resolved=pricing_resolved,
                action_logs=action_logs,
                final_state=final_state,
                final_cost_rank_score=None,
                final_reputation_score=None,
                final_compliance_score=None,
                final_normalized_rank=None,
                excluded=True,
                exclusion_reason="Excluded by compliance rule (action pipeline)",
            ))
            continue

        rank = final_state.get("rank", 0)
        supplier_results.append((dict(identity), rank, final_state))

        supplier_logs.append(SupplierLog(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            category_l2=category_l2,
            pricing_resolved=pricing_resolved,
            action_logs=action_logs,
            final_state=final_state,
            final_cost_rank_score=final_state.get("cost_rank_score"),
            final_reputation_score=final_state.get("reputation_score"),
            final_compliance_score=final_state.get("compliance_score"),
            final_normalized_rank=None,  # set after cross-supplier normalization below
            excluded=False,
            exclusion_reason=None,
        ))

    # ---------------------------------------------------------------------------
    # Text compliance: check free-text request for explicit/implicit directives.
    # One LLM call covers all suppliers; returns hard exclusions and soft scores.
    request_text: str | None = str(request.get("request_text") or "").strip() or None
    if request_text:
        from text_compliance import update_compliance_scores  # local import avoids startup cost
        text_excluded_ids = update_compliance_scores(request_text, supplier_results)

        if text_excluded_ids:
            # Move text-excluded suppliers out of supplier_results.
            # Update their SupplierLog entry to reflect the new exclusion status.
            _log_by_id = {sl.supplier_id: sl for sl in supplier_logs if not sl.excluded}
            remaining: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
            for identity, rank, final_state in supplier_results:
                sid = identity.get("supplier_id", "")
                if sid in text_excluded_ids:
                    reason = final_state.get(
                        "text_exclusion_reason", "Excluded by text compliance check"
                    )
                    sl = _log_by_id.get(sid)
                    if sl is not None:
                        sl.excluded = True
                        sl.exclusion_reason = reason
                        sl.final_compliance_score = None
                        sl.final_normalized_rank  = None
                else:
                    remaining.append((identity, rank, final_state))
            supplier_results = remaining

    # ---------------------------------------------------------------------------
    # Compute normalized_rank (0–1, cross-request comparable) for each supplier.
    #
    # Weights:
    #   95.0%  cost_score       — z-score sigmoid against blended market average,
    #                             scaled by historical std_dev so tight-price categories
    #                             spread scores further than high-variance ones; with
    #                             exponential budget penalty hitting 0 at 5 % over budget
    #    2.5%  reputation_norm  — weighted quality/risk/ESG composite, capped to [0,1]
    #    2.5%  historic_score   — shrunk Bayesian composite (award rate / rank / savings)
    #
    # Cost score detail
    # -----------------
    #   z = (blended_avg - unit_price) / hist_std_dev
    #   base_cost_score = sigmoid(k * z) = 1 / (1 + exp(-k * z))
    #     → 0.50 when unit_price = blended_avg  (market-average supplier)
    #     → → 1.0 as unit_price falls below avg (cheaper → better)
    #     → → 0.0 as unit_price rises above avg (more expensive → worse)
    #   k = 1.5 (steepness); low hist_std_dev amplifies z → wider score spread
    #
    #   Fallback when hist_std_dev unavailable: min(1.0, blended_avg / unit_price)
    #
    #   budget_penalty = 1.0                           if cost_total ≤ budget
    #                  = exp(-10 * overage / 0.05)     if 0 < overage < 5 %
    #                  = 0.0                           if overage ≥ 5 %
    #     where overage = (cost_total - budget) / budget
    #
    #   cost_score = base_cost_score * budget_penalty
    # ---------------------------------------------------------------------------
    _BUDGET_OVERAGE_CAP = 0.05   # fraction: 5 % over budget → penalty = 0
    _PENALTY_K          = 10     # exp(-10) ≈ 4.5e-5 at cap → effectively 0
    _SIGMOID_K          = 1.5    # steepness of sigmoid; higher = sharper spread

    # Historical stats + blended average
    category_l1 = str(global_context.get("category_l1", ""))
    category_l2 = str(global_context.get("category_l2", ""))
    hist_store  = load_historical_store()
    hist_avg, hist_std_dev, n_hist = get_historical_stats(
        category_l1, category_l2, hist_store
    )

    current_unit_prices = [
        float(fs.get("unit_price"))
        for _, _, fs in supplier_results
        if fs.get("unit_price") is not None and float(fs.get("unit_price") or 0) > 0
    ]

    if hist_avg is not None and current_unit_prices:
        blended_avg: float | None = (
            (hist_avg * n_hist + sum(current_unit_prices))
            / (n_hist + len(current_unit_prices))
        )
    elif hist_avg is not None:
        blended_avg = hist_avg
    elif current_unit_prices:
        blended_avg = sum(current_unit_prices) / len(current_unit_prices)
    else:
        blended_avg = None

    budget = float(global_context.get("budget") or 0)

    for i, (identity, _raw_rank, final_state) in enumerate(supplier_results):
        # --- Base cost deviation score ---
        unit_price = float(final_state.get("unit_price") or 0)
        if blended_avg is not None and unit_price > 0:
            if hist_std_dev:
                # Z-score sigmoid: amplifies differences more in tight-price categories
                z = (blended_avg - unit_price) / hist_std_dev
                base_cost_score = 1.0 / (1.0 + math.exp(-_SIGMOID_K * z))
            else:
                # Fallback: simple ratio (no variance data)
                base_cost_score = min(1.0, blended_avg / unit_price)
        else:
            base_cost_score = 0.5  # neutral when no price data at all

        # --- Exponential budget penalty ---
        cost_total = float(final_state.get("cost_total") or 0)
        if budget > 0 and cost_total > 0:
            overage = (cost_total - budget) / budget
            if overage <= 0:
                penalty = 1.0
            elif overage >= _BUDGET_OVERAGE_CAP:
                penalty = 0.0
            else:
                penalty = math.exp(-_PENALTY_K * overage / _BUDGET_OVERAGE_CAP)
        else:
            penalty = 1.0

        cost_score = base_cost_score * penalty

        # --- Reputation ---
        rep_raw = float(final_state.get("reputation_score") or 0)
        # ISSUE-016: log when clamping occurs so auditors can distinguish a
        # legitimately high/low score from a data-quality problem.
        if not (0.0 <= rep_raw <= 100.0):
            final_state["_reputation_clamped"] = True
            final_state["_reputation_raw"]     = rep_raw
        reputation_norm = max(0.0, min(rep_raw, 100.0)) / 100.0

        # --- Historic score (from ingest_historical_awards.py) ---
        # Composed of: award_rate (65%), 1/avg_rank (20%), savings_pct (15%),
        # shrunk toward the neutral prior 0.5 based on data reliability.
        # Returns 0.5 (neutral) when no historical data exists for this supplier
        # in this category — neither a boost nor a penalty.
        supplier_id_for_hist = identity.get("supplier_id", "")
        historic_score = get_supplier_historic_score(
            supplier_id_for_hist, category_l1, category_l2, hist_store
        )
        final_state["_historic_score_is_dummy"] = False

        # --- Compliance multiplier ---
        # Clamped to [0, 1]. OSLM actions reduce this from 1.0 for soft
        # violations. Hard violations set excluded=True and are handled above.
        compliance_score = max(0.0, min(1.0, float(final_state.get("compliance_score") or 1.0)))

        raw_rank = 0.95 * cost_score + 0.025 * reputation_norm + 0.025 * historic_score
        normalized_rank = round(raw_rank * compliance_score, 6)

        # --- Preferred supplier bonus ---
        # A supplier on the org's preferred list (preferred_supplier=True in master
        # data) receives a 10 % boost, capped at 1.0, to reflect existing trust and
        # reduced onboarding risk. This is distinct from preferred_supplier_mentioned
        # (the requester's stated preference) which is handled via text compliance.
        if final_state.get("preferred_supplier") is True:
            rank_before_bonus = normalized_rank
            normalized_rank = round(min(1.0, normalized_rank * 1.1), 6)
            # ISSUE-021: record rank without bonus so _flag_preferred_bonus_decisive
            # can determine whether the bonus flipped the ranking.
            final_state["preferred_supplier_bonus_applied"] = True
            final_state["rank_without_preferred_bonus"]     = rank_before_bonus

        final_state["compliance_score"]       = compliance_score
        final_state["normalized_rank"]        = normalized_rank
        final_state["blended_avg_unit_price"] = round(blended_avg, 6) if blended_avg is not None else None
        final_state["budget_penalty"]         = round(penalty, 6)
        supplier_results[i] = (identity, normalized_rank, final_state)

    # Back-fill final_normalized_rank on SupplierLog entries (matched by supplier_id)
    rank_by_id = {
        identity.get("supplier_id"): final_state.get("normalized_rank")
        for identity, _, final_state in supplier_results
    }
    for sl in supplier_logs:
        if not sl.excluded:
            sl.final_normalized_rank = rank_by_id.get(sl.supplier_id)

    # Sort by normalized_rank DESC
    supplier_results.sort(key=lambda x: x[1], reverse=True)

    # ISSUE-006: compute global_outputs from ALL surviving suppliers rather than
    # blindly trusting the first one.  Supplier #1 may have had different
    # WHEN-gated actions fire than subsequent suppliers, producing a different
    # policy-level fix_out value (e.g. min_supplier_quotes) for the same request.
    # Collecting values from every supplier lets us detect inconsistencies and
    # always use the value that the majority of suppliers agree on.
    global_outputs: dict[str, Any] = {}
    for k in fix_out_keys:
        if k == "rank":
            continue
        vals = [fs[k] for _, _, fs in supplier_results if k in fs]
        if not vals:
            # Fallback: check excluded-but-evaluated suppliers for global policy fields.
            vals = [sl.final_state[k] for sl in supplier_logs if k in sl.final_state]
        if vals:
            global_outputs[k] = vals[0]

    execution_log = RequestExecutionLog(
        request_id=str(request.get("request_id", "")),
        timestamp=datetime.now(timezone.utc).isoformat(),
        global_context_snapshot=dict(global_context),
        supplier_logs=supplier_logs,
        global_action_logs=[],
    )

    # Result-quality flags and confidence score — computed first so that
    # confidence_assessment can be passed into the escalation engine.
    from result_flags import evaluate_flags, compute_confidence_score  # local import avoids circular

    # ISSUE-009: HIGH_EXCLUSION_RATE should use the category-matched supplier
    # pool as its denominator, not the full unfiltered pool.  Using len(suppliers)
    # dilutes the fraction when the pool contains many off-category suppliers.
    #
    # n_category_matched  = suppliers that were within scope (category + country)
    # n_compliance_excluded = category-matched suppliers excluded by compliance gates
    #
    # Scope-only exclusions (wrong category/country) are NOT counted in either
    # number because they are expected and do not signal over-specified policy.
    n_category_matched   = sum(1 for sl in supplier_logs
                               if not _is_scope_exclusion(sl.exclusion_reason))
    n_compliance_excluded = sum(1 for sl in supplier_logs
                                if sl.excluded and not _is_scope_exclusion(sl.exclusion_reason))

    # Build a flat dict list covering ALL suppliers (incl. excluded) for preferred-supplier flags.
    all_supplier_log_dicts = [
        {
            "supplier_name":         sl.supplier_name,
            "supplier_id":           sl.supplier_id,
            "excluded":              sl.excluded,
            "exclusion_reason":      sl.exclusion_reason,
            "normalized_rank":       sl.final_normalized_rank,
            "text_compliance_score": sl.final_state.get("text_compliance_score"),
        }
        for sl in supplier_logs
    ]
    execution_log.flag_assessment = evaluate_flags(
        request=request,
        supplier_results=supplier_results,
        n_total_suppliers=n_category_matched,
        n_excluded=n_compliance_excluded,
        all_supplier_logs=all_supplier_log_dicts,
    )

    confidence_assessment = compute_confidence_score(
        request=request,
        supplier_results=supplier_results,
        n_total_suppliers=n_category_matched,
        n_excluded=n_compliance_excluded,
        hist_n_data_points=n_hist,
        hist_std_dev=hist_std_dev,
        hist_avg=hist_avg,
    )
    execution_log.confidence_assessment = confidence_assessment

    # Run escalation assessment — after confidence so CR-C rules can fire.
    # field_impact_map and escalation_rules are optional so callers that haven't
    # wired them up yet still work without changes.
    if field_impact_map is not None:
        from escalation_engine import evaluate_escalations  # local import avoids circular
        outcome_for_esc = {
            "supplier_results": supplier_results,
            "global_outputs":   global_outputs,
        }
        execution_log.escalation_assessment = evaluate_escalations(
            request=request,
            outcome=outcome_for_esc,
            fix_in_keys=fix_in_keys,
            field_impact_map=field_impact_map,
            escalation_rules=escalation_rules or [],
            confidence_assessment=confidence_assessment,
        )
        # Strip escalate_to_* booleans from global_outputs — they are now
        # represented as structured EscalationRecord objects in the assessment.
        # Keeping them would expose the same information twice in different formats.
        for _k in [k for k in global_outputs if k.startswith("escalate_to_")]:
            del global_outputs[_k]

    return (
        {
            "global_outputs":         global_outputs,
            "supplier_results":       supplier_results,
            "escalation_assessment":  execution_log.escalation_assessment,
            "flag_assessment":        execution_log.flag_assessment,
            "confidence_assessment":  confidence_assessment,
        },
        execution_log,
    )


# ---------------------------------------------------------------------------
# 6. Log rendering and persistence
# ---------------------------------------------------------------------------


def render_log(log: RequestExecutionLog) -> str:
    """
    Produce a human-readable plaintext report for a :class:`RequestExecutionLog`.

    Structure::

        REQUEST: {request_id}
        Timestamp: {timestamp}
        Context: {global_context_snapshot}

        SUPPLIER: {supplier_name} ({supplier_id}) — {category_l2}
        Pricing: {unit_price} {currency} x {quantity} units = {cost_total}
        Final scores: cost_rank={cost_rank_score}, reputation={reputation_score}

          [RULE: {rule_id}] {rule_description}
            Action: {action_type} {action_tuple}
            Inputs: {param} = {value}, ...
            WHEN: {condition} → PASSED|FAILED|N/A
            Result: {output_key} {before} → {after}    [SKIPPED if when failed]

    Excluded suppliers are shown with their exclusion reason and no action blocks.
    """
    lines: list[str] = []
    lines.append(f"REQUEST: {log.request_id}")
    lines.append(f"Timestamp: {log.timestamp}")
    lines.append(f"Context: {log.global_context_snapshot}")
    lines.append("")

    quantity = log.global_context_snapshot.get("quantity", "?")

    for sl in log.supplier_logs:
        if sl.excluded:
            lines.append(
                f"SUPPLIER: {sl.supplier_name} ({sl.supplier_id}) — {sl.category_l2}"
                f"  [EXCLUDED: {sl.exclusion_reason}]"
            )
            lines.append("")
            continue

        unit_price = sl.pricing_resolved.get("unit_price", "N/A")
        currency = sl.pricing_resolved.get("currency", "")
        cost_total = sl.final_state.get("cost_total", "N/A")

        lines.append(
            f"SUPPLIER: {sl.supplier_name} ({sl.supplier_id}) — {sl.category_l2}"
        )
        lines.append(
            f"Pricing: {unit_price} {currency} x {quantity} units = {cost_total}"
        )
        lines.append(
            f"Final scores: normalized_rank={sl.final_normalized_rank},"
            f" cost_rank={sl.final_cost_rank_score},"
            f" reputation={sl.final_reputation_score},"
            f" compliance={sl.final_compliance_score}"
        )
        lines.append("")

        for entry in sl.action_logs:
            if entry.rule_id == "UNKNOWN" and not entry.rule_description:
                # Skip no-op entries with no attribution to keep the report readable
                continue
            lines.append(f"  [RULE: {entry.rule_id}] {entry.rule_description}")
            lines.append(f"    Action: {entry.action_type} {entry.action_tuple}")

            inputs_str = ", ".join(
                f"{k} = {v}" for k, v in entry.input_values.items()
            )
            lines.append(f"    Inputs: {inputs_str}")

            if entry.when_condition is None:
                when_str = "N/A"
            elif entry.when_passed:
                when_str = f"{entry.when_condition} → PASSED"
            else:
                when_str = f"{entry.when_condition} → FAILED"
            lines.append(f"    WHEN: {when_str}")

            if entry.skipped:
                lines.append(
                    f"    Result: {entry.output_key} [SKIPPED]"
                )
            else:
                lines.append(
                    f"    Result: {entry.output_key}"
                    f" {entry.output_value_before} → {entry.output_value_after}"
                )
            lines.append("")

    # Result-quality flags
    flags = log.flag_assessment
    if flags is not None and flags.flags:
        lines.append("=" * 60)
        lines.append("RESULT FLAGS")
        lines.append("=" * 60)
        for f in flags.flags:
            icon = "⚠  WARNING" if f.severity == "warning" else "ℹ  INFO"
            lines.append(f"  [{icon}] [{f.flag_id}]")
            lines.append(f"    {f.description}")
            lines.append("")

    # Escalation assessment summary
    esc = log.escalation_assessment
    if esc is not None and (esc.triggers or esc.context_notes):
        lines.append("=" * 60)
        lines.append("ESCALATION ASSESSMENT")
        lines.append("=" * 60)
        for note in esc.context_notes:
            lines.append(f"  [CONTEXT] {note}")
        if esc.context_notes:
            lines.append("")
        for t in esc.triggers:
            flag = "🔴 BLOCKING" if t.severity == "blocking" else "🟡 ADVISORY" if t.severity == "advisory" else "ℹ  LOGGED"
            lines.append(f"  [{flag}] [{t.trigger_id}] impact={t.rank_impact:.2f}")
            lines.append(f"    {t.description}")
            if t.suppression_reason:
                lines.append(f"    ⚑ {t.suppression_reason}")
            if t.escalate_to:
                lines.append(f"    → Escalate to: {t.escalate_to}")
            lines.append("")
    elif esc is not None:
        lines.append("ESCALATION ASSESSMENT: no triggers")
        lines.append("")

    return "\n".join(lines)


def _log_to_json_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses and tuples for JSON serialisation."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            k: _log_to_json_serializable(v)
            for k, v in dataclasses.asdict(obj).items()
        }
    if isinstance(obj, tuple):
        return [_log_to_json_serializable(x) for x in obj]
    if isinstance(obj, list):
        return [_log_to_json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _log_to_json_serializable(v) for k, v in obj.items()}
    return obj


def save_log(log: RequestExecutionLog, path: str) -> None:
    """
    Persist a :class:`RequestExecutionLog` to disk as JSON:

    - ``{path}.json`` — raw JSON (dataclass serialised; tuples become lists)
    """
    serialisable = _log_to_json_serializable(log)
    with open(f"{path}.json", "w", encoding="utf-8") as fh:
        json.dump(serialisable, fh, indent=2, default=str)
