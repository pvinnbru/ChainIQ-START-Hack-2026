"""
test_example_request.py — Integration test that runs only examples/example_request.json
through the complete ChainIQ procurement pipeline, including execution log persistence.

Run with:
    python -m pytest test_example_request.py -s -v
or:
    python test_example_request.py
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
SUPPLIERS_PATH = DATA_DIR / "suppliers.csv"
PRICING_PATH = DATA_DIR / "pricing.csv"
RANKING_STORE_PATH = STORE_DIR / "ranking_actions.json"
EXAMPLE_REQUEST_PATH =   "examples/example_request.json"


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_system_full.py)
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
        # Passed through for text compliance — not a schema fix_in, never touches the action pipeline
        "request_text": raw.get("request_text") or "",
    }


def _safe_round(value: Any, ndigits: int = 4) -> Any:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return value


def _strip_action_quotes(actions: list[tuple]) -> list[tuple]:
    def _unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s
    return [tuple(_unquote(str(field)) for field in action) for action in actions]


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
    raw = _load_raw_ranking_store()
    if raw is not None and raw.get("data_hash") == data_hash:
        actions = _strip_action_quotes([tuple(a) for a in raw["ranking_actions"]])
        attribution = {int(k): v for k, v in raw.get("attribution", {}).items()}
        return actions, attribution, True

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
# Session-scoped pipeline fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pipeline():
    schema, fix_in_keys = load_schema(str(SCHEMA_PATH))
    schema = add_ranking_schema_entries(schema)

    data_hash = hash_data_folder(DATA_DIR)
    print(f"\n[pipeline] data_hash: {data_hash[:16]}...")

    approval_store   = get_or_build_actions_store("approval_thresholds")
    category_store   = get_or_build_actions_store("category_rules")
    escalation_store = get_or_build_actions_store("escalation_rules")

    rules_actions = (
        list(approval_store["sorted_actions"])
        + list(category_store["sorted_actions"])
        + list(escalation_store["sorted_actions"])
    )

    ranking_actions, ranking_attribution, ranking_cache_hit = get_or_build_ranking_actions(
        schema, data_hash
    )

    rules_attribution: dict = {}
    offset = 0
    for store_key, store in (
        ("approval_thresholds", approval_store),
        ("category_rules", category_store),
        ("escalation_rules", escalation_store),
    ):
        for k, v in store.get("attribution", {}).items():
            rules_attribution[int(k) + offset] = v
        offset += len(store["sorted_actions"])

    sorted_actions, is_low_confidence, combined_attribution = build_full_action_pipeline(
        ranking_actions, rules_actions, fix_in_keys,
        ranking_attribution=ranking_attribution,
        rules_attribution=rules_attribution,
    )

    suppliers = load_suppliers(str(SUPPLIERS_PATH), [])
    pricing_index = load_pricing_index(str(PRICING_PATH))

    print(f"[pipeline] sorted_actions: {len(sorted_actions)}")
    print(f"[pipeline] suppliers: {len(suppliers)}")
    print(f"[pipeline] ranking cache_hit: {ranking_cache_hit}")

    return {
        "schema":            schema,
        "fix_in_keys":       fix_in_keys,
        "sorted_actions":    sorted_actions,
        "is_low_confidence": is_low_confidence,
        "attribution":       combined_attribution,
        "suppliers":         suppliers,
        "pricing_index":     pricing_index,
        "data_hash":         data_hash,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_example_request_exists():
    """examples/example_request.json must exist and be parseable."""
    assert EXAMPLE_REQUEST_PATH.exists(), f"Missing: {EXAMPLE_REQUEST_PATH}"
    with open(EXAMPLE_REQUEST_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data.get("request_id"), "example_request.json has no request_id"
    print(f"\n[test_example_request_exists] request_id={data['request_id']}")


def test_example_request_normalizes():
    """The example request must survive normalization (all mandatory fields present)."""
    with open(EXAMPLE_REQUEST_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)

    normalized = normalize_request(raw)
    assert normalized is not None, "normalize_request() returned None — mandatory field missing"

    # Spot-check key fields
    assert normalized["request_id"] == raw["request_id"]
    assert normalized["category_l1"] == "IT"
    assert normalized["category_l2"] == "Docking Stations"
    assert normalized["budget"] == 25199.55
    assert normalized["currency"] == "EUR"
    assert normalized["quantity"] == 240
    assert normalized["delivery_country"] == "DE"
    assert normalized["preferred_supplier_mentioned"] == "Dell Enterprise Europe"
    assert normalized["incumbent_supplier"] == "Bechtle Workplace Solutions"

    print(f"\n[test_example_request_normalizes] normalized: {normalized}")


def test_example_request_full_pipeline(pipeline):
    """
    Run examples/example_request.json through the complete pipeline.

    Asserts:
    - run_procurement_evaluation() completes without exception.
    - Returns a non-empty outcome dict and a RequestExecutionLog.
    - Execution log is saved to stores/execution_logs/REQ-000004.{json,txt}.
    """
    with open(EXAMPLE_REQUEST_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)

    request = normalize_request(raw)
    assert request is not None, "Example request failed normalization"

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    outcome, exec_log = run_procurement_evaluation(
        request=request,
        schema=pipeline["schema"],
        sorted_actions=pipeline["sorted_actions"],
        suppliers=pipeline["suppliers"],
        fix_in_keys=pipeline["fix_in_keys"],
        pricing_index=pipeline["pricing_index"],
        attribution=pipeline.get("attribution"),
    )

    # Basic structure checks
    assert isinstance(outcome, dict), "outcome must be a dict"
    assert "supplier_results" in outcome, "outcome missing 'supplier_results'"
    assert "global_outputs" in outcome, "outcome missing 'global_outputs'"

    # Log must reference the correct request
    assert exec_log.request_id == request["request_id"]
    assert exec_log.timestamp  # non-empty ISO timestamp
    assert isinstance(exec_log.supplier_logs, list)

    # Persist logs
    log_path = str(LOGS_DIR / request["request_id"])
    save_log(exec_log, log_path)

    json_log = Path(log_path + ".json")
    assert json_log.exists(), f"JSON log not written: {json_log}"

    # Validate JSON log is parseable
    with open(json_log, encoding="utf-8") as fh:
        log_data = json.load(fh)
    assert log_data.get("request_id") == request["request_id"]

    # Print summary
    supplier_results = outcome["supplier_results"]
    print(f"\n[test_example_request_full_pipeline]")
    print(f"  request_id:      {request['request_id']}")
    print(f"  supplier_count:  {len(supplier_results)}")
    print(f"  global_outputs:  {outcome['global_outputs']}")
    print(f"  supplier_logs:   {len(exec_log.supplier_logs)}")
    print(f"  log written:     {json_log}")

    if supplier_results:
        print(f"\n  Ranked suppliers:")
        for pos, (identity, rank, final_state) in enumerate(supplier_results[:5]):
            print(
                f"    #{pos + 1}  {identity.get('supplier_name', '?'):40s}"
                f"  normalized_rank={_safe_round(final_state.get('normalized_rank')):>8}"
                f"  cost_total={_safe_round(final_state.get('cost_total'))}"
            )
    else:
        print("  No suppliers matched (may be expected given contradictory scenario tags)")


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
    Load a JSON list of raw requests from *requests_path* and evaluate as many
    as possible in parallel.

    Each request is normalised with :func:`normalize_request`; requests that
    fail normalisation are skipped and recorded with ``status="skipped"``.

    Results are returned as a list of dicts with keys:
      - ``request_id``
      - ``status``          — ``"ok"`` | ``"skipped"`` | ``"error"``
      - ``skip_reason``     — set when status is ``"skipped"``
      - ``error``           — set when status is ``"error"``
      - ``supplier_count``
      - ``ranking``         — list of top supplier dicts
      - ``global_outputs``

    Parameters
    ----------
    requests_path:
        Path to a JSON file containing a list of raw request dicts (same
        schema as ``data/requests.json``).
    pipeline:
        Pre-built pipeline dict as returned by the ``pipeline`` fixture.
    logs_dir:
        Directory where per-request ``.json`` log files are written.
    max_workers:
        Maximum number of parallel worker threads.
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


if __name__ == "__main__":
    pytest.main([__file__, "-s", "-v"])
