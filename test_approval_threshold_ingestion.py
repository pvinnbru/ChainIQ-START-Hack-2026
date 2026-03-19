"""
Integration test: ingest all approval_threshold rules from policies.json using
the start_dict.csv schema, accumulate actions across rules, then return them
as a topologically sorted list via sort_actions.

Run with:  python -m pytest test_approval_threshold_ingestion.py -s
       or:  python test_approval_threshold_ingestion.py
"""

import csv
import json
import re
from pathlib import Path

from rule_ingestion_prompt import ingest_rule
from sort_actions import sort_actions

CSV_PATH = Path("start_dict.csv")
POLICIES_PATH = Path("data/policies.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_dict_tuples() -> tuple[list[tuple], set[str]]:
    """
    Read start_dict.csv and return:
      - tuples: list of (name, description, type) for every non-meta entry,
                with "[supplier_matrix]" appended to the description for
                supplier-matrix fields so the LLM knows their scope.
      - fix_in_keys: set of all fix_in key names (request-level and
                     supplier_matrix) — both are externally provided and must
                     not be used as dependency-edge targets in sort_actions.
    """
    tuples: list[tuple] = []
    fix_in_keys: set[str] = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
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


def parse_actions(llm_output: str) -> list[tuple]:
    """
    Extract action tuples from the ACTIONS: { ... } section of the LLM response.

    Expects each action on its own line in the form:
        (TYPE, in_param1, in_param2, operator, out_param [, WHEN condition])

    The WHEN clause (index 5) may contain spaces but not commas, so splitting
    on ', ' is safe for the first five positional fields.
    """
    # Slice from ACTIONS: to DICT: (or end of string) to avoid false matches
    actions_start = llm_output.find("ACTIONS:")
    if actions_start == -1:
        return []
    dict_start = llm_output.find("DICT:", actions_start)
    section = llm_output[actions_start : dict_start if dict_start != -1 else None]

    valid_types = {"AL", "ALI", "OSLM", "SRM"}
    actions: list[tuple] = []

    for m in re.finditer(r"\(([^()]+)\)", section):
        content = m.group(1)
        parts = [p.strip() for p in content.split(",")]
        if len(parts) < 5:
            continue
        if parts[0] not in valid_types:
            continue
        # Rejoin anything past index 5 as the WHEN condition
        if len(parts) > 5:
            action: tuple = tuple(parts[:5]) + (", ".join(parts[5:]),)
        else:
            action = tuple(parts)
        actions.append(action)

    return actions


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_ingest_approval_thresholds() -> list[tuple]:
    """
    For each rule in approval_thresholds:
      1. Call ingest_rule with the full dict schema and all previously
         ingested actions as context.
      2. Parse the returned action tuples.
      3. After all rules are processed, sort the collected actions
         topologically and assert there are no dependency cycles.
    """
    tuples, fix_in_keys = load_dict_tuples()
    print(f"\nLoaded {len(tuples)} dict entries, {len(fix_in_keys)} fix_in keys")

    with open(POLICIES_PATH, encoding="utf-8") as f:
        policies = json.load(f)
    thresholds = policies["approval_thresholds"]
    print(f"Found {len(thresholds)} approval threshold rules to ingest\n")

    all_actions: list[tuple] = []

    for threshold in thresholds:
        tid = threshold.get("threshold_id", "?")
        print(f"{'=' * 60}")
        print(f"Ingesting {tid}  (currency={threshold.get('currency', '?')})")
        print(f"{'=' * 60}")

        result = ingest_rule(
            tuples=tuples,
            json_data=threshold,
            actions_so_far=all_actions,
        )
        print(result)

        new_actions = parse_actions(result)
        print(f"\n  → parsed {len(new_actions)} new action(s) from {tid}")
        all_actions.extend(new_actions)

    print(f"\n{'=' * 60}")
    print(f"Total raw actions collected: {len(all_actions)}")
    print(f"{'=' * 60}\n")

    sorted_acts, is_low_confidence = sort_actions(all_actions, fix_in_keys)

    print(f"Sorted actions ({len(sorted_acts)} total, low_confidence={is_low_confidence}):\n")
    for i, action in enumerate(sorted_acts, 1):
        print(f"  {i:2d}. {action}")

    assert len(sorted_acts) == len(all_actions), (
        f"Expected {len(all_actions)} actions after sorting, got {len(sorted_acts)}"
    )
    assert not is_low_confidence, (
        "Dependency cycle detected in approval threshold actions — check WHEN conditions"
    )


if __name__ == "__main__":
    test_ingest_approval_thresholds()
