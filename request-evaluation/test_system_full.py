"""
test_system_full.py — Full system integration test: processes all requests from
data/requests.json through the complete ChainIQ procurement pipeline.

Persistence strategy
--------------------
  - Rules actions (approval_thresholds / category_rules / escalation_rules):
    get_or_build_actions_store() — hash-invalidated against data/ folder contents.
    Cached in stores/{ruleset}_actions.json and reused across runs.

  - Ranking actions: wrapped in the same hash-check pattern; cached in
    stores/ranking_actions.json and only regenerated when data/ changes.

  - Combined sorted pipeline: rebuilt only when any action source was stale,
    using build_full_action_pipeline() from supplier_matrix.py.

Results are written to stores/system_test_results.json after each run so the
output can be inspected without re-running the full pipeline.

Run with:
    python -m pytest test_system_full.py -s -v
or:
    python test_system_full.py
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

from actions_store import get_or_build_actions_store, hash_data_folder
from supplier_matrix import (
    add_ranking_schema_entries,
    build_full_action_pipeline,
    generate_ranking_actions,
    load_pricing_index,
    load_schema,
    load_suppliers,
    run_procurement_evaluation,
    save_log,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent  # project root
_MODULE_DIR = Path(__file__).parent   # request-evaluation/
DATA_DIR = _ROOT / "data"
STORE_DIR = _ROOT / "stores"
LOGS_DIR = STORE_DIR / "execution_logs"
SCHEMA_PATH = _MODULE_DIR / "start_dict.csv"
REQUESTS_PATH = DATA_DIR / "requests.json"
SUPPLIERS_PATH = DATA_DIR / "suppliers.csv"
PRICING_PATH = DATA_DIR / "pricing.csv"
RANKING_STORE_PATH = STORE_DIR / "ranking_actions.json"
RESULTS_PATH = STORE_DIR / "system_test_results.json"


# ---------------------------------------------------------------------------
# Ranking actions persistence (hash-invalidated, mirrors actions_store pattern)
# ---------------------------------------------------------------------------

def _strip_action_quotes(actions: list[tuple]) -> list[tuple]:
    """
    Normalise action tuples produced by _parse_actions_from_llm, which wraps
    every field in single quotes (e.g. "'*'" instead of "*").  Strip those
    surrounding quotes so the fields match what _apply_operator / evaluate_actions
    expect.
    """
    def _unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s

    return [tuple(_unquote(str(field)) for field in action) for action in actions]

def _ranking_store_path(path: Path = RANKING_STORE_PATH) -> Path:
    return path


def _load_raw_ranking_store(path: Path = RANKING_STORE_PATH) -> dict | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_ranking_store(
    ranking_actions: list[tuple],
    data_hash: str,
    path: Path = RANKING_STORE_PATH,
    attribution: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_hash": data_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ranking_actions": [list(a) for a in ranking_actions],
        "attribution": {str(k): v for k, v in (attribution or {}).items()},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def get_or_build_ranking_actions(
    schema: list[tuple],
    data_hash: str,
) -> tuple[list[tuple], dict, bool]:
    """
    Return cached ranking actions if the data hash matches, otherwise rebuild
    via a single LLM call using generate_ranking_actions().

    Returns (ranking_actions, attribution, cache_hit).
    """
    raw = _load_raw_ranking_store()
    if raw is not None and raw.get("data_hash") == data_hash:
        actions = _strip_action_quotes([tuple(a) for a in raw["ranking_actions"]])
        attribution = {
            int(k): v for k, v in raw.get("attribution", {}).items()
        }
        return actions, attribution, True

    # Cache miss — call LLM to generate ranking actions
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )

    ranking_actions_raw, attribution = generate_ranking_actions(schema, client)
    ranking_actions = _strip_action_quotes(ranking_actions_raw)
    _save_ranking_store(ranking_actions, data_hash)
    return ranking_actions, attribution, False


# ---------------------------------------------------------------------------
# Session-scoped pipeline fixture — built once, reused by all tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pipeline():
    """
    Build (or load from cache) the full sorted action pipeline and all data.

    Returns a dict with:
        schema, fix_in_keys, sorted_actions, is_low_confidence,
        suppliers, pricing_index, cache_hits, data_hash
    """
    # Schema with ranking KPI extensions
    schema, fix_in_keys = load_schema(str(SCHEMA_PATH))
    schema = add_ranking_schema_entries(schema)

    data_hash = hash_data_folder(DATA_DIR)
    print(f"\n[pipeline] data_hash: {data_hash[:16]}...")

    # Rule action stores (hash-invalidated automatically)
    approval_store  = get_or_build_actions_store("approval_thresholds")
    category_store  = get_or_build_actions_store("category_rules")
    escalation_store = get_or_build_actions_store("escalation_rules")

    rules_actions = (
        list(approval_store["sorted_actions"])
        + list(category_store["sorted_actions"])
        + list(escalation_store["sorted_actions"])
    )

    # Ranking actions store
    ranking_actions, ranking_attribution, ranking_cache_hit = get_or_build_ranking_actions(
        schema, data_hash
    )

    # Build combined attribution from all rulesets
    rules_attribution: dict = {}
    offset = 0
    for store_key in ("approval_thresholds", "category_rules", "escalation_rules"):
        store_map = {
            "approval_thresholds": approval_store,
            "category_rules": category_store,
            "escalation_rules": escalation_store,
        }
        store = store_map[store_key]
        for k, v in store.get("attribution", {}).items():
            rules_attribution[int(k) + offset] = v
        offset += len(store["sorted_actions"])

    # Combine all actions into one topologically sorted pipeline
    sorted_actions, is_low_confidence, combined_attribution = build_full_action_pipeline(
        ranking_actions, rules_actions, fix_in_keys,
        ranking_attribution=ranking_attribution,
        rules_attribution=rules_attribution,
    )

    # Supplier and pricing data
    suppliers = load_suppliers(str(SUPPLIERS_PATH), [])
    pricing_index = load_pricing_index(str(PRICING_PATH))

    cache_hits = {
        "approval_thresholds": approval_store["cache_hit"],
        "category_rules":      category_store["cache_hit"],
        "escalation_rules":    escalation_store["cache_hit"],
        "ranking_actions":     ranking_cache_hit,
    }

    print(f"[pipeline] sorted_actions: {len(sorted_actions)}")
    print(f"[pipeline] is_low_confidence: {is_low_confidence}")
    print(f"[pipeline] suppliers: {len(suppliers)}")
    print(f"[pipeline] cache_hits: {cache_hits}")

    return {
        "schema":             schema,
        "fix_in_keys":        fix_in_keys,
        "sorted_actions":     sorted_actions,
        "is_low_confidence":  is_low_confidence,
        "attribution":        combined_attribution,
        "suppliers":          suppliers,
        "pricing_index":      pricing_index,
        "cache_hits":         cache_hits,
        "data_hash":          data_hash,
    }


# ---------------------------------------------------------------------------
# Request normalisation
# ---------------------------------------------------------------------------

def _days_until(date_str: str | None) -> int:
    if not date_str:
        return 0
    try:
        d = date.fromisoformat(date_str[:10])
        return max(0, (d - date.today()).days)
    except ValueError:
        return 0


def normalize_request(raw: dict[str, Any]) -> dict[str, Any] | None:
    delivery_countries: list[str] = raw.get("delivery_countries") or []
    delivery_country = delivery_countries[0] if delivery_countries else None
    if not delivery_country:
        return None

    category_l1 = raw.get("category_l1")
    category_l2 = raw.get("category_l2")
    if not category_l1 or not category_l2:
        return None

    budget = raw.get("budget_amount")
    currency = raw.get("currency")
    if budget is None or not currency:
        return None

    quantity = raw.get("quantity")
    if quantity is None:
        quantity = 1

    return {
        "request_id": raw.get("request_id"),
        "category_l1": category_l1,
        "category_l2": category_l2,
        "budget": budget,
        "currency": currency,
        "quantity": quantity,
        "amount_unit": raw.get("unit_of_measure") or "",
        "delivery_country": delivery_country,
        "days_until_required": _days_until(raw.get("required_by_date")),
        "preferred_supplier_mentioned": raw.get("preferred_supplier_mentioned"),
        "incumbent_supplier": raw.get("incumbent_supplier"),
        "data_residency_constraint": raw.get("data_residency_constraint", False),
        "esg_requirement": raw.get("esg_requirement", False),
        "request_text": raw.get("request_text") or "",
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def run_batch(
    requests_path: str | Path,
    pipeline: dict,
    logs_dir: Path = LOGS_DIR,
    max_workers: int = 50,
) -> list[dict]:
    """
    Load all requests from *requests_path* and evaluate them in parallel.

    Returns a list of dicts with keys:
      request_id, status ("ok" | "skipped" | "error"), supplier_count,
      ranking, global_outputs.
    """
    with open(requests_path, encoding="utf-8") as fh:
        raw_requests: list[dict] = json.load(fh)

    logs_dir.mkdir(parents=True, exist_ok=True)

    def _process_one(raw: dict) -> dict:
        request_id = raw.get("request_id", "<unknown>")
        request = normalize_request(raw)
        if request is None:
            return {"request_id": request_id, "status": "skipped",
                    "skip_reason": "failed normalization", "supplier_count": 0,
                    "ranking": [], "global_outputs": {}}
        try:
            outcome, exec_log = run_procurement_evaluation(
                request=request,
                schema=pipeline["schema"],
                sorted_actions=pipeline["sorted_actions"],
                suppliers=pipeline["suppliers"],
                fix_in_keys=pipeline["fix_in_keys"],
                pricing_index=pipeline["pricing_index"],
                attribution=pipeline.get("attribution"),
            )
            log_path = str(logs_dir / request_id)
            save_log(exec_log, log_path)

            supplier_results = outcome.get("supplier_results", [])
            ranking = [
                {
                    "position": pos + 1,
                    "supplier_id": identity.get("supplier_id"),
                    "supplier_name": identity.get("supplier_name"),
                    "normalized_rank": _safe_round(fs.get("normalized_rank")),
                    "cost_total": _safe_round(fs.get("cost_total")),
                }
                for pos, (identity, _, fs) in enumerate(supplier_results)
            ]
            return {
                "request_id": request_id,
                "status": "ok",
                "supplier_count": len(supplier_results),
                "ranking": ranking,
                "global_outputs": outcome.get("global_outputs", {}),
            }
        except Exception as exc:  # noqa: BLE001
            return {"request_id": request_id, "status": "error",
                    "error": str(exc), "supplier_count": 0,
                    "ranking": [], "global_outputs": {}}

    results: list[dict] = [None] * len(raw_requests)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_process_one, raw): idx
            for idx, raw in enumerate(raw_requests)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()

    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pipeline_loads(pipeline):
    """Verify the pipeline is ready: non-empty action set and supplier list."""
    assert len(pipeline["sorted_actions"]) > 0, "No actions in combined pipeline"
    assert len(pipeline["suppliers"]) > 0, "No suppliers loaded"
    assert len(pipeline["fix_in_keys"]) > 0, "No fix_in keys in schema"
    print(
        f"\n[test_pipeline_loads] "
        f"actions={len(pipeline['sorted_actions'])}, "
        f"suppliers={len(pipeline['suppliers'])}, "
        f"fix_in_keys={len(pipeline['fix_in_keys'])}"
    )


def test_action_stores_are_cached(pipeline):
    """Re-calling action store builders should always return cache hits."""
    store = get_or_build_actions_store("approval_thresholds")
    assert store["cache_hit"] is True, (
        "Second call to approval_thresholds store should be a cache hit"
    )

    store = get_or_build_actions_store("category_rules")
    assert store["cache_hit"] is True, (
        "Second call to category_rules store should be a cache hit"
    )

    store = get_or_build_actions_store("escalation_rules")
    assert store["cache_hit"] is True, (
        "Second call to escalation_rules store should be a cache hit"
    )

    _, _attr, hit = get_or_build_ranking_actions(pipeline["schema"], pipeline["data_hash"])
    assert hit is True, "Second call to ranking actions should be a cache hit"

    print("\n[test_action_stores_are_cached] All stores returned cache_hit=True ✓")


def test_all_requests(pipeline):
    """
    Process every request in data/requests.json through the full pipeline.

    Assertions:
    - No request evaluation raises an unhandled exception.
    - At least 20% of normalised requests produce at least one ranked supplier
      (intentionally lenient: many requests may target categories/regions with
      no matching supplier in the test data).
    - Results are written to stores/system_test_results.json.
    """
    results = run_batch(REQUESTS_PATH, pipeline, logs_dir=LOGS_DIR)

    skipped        = sum(1 for r in results if r["status"] == "skipped")
    evaluated      = sum(1 for r in results if r["status"] != "skipped")
    with_suppliers = sum(1 for r in results if r.get("supplier_count", 0) > 0)
    errors         = sum(1 for r in results if r["status"] == "error")

    # Persist results
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": {
            "data_hash":         pipeline["data_hash"][:16] + "...",
            "total_actions":     len(pipeline["sorted_actions"]),
            "is_low_confidence": pipeline["is_low_confidence"],
            "cache_hits":        pipeline["cache_hits"],
        },
        "stats": {
            "total_requests": len(results),
            "evaluated":      evaluated,
            "skipped":        skipped,
            "with_suppliers": with_suppliers,
            "errors":         errors,
            "match_rate":     round(with_suppliers / max(evaluated, 1), 4),
        },
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    # Print summary
    print(f"\n[test_all_requests] Summary:")
    print(f"  total_requests:  {len(results)}")
    print(f"  evaluated:       {evaluated}")
    print(f"  skipped:         {skipped}")
    print(f"  with_suppliers:  {with_suppliers}  "
          f"({with_suppliers / max(evaluated, 1):.0%} of evaluated)")
    print(f"  errors:          {errors}")
    print(f"  results written: {RESULTS_PATH}")
    print(f"  logs written:    {LOGS_DIR}/ ({evaluated - errors} × .json)")

    # Print all results
    for r in results:
        if r.get("status") == "ok" and r.get("supplier_count", 0) > 0:
            print(f"\n  {r['request_id']}")
            for s in r["ranking"]:
                print(f"    #{s['position']} {s['supplier_name']:40s} "
                      f"normalized_rank={s['normalized_rank']:>8}  cost={s['cost_total']}")

    # Assertions
    assert evaluated > 0, "No requests were evaluated"
    assert errors == 0 or errors / evaluated < 0.05, (
        f"Too many errors: {errors}/{evaluated} requests failed"
    )
    assert with_suppliers / max(evaluated, 1) >= 0.20, (
        f"Match rate too low: only {with_suppliers}/{evaluated} requests "
        f"found at least one supplier ({with_suppliers / evaluated:.0%} < 20%)"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_round(value: Any, ndigits: int = 4) -> Any:
    """Round a numeric value; return as-is if not numeric."""
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return value


def _run_with_escalation(pipeline: dict, request: dict) -> dict:
    """
    Run run_procurement_evaluation with field_impact_map and escalation_rules
    so that EscalationAssessment is populated in the outcome.

    The escalation_rules store is cached by the session-level fixture, so this
    call is cheap (cache hit).
    """
    from actions_store import get_or_build_actions_store
    from escalation_engine import build_field_impact_map

    escalation_store = get_or_build_actions_store("escalation_rules")
    escalation_rules = escalation_store.get("raw_rules", [])
    field_impact_map = build_field_impact_map(pipeline["sorted_actions"], pipeline["fix_in_keys"])

    outcome, _ = run_procurement_evaluation(
        request=request,
        schema=pipeline["schema"],
        sorted_actions=pipeline["sorted_actions"],
        suppliers=pipeline["suppliers"],
        fix_in_keys=pipeline["fix_in_keys"],
        pricing_index=pipeline["pricing_index"],
        attribution=pipeline.get("attribution"),
        field_impact_map=field_impact_map,
        escalation_rules=escalation_rules,
    )
    return outcome


def _flag_ids(outcome: dict) -> list[str]:
    """Return all flag_ids from the outcome's flag_assessment."""
    fa = outcome.get("flag_assessment")
    if fa is None:
        return []
    return [f.flag_id for f in fa.flags]


