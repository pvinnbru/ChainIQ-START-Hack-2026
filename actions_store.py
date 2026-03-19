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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rule_ingestion_prompt import ingest_rule, parse_rule_attribution
from sort_actions import sort_actions

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DATA_DIR: Path = Path("data")
STORE_DIR: Path = Path("stores")
SCHEMA_PATH: Path = Path("start_dict.csv")
POLICIES_PATH: Path = DATA_DIR / "policies.json"

# Policy sections that can be ingested as rulesets.
SUPPORTED_RULESETS: frozenset[str] = frozenset(
    {"approval_thresholds", "category_rules", "escalation_rules"}
)


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


# ---------------------------------------------------------------------------
# Store serialisation
# ---------------------------------------------------------------------------

def _store_path(ruleset_id: str, store_dir: Path = STORE_DIR) -> Path:
    return store_dir / f"{ruleset_id}_actions.json"


def _load_raw_store(ruleset_id: str, store_dir: Path = STORE_DIR) -> dict | None:
    """Load the raw JSON payload; returns None when the file does not exist."""
    path = _store_path(ruleset_id, store_dir)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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
    Run LLM ingestion for every rule in the *ruleset_id* section of policies.json.

    Returns (all_actions, attribution_dict) where attribution_dict maps
    original action index → {rule_id, rule_description}.

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

    all_actions: list[tuple] = []
    all_attribution: dict = {}

    for rule in rules:
        llm_output = ingest_rule(
            tuples=schema_tuples,
            json_data=rule,
            actions_so_far=all_actions,
        )
        offset = len(all_actions)
        new_actions = _parse_actions(llm_output)
        rule_attribution = parse_rule_attribution(llm_output)
        # Offset attribution indices to reflect position in the global list
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
    current_hash = hash_data_folder(data_dir)

    raw = _load_raw_store(ruleset_id, store_dir)
    if raw is not None and raw.get("data_hash") == current_hash:
        raw["sorted_actions"] = [tuple(a) for a in raw["sorted_actions"]]
        # Convert string keys back to int for attribution dict
        raw["attribution"] = {
            int(k): v for k, v in raw.get("attribution", {}).items()
        }
        raw["cache_hit"] = True
        return raw

    # Rebuild
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
