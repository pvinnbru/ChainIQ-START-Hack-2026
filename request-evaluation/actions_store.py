"""
actions_store.py — Hash-invalidated persistence for the sorted actions pipeline.

The actions store persists the topologically-sorted action list for a given
policy ruleset so that subsequent requests skip the expensive LLM ingestion
step.  A SHA-256 hash of every file in the data/ folder is saved alongside
the actions; if any file changes the store is automatically rebuilt on the
next access.

Usage:
    from actions_store import get_or_build_actions_store

    result = get_or_build_actions_store("approval_thresholds")
    # result["sorted_actions"]   → list[tuple]
    # result["cache_hit"]        → bool
    # result["is_low_confidence"] → bool
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rule_ingestion_prompt import (
    MAX_CONCURRENT_LLM_CALLS,
    ingest_rule,
    ingest_escalation_rules,
    parse_rule_attribution,
)
from sort_actions import sort_actions

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_HERE: Path = Path(__file__).parent.parent  # project root
_MODULE_DIR: Path = Path(__file__).parent   # request-evaluation/ (schema lives here)
DATA_DIR: Path = _HERE / "data"
STORE_DIR: Path = _HERE / "stores"
SCHEMA_PATH: Path = _MODULE_DIR / "start_dict.csv"
POLICIES_PATH: Path = DATA_DIR / "policies.json"

# Policy sections that produce executable action tuples.
SUPPORTED_RULESETS: frozenset[str] = frozenset(
    {"approval_thresholds", "category_rules"}
)

# Policy sections that are stored as natural-language escalation conditions.
ESCALATION_RULESETS: frozenset[str] = frozenset({"escalation_rules"})

# ---------------------------------------------------------------------------
# Per-ruleset rebuild locks
# Prevents two concurrent callers from both finding a stale cache and
# triggering duplicate (expensive) LLM rebuilds for the same ruleset.
# Different rulesets get different locks so they never block each other.
# ---------------------------------------------------------------------------
_rebuild_locks: dict[str, threading.Lock] = {
    ruleset: threading.Lock()
    for ruleset in SUPPORTED_RULESETS | ESCALATION_RULESETS
}


# ---------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------

def hash_data_folder(data_dir: Path = DATA_DIR) -> str:
    """
    Return a SHA-256 hex digest over all files in *data_dir*.

    Files are processed in sorted order by name so the hash is stable
    across runs.  Both the file name and its contents contribute to the
    digest, so renames as well as content edits invalidate the store.
    """
    h = hashlib.sha256()
    data_path = Path(data_dir)
    for file_path in sorted(data_path.iterdir()):
        if file_path.is_file():
            h.update(file_path.name.encode("utf-8"))
            h.update(file_path.read_bytes())
    return h.hexdigest()


def hash_data_and_schema(data_dir: Path = DATA_DIR, schema_path: Path = SCHEMA_PATH) -> str:
    """
    ISSUE-003: return a SHA-256 digest that covers both *data_dir* and the
    schema file (start_dict.csv).

    The action store caches are keyed against this combined hash so that:
      - Adding a new fix_in field to start_dict.csv invalidates the store.
      - Renaming a field in start_dict.csv invalidates the store.
      - Changing a type annotation in start_dict.csv invalidates the store.

    This function is used by get_or_build_actions_store but NOT by the separate
    ranking-actions cache in test_example_request.py, which has its own hash
    strategy and its own regeneration path.
    """
    base = hash_data_folder(data_dir)
    if not schema_path.is_file():
        return base
    h = hashlib.sha256()
    h.update(base.encode("utf-8"))
    h.update(schema_path.name.encode("utf-8"))
    h.update(schema_path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Store serialisation
# ---------------------------------------------------------------------------

def _store_path(ruleset_id: str, store_dir: Path = STORE_DIR) -> Path:
    return store_dir / f"{ruleset_id}_actions.json"


def _load_raw_store(ruleset_id: str, store_dir: Path = STORE_DIR) -> dict | None:
    """Load the raw JSON payload; returns None when the file does not exist or is corrupted."""
    path = _store_path(ruleset_id, store_dir)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            # ISSUE-020: treat a corrupted store file as a cache miss so the
            # store is rebuilt cleanly rather than crashing the pipeline.
            return None


def _save_store(
    ruleset_id: str,
    sorted_actions: list[tuple],
    data_hash: str,
    is_low_confidence: bool,
    store_dir: Path = STORE_DIR,
    attribution: dict | None = None,
) -> None:
    """Serialise *sorted_actions*, attribution, and metadata to the store file."""
    store_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ruleset_id": ruleset_id,
        "data_hash": data_hash,
        "is_low_confidence": is_low_confidence,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sorted_actions": [list(a) for a in sorted_actions],
        "attribution": {str(k): v for k, v in (attribution or {}).items()},
    }
    with open(_store_path(ruleset_id, store_dir), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


# ---------------------------------------------------------------------------
# Schema loader (mirrors test_approval_threshold_ingestion.load_dict_tuples)
# ---------------------------------------------------------------------------

def _load_schema_tuples(schema_path: Path = SCHEMA_PATH) -> tuple[list[tuple], set[str]]:
    """
    Read *schema_path* (start_dict.csv) and return:
      - tuples: list of (name, description, type) for every non-meta entry
      - fix_in_keys: set of all fix_in key names
    """
    tuples: list[tuple] = []
    fix_in_keys: set[str] = set()
    with open(schema_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = row["names"].strip()
            typ = row["type"].strip()
            desc = row["description"].strip()
            relevance = row.get("relevance", "").strip()
            if typ == "meta":
                continue
            if relevance == "supplier_matrix":
                desc = f"{desc} [supplier_matrix]"
            tuples.append((name, desc, typ))
            if typ == "fix_in":
                fix_in_keys.add(name)
    return tuples, fix_in_keys


# ---------------------------------------------------------------------------
# Action parser (equivalent to test_approval_threshold_ingestion.parse_actions)
# ---------------------------------------------------------------------------

def _parse_actions(llm_output: str) -> list[tuple]:
    """Extract action tuples from the ACTIONS: { ... } section of an LLM response."""
    actions_start = llm_output.find("ACTIONS:")
    if actions_start == -1:
        return []
    dict_start = llm_output.find("DICT:", actions_start)
    section = llm_output[actions_start: dict_start if dict_start != -1 else None]

    valid_types = {"AL", "ALI", "OSLM", "SRM"}
    actions: list[tuple] = []
    for m in re.finditer(r"\(([^()]+)\)", section):
        content = m.group(1)
        parts = [p.strip() for p in content.split(",")]
        if len(parts) < 5 or parts[0] not in valid_types:
            continue
        action: tuple = (
            tuple(parts[:5]) + (", ".join(parts[5:]),)
            if len(parts) > 5
            else tuple(parts)
        )
        actions.append(action)
    return actions


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------

def _build_rules_actions(
    ruleset_id: str,
    schema_tuples: list[tuple],
    fix_in_keys: set[str],
    policies_path: Path = POLICIES_PATH,
) -> tuple[list[tuple], dict]:
    """
    Run LLM ingestion for every rule in the *ruleset_id* section of policies.json,
    issuing up to MAX_CONCURRENT_LLM_CALLS requests in parallel.

    Each rule is ingested independently (actions_so_far is not passed). This is
    safe because AT rules cover non-overlapping budget bands and CR rules cover
    non-overlapping categories, so there is no meaningful inter-rule dependency
    that the actions_so_far context would resolve.

    Results are merged in original rule order after all futures complete.

    Returns (all_actions, attribution_dict).
    Raises ValueError if the section is absent or empty.
    """
    with open(policies_path, encoding="utf-8") as fh:
        policies = json.load(fh)

    rules: list[dict[str, Any]] = policies.get(ruleset_id, [])
    if not rules:
        raise ValueError(
            f"No rules found for ruleset '{ruleset_id}' in {policies_path}. "
            f"Available sections: {list(policies.keys())}"
        )

    def _ingest_one(rule: dict) -> tuple[list[tuple], dict]:
        llm_output = ingest_rule(tuples=schema_tuples, json_data=rule, actions_so_far=None)
        return _parse_actions(llm_output), parse_rule_attribution(llm_output)

    # Submit all rules in parallel; collect results keyed by original position
    results_by_idx: dict[int, tuple[list[tuple], dict]] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM_CALLS) as executor:
        future_to_idx = {
            executor.submit(_ingest_one, rule): idx
            for idx, rule in enumerate(rules)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results_by_idx[idx] = future.result()
            except Exception as exc:
                rule_id = rules[idx].get("rule_id", rules[idx].get("threshold_id", f"index-{idx}"))
                print(f"[WARNING] LLM ingestion failed for rule {rule_id}: {exc}")
                results_by_idx[idx] = ([], {})

    # Merge in original order so topological sort sees a stable sequence
    all_actions: list[tuple] = []
    all_attribution: dict = {}
    for idx in sorted(results_by_idx):
        new_actions, rule_attribution = results_by_idx[idx]
        offset = len(all_actions)
        for k, v in rule_attribution.items():
            all_attribution[k + offset] = v
        all_actions.extend(new_actions)

    return all_actions, all_attribution


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_build_actions_store(
    ruleset_id: str,
    data_dir: Path = DATA_DIR,
    store_dir: Path = STORE_DIR,
    schema_path: Path = SCHEMA_PATH,
    policies_path: Path = POLICIES_PATH,
) -> dict[str, Any]:
    """
    Return the actions store for *ruleset_id*, rebuilding it when stale or absent.

    The store is considered stale when the SHA-256 hash of all files in
    *data_dir* differs from the hash recorded in the stored payload.

    Returns a dict with keys:
        ruleset_id       (str)
        data_hash        (str)   — SHA-256 of data_dir at store creation time
        is_low_confidence (bool) — True if dependency cycles were detected
        created_at       (str)   — ISO-8601 timestamp
        sorted_actions   (list[tuple])
        cache_hit        (bool)  — True when the store was loaded from disk
    """
    # ISSUE-003: include start_dict.csv in the hash so schema changes (new
    # fix_in fields, renamed fields, type annotations) invalidate the store.
    current_hash = hash_data_and_schema(data_dir)

    # Fast path: cache is valid — no lock needed (read-only)
    raw = _load_raw_store(ruleset_id, store_dir)
    if raw is not None and raw.get("data_hash") == current_hash:
        raw["sorted_actions"] = [tuple(a) for a in raw["sorted_actions"]]
        raw["attribution"] = {
            int(k): v for k, v in raw.get("attribution", {}).items()
        }
        raw["cache_hit"] = True
        return raw

    # Slow path: rebuild under a per-ruleset lock so that concurrent callers
    # (e.g. from build_all_stores_parallel) don't both issue expensive LLM
    # calls for the same ruleset.
    with _rebuild_locks[ruleset_id]:
        # Re-check inside the lock in case another thread just finished building
        raw = _load_raw_store(ruleset_id, store_dir)
        if raw is not None and raw.get("data_hash") == current_hash:
            raw["sorted_actions"] = [tuple(a) for a in raw["sorted_actions"]]
            raw["attribution"] = {
                int(k): v for k, v in raw.get("attribution", {}).items()
            }
            raw["cache_hit"] = True
            return raw

        schema_tuples, fix_in_keys = _load_schema_tuples(schema_path)
        raw_actions, raw_attribution = _build_rules_actions(
            ruleset_id, schema_tuples, fix_in_keys, policies_path
        )
        sorted_acts, is_low_confidence, attribution = sort_actions(
            raw_actions, fix_in_keys, attribution=raw_attribution
        )

        _save_store(
            ruleset_id=ruleset_id,
            sorted_actions=sorted_acts,
            data_hash=current_hash,
            is_low_confidence=is_low_confidence,
            store_dir=store_dir,
            attribution=attribution,
        )

    return {
        "ruleset_id": ruleset_id,
        "data_hash": current_hash,
        "is_low_confidence": is_low_confidence,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sorted_actions": sorted_acts,
        "attribution": attribution,
        "cache_hit": False,
    }


# ---------------------------------------------------------------------------
# Escalation rule store
# ---------------------------------------------------------------------------

def _escalation_store_path(store_dir: Path = STORE_DIR) -> Path:
    return store_dir / "escalation_rules_store.json"


def _load_raw_escalation_store(store_dir: Path = STORE_DIR) -> dict | None:
    path = _escalation_store_path(store_dir)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_escalation_store(
    rules: list[dict],
    data_hash: str,
    store_dir: Path = STORE_DIR,
) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ruleset_id": "escalation_rules",
        "data_hash": data_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rules": rules,
    }
    with open(_escalation_store_path(store_dir), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def get_or_build_escalation_store(
    data_dir: Path = DATA_DIR,
    store_dir: Path = STORE_DIR,
    policies_path: Path = POLICIES_PATH,
) -> dict[str, Any]:
    """
    Return the escalation rules store, rebuilding it when stale or absent.

    Each entry in ``rules`` has keys:
        rule_id          (str)
        trigger_condition (str)  — natural language description of the trigger
        escalate_to      (str)  — target role or team
        applies_when     (str)  — scope constraint or "always"

    Returns a dict with keys: ruleset_id, data_hash, created_at, rules, cache_hit.
    """
    current_hash = hash_data_folder(data_dir)

    # Fast path — no lock needed
    raw = _load_raw_escalation_store(store_dir)
    if raw is not None and raw.get("data_hash") == current_hash:
        raw["cache_hit"] = True
        return raw

    with _rebuild_locks["escalation_rules"]:
        # Re-check inside lock
        raw = _load_raw_escalation_store(store_dir)
        if raw is not None and raw.get("data_hash") == current_hash:
            raw["cache_hit"] = True
            return raw

        with open(policies_path, encoding="utf-8") as fh:
            policies = json.load(fh)

        raw_rules: list[dict] = policies.get("escalation_rules", [])
        if not raw_rules:
            raise ValueError(
                f"No escalation_rules section found in {policies_path}."
            )

        structured_rules = ingest_escalation_rules(raw_rules)

        _save_escalation_store(
            rules=structured_rules,
            data_hash=current_hash,
            store_dir=store_dir,
        )

    return {
        "ruleset_id": "escalation_rules",
        "data_hash": current_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rules": structured_rules,
        "cache_hit": False,
    }


# ---------------------------------------------------------------------------
# Cross-section parallel bootstrap
# ---------------------------------------------------------------------------

def build_all_stores_parallel(
    data_dir: Path = DATA_DIR,
    store_dir: Path = STORE_DIR,
    schema_path: Path = SCHEMA_PATH,
    policies_path: Path = POLICIES_PATH,
) -> dict[str, dict[str, Any]]:
    """
    Build (or load from cache) all action stores and the escalation rule store
    concurrently.

    The three sections — approval_thresholds, category_rules, escalation_rules —
    are fully independent and write to separate store files, so they can be built
    in parallel without any coordination beyond the per-ruleset rebuild locks
    (which prevent duplicate work when the cache is stale).

    Returns a dict keyed by ruleset_id containing each store's result payload.
    """
    tasks: dict[str, Any] = {
        ruleset: (get_or_build_actions_store, (ruleset, data_dir, store_dir, schema_path, policies_path))
        for ruleset in SUPPORTED_RULESETS
    }
    tasks["escalation_rules"] = (
        get_or_build_escalation_store,
        (data_dir, store_dir, policies_path),
    )

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_to_id = {
            executor.submit(fn, *args): ruleset_id
            for ruleset_id, (fn, args) in tasks.items()
        }
        for future in as_completed(future_to_id):
            ruleset_id = future_to_id[future]
            try:
                results[ruleset_id] = future.result()
            except Exception as exc:
                print(f"[ERROR] Failed to build store for {ruleset_id}: {exc}")
                raise

    return results