# ---------------------------------------------------------------------------
# Targeted scenario tests
# ---------------------------------------------------------------------------

def test_all_suppliers_over_budget(pipeline):
    """
    When every supplier's total cost exceeds the budget by more than 20%, the
    pipeline must still produce a ranked list (not crash / return empty) AND
    fire the BUDGET_INSUFFICIENT flag.

    Scenario: IT/Laptops, budget=400 CHF for 1 unit.
    Laptop unit prices in the dataset range from ~817 to ~1190 — all are well
    above the 400 × 1.20 = 480 threshold.
    """
    request = {
        "request_id": "TEST-BUDGET-001",
        "category_l1": "IT",
        "category_l2": "Laptops",
        "budget": 400.0,
        "currency": "CHF",
        "quantity": 1,
        "amount_unit": "devices",
        "delivery_country": "DE",
        "days_until_required": 30,
        "preferred_supplier_mentioned": None,
        "incumbent_supplier": None,
        "data_residency_constraint": False,
        "esg_requirement": False,
    }

    outcome = _run_with_escalation(pipeline, request)

    supplier_results = outcome.get("supplier_results", [])
    assert len(supplier_results) > 0, (
        "Expected ranked suppliers even when all are over budget, got empty list"
    )

    flags = _flag_ids(outcome)
    assert "BUDGET_INSUFFICIENT" in flags, (
        f"Expected BUDGET_INSUFFICIENT flag; got flags: {flags}"
    )

    # Every surviving supplier should have a cost_total above budget
    for identity, _, fs in supplier_results:
        cost = fs.get("cost_total")
        if cost is not None:
            assert float(cost) > 400.0, (
                f"Supplier {identity.get('supplier_name')} cost_total={cost} "
                f"unexpectedly within 400 budget"
            )

    print(
        f"\n[test_all_suppliers_over_budget] "
        f"{len(supplier_results)} suppliers ranked, flags={flags}"
    )


