"""
ingest_historical_awards.py — Build (or rebuild) stores/historical_data.json from
data/historical_awards.csv.

Computes per-(category_l1, category_l2) average unit price, sample standard deviation,
and data-point count.  Unit price is derived as  total_value / quantity  for each row.

The standard deviation is used by supplier_matrix.py to compute a z-score for each
supplier's unit price relative to the blended market average.  Categories with tight
price clustering (low std_dev) amplify small differences; categories with wide spread
(high std_dev) dampen them.  This keeps the final rank comparable across categories.

Extensibility
-------------
The CSV is the single source of truth. Add new rows to data/historical_awards.csv
and re-run this script to refresh the store. The store is a flat JSON file that can
also be seeded manually for categories not yet represented in the awards data.

Run:
    python ingest_historical_awards.py
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE         = Path(__file__).parent.parent  # project root
AWARDS_CSV    = _HERE / "data/historical_awards.csv"
HISTORICAL_STORE = _HERE / "stores/historical_data.json"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_historical_store(
    csv_path: Path = AWARDS_CSV,
    store_path: Path = HISTORICAL_STORE,
) -> dict[str, Any]:
    """
    Read historical_awards.csv, compute per-category average unit price, and
    persist the result to stores/historical_data.json.

    Only rows with parseable total_value and quantity > 0 are included.
    All award rows are used (both awarded=True and evaluated-but-not-awarded) to
    get a broad market average.

    Returns the store dict.
    """
    # {category_l1: {category_l2: [unit_price, ...]}}
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    skipped = 0
    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                total_value = float(row["total_value"])
                quantity    = float(row["quantity"])
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue

            if quantity <= 0:
                skipped += 1
                continue

            l1 = row.get("category_l1", "").strip()
            l2 = row.get("category_l2", "").strip()
            if not l1 or not l2:
                skipped += 1
                continue

            buckets[l1][l2].append(total_value / quantity)

    categories: dict[str, dict[str, dict]] = {}
    for l1, l2_map in sorted(buckets.items()):
        categories[l1] = {}
        for l2, prices in sorted(l2_map.items()):
            n   = len(prices)
            avg = sum(prices) / n
            # Sample standard deviation (ddof=1); None when only one data point
            if n >= 2:
                variance = sum((p - avg) ** 2 for p in prices) / (n - 1)
                std_dev  = math.sqrt(variance)
            else:
                std_dev = None
            categories[l1][l2] = {
                "avg_unit_price": round(avg, 6),
                "std_dev":        round(std_dev, 6) if std_dev is not None else None,
                "data_points":    n,
            }

    total_points = sum(
        v["data_points"]
        for l2_map in categories.values()
        for v in l2_map.values()
    )

    store: dict[str, Any] = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "source":          str(csv_path),
        "total_data_points": total_points,
        "categories":      categories,
    }

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2)

    return store


# ---------------------------------------------------------------------------
# Load / query helpers (used by supplier_matrix.py at ranking time)
# ---------------------------------------------------------------------------

def load_historical_store(store_path: Path = HISTORICAL_STORE) -> dict[str, Any]:
    """Return the historical data store. Returns {'categories': {}} if not found."""
    if not store_path.exists():
        return {"categories": {}}
    with open(store_path, encoding="utf-8") as fh:
        return json.load(fh)


def get_historical_stats(
    category_l1: str,
    category_l2: str,
    store: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, int]:
    """
    Look up historical pricing stats for a (category_l1, category_l2) pair.

    Returns (avg_unit_price, std_dev, data_points).
    std_dev is None when fewer than 2 data points exist for the category.
    Returns (None, None, 0) when the category has no historical data at all.
    """
    if store is None:
        store = load_historical_store()
    entry = (
        store.get("categories", {})
             .get(category_l1, {})
             .get(category_l2)
    )
    if entry is None:
        return None, None, 0
    return entry["avg_unit_price"], entry.get("std_dev"), entry["data_points"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Reading {AWARDS_CSV} ...")
    store = build_historical_store()
    n_cats = sum(len(v) for v in store["categories"].values())
    print(
        f"Done — {store['total_data_points']} data points across "
        f"{n_cats} categories written to {HISTORICAL_STORE}\n"
    )
    for l1, l2_map in store["categories"].items():
        for l2, entry in l2_map.items():
            std = entry["std_dev"]
            std_str = f"{std:>10.4f}" if std is not None else "       N/A"
            print(
                f"  {l1:30s} / {l2:45s}"
                f"  avg={entry['avg_unit_price']:>12.4f}"
                f"  std={std_str}"
                f"  n={entry['data_points']}"
            )
