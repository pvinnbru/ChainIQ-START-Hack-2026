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
    save_generated_actions,
    load_generated_actions,
    save_log,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
STORE_DIR = Path("stores")
LOGS_DIR = STORE_DIR / "execution_logs"
SCHEMA_PATH = Path("start_dict.csv")
REQUESTS_PATH = DATA_DIR / "requests.json"
SUPPLIERS_PATH = DATA_DIR / "suppliers.csv"
PRICING_PATH = DATA_DIR / "pricing.csv"
RANKING_STORE_PATH = STORE_DIR / "ranking_actions.json"
RESULTS_PATH = STORE_DIR / "system_test_results.json"


# ---------------------------------------------------------------------------
# Request normalisation
# ---------------------------------------------------------------------------

def _days_until(date_str: str | None) -> int:
    """Convert an ISO-8601 date string to calendar days until that date from today."""
    if not date_str:
        return 0
    try:
        d = date.fromisoformat(date_str[:10])
        return max(0, (d - date.today()).days)
    except ValueError:
        return 0


def normalize_request(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Map a raw entry from requests.json to the field names expected by the schema
    fix_in keys:
        category_l1, category_l2, budget, currency, quantity, amount_unit,
        delivery_country, days_until_required, preferred_supplier_mentioned,
        incumbent_supplier, data_residency_constraint, esg_requirement

    Returns None if any mandatory field is absent.
    """
    # delivery_country: first element of delivery_countries list
    delivery_countries: list[str] = raw.get("delivery_countries") or []
    delivery_country = delivery_countries[0] if delivery_countries else None
    if not delivery_country:
        return None

    # Mandatory categorical fields
    category_l1 = raw.get("category_l1")
    category_l2 = raw.get("category_l2")
    if not category_l1 or not category_l2:
        return None

    # Budget / currency
    budget = raw.get("budget_amount")
    currency = raw.get("currency")
    if budget is None or not currency:
        return None

    # Quantity — null means the request didn't specify; default to 1 so pricing
    # tier lookup can still find a match (quantity=1 is within every tier).
    quantity = raw.get("quantity")
    if quantity is None:
        quantity = 1

    return {
        # Passthrough meta (not in schema, used for result reporting only)
        "request_id": raw.get("request_id"),
        # Schema fix_in — request-level
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
    }


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
    with open(REQUESTS_PATH, encoding="utf-8") as fh:
        raw_requests: list[dict] = json.load(fh)

    schema        = pipeline["schema"]
    fix_in_keys   = pipeline["fix_in_keys"]
    sorted_actions = pipeline["sorted_actions"]
    suppliers     = pipeline["suppliers"]
    pricing_index = pipeline["pricing_index"]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    skipped      = 0
    evaluated    = 0
    with_suppliers = 0
    errors       = 0

    for raw in raw_requests:
        request = normalize_request(raw)

        if request is None:
            skipped += 1
            results.append({
                "request_id": raw.get("request_id"),
                "status": "skipped",
                "reason": "missing_mandatory_fields",
            })
            continue

        evaluated += 1
        try:
            outcome, exec_log = run_procurement_evaluation(
                request=request,
                schema=schema,
                sorted_actions=sorted_actions,
                suppliers=suppliers,
                fix_in_keys=fix_in_keys,
                pricing_index=pricing_index,
                attribution=pipeline.get("attribution"),
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            results.append({
                "request_id": request["request_id"],
                "status": "error",
                "error": str(exc),
            })
            continue

        # Persist execution log for this request
        log_path = str(LOGS_DIR / request["request_id"])
        save_log(exec_log, log_path)

        supplier_results = outcome["supplier_results"]
        if supplier_results:
            with_suppliers += 1

        ranking = [
            {
                "position":         pos + 1,
                "supplier_id":      identity.get("supplier_id"),
                "supplier_name":    identity.get("supplier_name"),
                "rank_score":       _safe_round(final_state.get("rank")),
                "cost_rank_score":  _safe_round(final_state.get("cost_rank_score")),
                "reputation_score": _safe_round(final_state.get("reputation_score")),
                "cost_total":       _safe_round(final_state.get("cost_total")),
                "unit_price":       _safe_round(final_state.get("unit_price")),
            }
            for pos, (identity, rank, final_state) in enumerate(supplier_results)
        ]

        results.append({
            "request_id":     request["request_id"],
            "status":         "ok",
            "category_l1":    request["category_l1"],
            "category_l2":    request["category_l2"],
            "delivery_country": request["delivery_country"],
            "currency":       request["currency"],
            "quantity":       request["quantity"],
            "global_outputs": outcome["global_outputs"],
            "supplier_count": len(supplier_results),
            "ranking":        ranking,
        })

    # Persist results
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "pipeline": {
            "data_hash":         pipeline["data_hash"][:16] + "...",
            "total_actions":     len(sorted_actions),
            "is_low_confidence": pipeline["is_low_confidence"],
            "cache_hits":        pipeline["cache_hits"],
        },
        "stats": {
            "total_requests":  len(raw_requests),
            "evaluated":       evaluated,
            "skipped":         skipped,
            "with_suppliers":  with_suppliers,
            "errors":          errors,
            "match_rate":      round(with_suppliers / max(evaluated, 1), 4),
        },
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    # Print summary
    print(f"\n[test_all_requests] Summary:")
    print(f"  total_requests:  {len(raw_requests)}")
    print(f"  evaluated:       {evaluated}")
    print(f"  skipped:         {skipped}")
    print(f"  with_suppliers:  {with_suppliers}  "
          f"({with_suppliers / max(evaluated, 1):.0%} of evaluated)")
    print(f"  errors:          {errors}")
    print(f"  results written: {RESULTS_PATH}")
    print(f"  logs written:    {LOGS_DIR}/ ({evaluated - errors} × .json + .txt)")

    # Sample: show first 3 results with suppliers
    shown = 0
    for r in results:
        if r.get("status") == "ok" and r.get("supplier_count", 0) > 0:
            print(f"\n  {r['request_id']} — {r['category_l2']} / {r['delivery_country']}")
            for s in r["ranking"][:3]:
                print(f"    #{s['position']} {s['supplier_name']:40s} "
                      f"rank={s['rank_score']:>10}  cost={s['cost_total']}")
            shown += 1
            if shown >= 3:
                break

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


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-s", "-v"])