def test_quantity_zero_returns_validation_error(pipeline):
    """
    A request with quantity=0 must be rejected at the validation layer:
    status='error', validation_errors contains an entry for 'quantity',
    and ranked_suppliers is empty.

    Uses evaluate_request() so the full validation path is exercised.
    The pipeline module-level cache is already warm from the session fixture,
    so no LLM calls are made.
    """
    import json as _json
    from evaluate_request import evaluate_request

    request_json = _json.dumps({
        "request_id": "TEST-QTY-ZERO-001",
        "category_l1": "IT",
        "category_l2": "Laptops",
        "budget": 50000.0,
        "currency": "EUR",
        "quantity": 0,
        "amount_unit": "devices",
        "delivery_country": "DE",
        "days_until_required": 14,
        "preferred_supplier_mentioned": None,
        "incumbent_supplier": None,
        "data_residency_constraint": False,
        "esg_requirement": False,
    })

    result = _json.loads(evaluate_request(request_json))

    assert result["status"] == "error", (
        f"Expected status='error' for quantity=0, got {result['status']!r}"
    )
    assert result["ranked_suppliers"] == [], (
        "Expected empty ranked_suppliers for validation failure"
    )

    val_errors = result.get("validation_errors", [])
    qty_errors = [e for e in val_errors if e.get("field") == "quantity"]
    assert qty_errors, (
        f"Expected a validation_error for field='quantity'; got: {val_errors}"
    )

    print(
        f"\n[test_quantity_zero_returns_validation_error] "
        f"validation_errors={val_errors}"
    )


