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
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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
        payload = json.load(fh)

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
    """
    stripped = token.strip()
    if stripped in state:
        return state[stripped]
    return _parse_literal(stripped)


def _apply_operator(lhs: Any, op: str, rhs: Any) -> Any:
    """
    Apply *op* between *lhs* and *rhs* using explicit conditional logic.
    No eval() is used.
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
        case _:
            raise ValueError(f"Unknown operator: {op!r}")


# ---------------------------------------------------------------------------
# WHEN condition evaluator
# ---------------------------------------------------------------------------

def _tokenize_when(expr: str) -> list[str]:
    """
    Split a WHEN expression into a flat token list.
    Inserts whitespace around operators and parentheses so identifiers/literals
    don't fuse with them.  Two-char operators are listed first in the
    alternation so they are matched before their single-char prefixes.
    """
    spaced = re.sub(r"(>=|<=|!=|[><=()])", r" \1 ", expr)
    return [t for t in spaced.split() if t]


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

        if out == "_":
            # No-op action: nothing to write
            skipped = True
        else:
            # WHEN gate — applies to OSLM and SRM
            if typ in ("OSLM", "SRM") and when_expr is not None:
                when_evaluated = True
                try:
                    when_passed = bool(_eval_when(when_expr, state))
                except Exception:
                    when_passed = False
                if not when_passed:
                    skipped = True

            if not skipped:
                lhs: Any = _resolve_value(in1, state) if in1 != "_" else None
                if typ == "ALI":
                    rhs: Any = _parse_literal(in2_raw) if in2_raw != "_" else None
                else:
                    rhs = _resolve_value(in2_raw, state) if in2_raw != "_" else None

                if lhs is None and rhs is None:
                    skipped = True
                else:
                    if lhs is None:
                        result: Any = rhs
                    elif rhs is None:
                        result = lhs
                    else:
                        try:
                            result = _apply_operator(lhs, op, rhs)
                        except Exception:
                            skipped = True
                            result = None

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
    global_context = build_global_context(request, schema)
    fix_out_keys: set[str] = {entry[0] for entry in schema if entry[1] == "fix_out"}

    supplier_results: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
    first_global_state: dict[str, Any] = {}
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
                excluded=True,
                exclusion_reason=exclusion_reason,
            ))
            continue

        # Build supplier attrs with pricing injected
        new_attrs: dict[str, Any] = dict(attrs)
        new_attrs["serves_delivery_country"] = True
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
        rank = final_state.get("rank", 0)
        supplier_results.append((dict(identity), rank, final_state))

        if not first_global_state:
            first_global_state = final_state

        supplier_logs.append(SupplierLog(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            category_l2=category_l2,
            pricing_resolved=pricing_resolved,
            action_logs=action_logs,
            final_state=final_state,
            final_cost_rank_score=final_state.get("cost_rank_score"),
            final_reputation_score=final_state.get("reputation_score"),
            excluded=False,
            exclusion_reason=None,
        ))

    # Primary sort: (cost_rank_score DESC, reputation_score DESC)
    supplier_results.sort(
        key=lambda x: (
            x[2].get("cost_rank_score", 0),
            x[2].get("reputation_score", 0),
        ),
        reverse=True,
    )

    global_outputs: dict[str, Any] = {
        k: first_global_state[k]
        for k in fix_out_keys
        if k != "rank" and k in first_global_state
    }

    execution_log = RequestExecutionLog(
        request_id=str(request.get("request_id", "")),
        timestamp=datetime.now(timezone.utc).isoformat(),
        global_context_snapshot=dict(global_context),
        supplier_logs=supplier_logs,
        global_action_logs=[],
    )

    return (
        {
            "global_outputs": global_outputs,
            "supplier_results": supplier_results,
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
            f"Final scores: cost_rank={sl.final_cost_rank_score},"
            f" reputation={sl.final_reputation_score}"
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
    Persist a :class:`RequestExecutionLog` to disk in two formats:

    - ``{path}.json`` — raw JSON (dataclass serialised; tuples become lists)
    - ``{path}.txt``  — human-readable plaintext from :func:`render_log`
    """
    serialisable = _log_to_json_serializable(log)
    with open(f"{path}.json", "w", encoding="utf-8") as fh:
        json.dump(serialisable, fh, indent=2, default=str)

    with open(f"{path}.txt", "w", encoding="utf-8") as fh:
        fh.write(render_log(log))