def test_single_qualified_supplier_flag_and_escalation(pipeline):
    """
    When only one supplier survives filtering, the pipeline must fire the
    SINGLE_QUALIFIED_SUPPLIER flag and raise at least one escalation record.

    Scenario: IT/Smartphones, delivery_country=UK.
    - Apple Business Channel (SUP-0004) covers UK.
    - Samsung Knox Devices (SUP-0005) does NOT cover UK.
    Only one non-restricted supplier remains, satisfying the flag condition.
    """
    request = {
        "request_id": "TEST-SINGLE-SUP-001",
        "category_l1": "IT",
        "category_l2": "Smartphones",
        "budget": 50000.0,
        "currency": "EUR",
        "quantity": 10,
        "amount_unit": "devices",
        "delivery_country": "UK",
        "days_until_required": 30,
        "preferred_supplier_mentioned": None,
        "incumbent_supplier": None,
        "data_residency_constraint": False,
        "esg_requirement": False,
    }

    outcome = _run_with_escalation(pipeline, request)

    supplier_results = outcome.get("supplier_results", [])
    assert len(supplier_results) <= 1, (
        f"Expected at most 1 surviving supplier for UK Smartphones; "
        f"got {len(supplier_results)}: "
        f"{[r[0].get('supplier_name') for r in supplier_results]}"
    )

    flags = _flag_ids(outcome)
    assert "SINGLE_QUALIFIED_SUPPLIER" in flags, (
        f"Expected SINGLE_QUALIFIED_SUPPLIER flag; got flags: {flags}"
    )

    escalation = outcome.get("escalation_assessment")
    assert escalation is not None, "Expected escalation_assessment in outcome"
    assert escalation.needs_escalation, (
        "Expected needs_escalation=True when only one supplier qualifies"
    )
    assert len(escalation.records) > 0, (
        "Expected at least one EscalationRecord for single-supplier scenario"
    )

    print(
        f"\n[test_single_qualified_supplier_flag_and_escalation] "
        f"survivors={len(supplier_results)}, flags={flags}, "
        f"escalation_records={len(escalation.records)}, "
        f"has_blocking={escalation.has_blocking}"
    )


def test_preferred_supplier_restricted_takes_precedence(pipeline):
    """
    When the requester names a restricted supplier as their preferred supplier,
    the restriction must take precedence: the supplier is excluded from the
    ranked list and the PREFERRED_SUPPLIER_EXCLUDED flag fires.

    Scenario: IT/Laptops, preferred_supplier_mentioned='Computacenter Devices'.
    SUP-0008 (Computacenter Devices) is marked is_restricted=True in this
    category in the dataset.
    """
    request = {
        "request_id": "TEST-PREF-RESTRICTED-001",
        "category_l1": "IT",
        "category_l2": "Laptops",
        "budget": 100000.0,
        "currency": "EUR",
        "quantity": 50,
        "amount_unit": "devices",
        "delivery_country": "DE",
        "days_until_required": 30,
        "preferred_supplier_mentioned": "Computacenter Devices",
        "incumbent_supplier": None,
        "data_residency_constraint": False,
        "esg_requirement": False,
    }

    outcome = _run_with_escalation(pipeline, request)

    # Restricted supplier must not appear in the ranked list
    supplier_results = outcome.get("supplier_results", [])
    ranked_ids = [identity.get("supplier_id") for identity, _, _ in supplier_results]
    assert "SUP-0008" not in ranked_ids, (
        f"Restricted supplier SUP-0008 (Computacenter Devices) must not appear "
        f"in ranked results; got: {ranked_ids}"
    )

    # PREFERRED_SUPPLIER_EXCLUDED flag must fire
    flags = _flag_ids(outcome)
    assert "PREFERRED_SUPPLIER_EXCLUDED" in flags, (
        f"Expected PREFERRED_SUPPLIER_EXCLUDED flag; got flags: {flags}"
    )

    # At least one other (non-restricted) supplier should still be ranked
    assert len(supplier_results) > 0, (
        "Expected at least one non-restricted supplier to be ranked after exclusion"
    )

    print(
        f"\n[test_preferred_supplier_restricted_takes_precedence] "
        f"ranked_ids={ranked_ids}, flags={flags}"
    )


# ---------------------------------------------------------------------------
# Validation error escalation test
# ---------------------------------------------------------------------------


def test_validation_error_returns_escalation():
    """
    An incomplete request (missing required fields) must return status='error'
    AND a structured escalation object pointing to Procurement Manager, so the
    caller always has an actionable escalation path even for invalid input.
    """
    from evaluate_request import evaluate_request
    result = json.loads(evaluate_request("{}"))

    assert result["status"] == "error", "expected status=error for empty request"
    assert result["validation_errors"], "expected at least one validation error"
    assert result["ranked_suppliers"] == [], "expected no ranked suppliers"

    esc = result.get("escalation")
    assert esc is not None, "escalation must not be None for invalid requests"
    assert esc["needs_escalation"] is True, "needs_escalation must be True"
    assert esc["has_blocking"] is True, "must be blocking (request cannot proceed)"
    assert len(esc["records"]) >= 1, "at least one escalation record expected"

    rec = esc["records"][0]
    assert rec["severity"] == "blocking"
    assert rec["person_to_escalate_to"], "must name a person to escalate to"
    assert any("input_validation" in s for s in rec["sources"]), (
        "source must indicate input_validation"
    )

    print(
        f"\n[test_validation_error_returns_escalation] "
        f"validation_errors={[e['field'] for e in result['validation_errors']]}, "
        f"escalation_to={rec['person_to_escalate_to']}"
    )


# ---------------------------------------------------------------------------
# Overdue request escalation test
# ---------------------------------------------------------------------------


def test_overdue_request_triggers_blocking_escalation(pipeline):
    """
    A request whose days_until_required is negative (delivery date in the past)
    must trigger a dedicated blocking escalation record from source
    'overdue_delivery_date', independent of other policy triggers.
    """
    from evaluate_request import evaluate_request
    result = json.loads(
        evaluate_request(
            json.dumps({
                "request_id":         "TEST-OVERDUE-001",
                "category_l1":        "IT",
                "category_l2":        "Laptops",
                "budget":             50000,
                "currency":           "EUR",
                "quantity":           10,
                "amount_unit":        "devices",
                "delivery_country":   "DE",
                "days_until_required": -7,
            })
        )
    )

    assert result["status"] == "ok", f"unexpected error: {result.get('error')}"

    esc = result.get("escalation", {})
    assert esc.get("needs_escalation") is True, "needs_escalation must be True for overdue"
    assert esc.get("has_blocking") is True, "overdue must produce blocking severity"

    all_sources = [s for rec in esc.get("records", []) for s in rec.get("sources", [])]
    assert "overdue_delivery_date" in all_sources, (
        f"expected 'overdue_delivery_date' source in escalation records, got: {all_sources}"
    )

    # The overdue reason must appear in at least one record
    all_reasons = [r for rec in esc.get("records", []) for r in rec.get("reasons", [])]
    assert any("passed" in reason.lower() or "overdue" in reason.lower() for reason in all_reasons), (
        f"expected overdue language in reasons, got: {all_reasons}"
    )

    print(
        f"\n[test_overdue_request_triggers_blocking_escalation] "
        f"records={len(esc.get('records', []))}, sources={all_sources}"
    )


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-s", "-v"])
