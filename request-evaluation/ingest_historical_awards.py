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

# ---------------------------------------------------------------------------
# Historic-score formula constants
# ---------------------------------------------------------------------------

# Weights for the three signals that compose the raw historic score.
# Must sum to 1.0.
_W_AWARD_RATE   = 0.65   # fraction of evaluations that ended in an award
_W_RANK_SCORE   = 0.20   # 1/avg_rank: how consistently does the supplier rank first?
_W_SAVINGS      = 0.15   # normalised savings delivered (capped at 10%)

# Savings cap: savings_pct values above this are treated as full score.
_SAVINGS_CAP_PCT = 10.0

# Reliability half-life: n appearances that yield reliability ≈ 0.63.
# reliability = 1 - exp(-n / _RELIABILITY_HALFLIFE)
#   n=1  → 0.18   (mostly prior)
#   n=5  → 0.63
#   n=10 → 0.86
#   n=20 → 0.98
_RELIABILITY_HALFLIFE = 5.0

# Neutral prior: used when reliability < 1.  A supplier with no history
# gets exactly this score, which is neither a boost nor a penalty.
_NEUTRAL_PRIOR = 0.5

_HERE         = Path(__file__).parent.parent  # project root
AWARDS_CSV    = _HERE / "data/historical_awards.csv"
HISTORICAL_STORE = _HERE / "stores/historical_data.json"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _compute_historic_score(
    n_appearances: int,
    n_awarded: int,
    avg_rank: float,
    avg_savings_pct: float,
) -> float:
    """
    Compute a historic_score in [0, 1] for one (supplier, category) pair.

    Formula
    -------
    raw_score   = W_AWARD_RATE × award_rate
                + W_RANK_SCORE × (1 / avg_rank)
                + W_SAVINGS    × min(1, avg_savings_pct / SAVINGS_CAP_PCT)

    reliability = 1 - exp(-n_appearances / RELIABILITY_HALFLIFE)
                  (Bayesian shrinkage: fewer appearances → closer to neutral prior)

    historic_score = reliability × raw_score + (1 - reliability) × NEUTRAL_PRIOR

    Signals
    -------
    award_rate   — fraction of evaluations that ended in an actual award;
                   the dominant signal (65%).
    1/avg_rank   — how consistently the supplier ranked first across all
                   evaluated requests (20%); rank=1 → 1.0, rank=3 → 0.33.
    savings_pct  — average savings delivered at award, normalised to [0,1]
                   by capping at SAVINGS_CAP_PCT (15%).
    """
    if n_appearances == 0:
        return _NEUTRAL_PRIOR

    award_rate    = n_awarded / n_appearances
    rank_score    = 1.0 / max(avg_rank, 1.0)   # guard against 0 avg_rank
    savings_score = min(1.0, avg_savings_pct / _SAVINGS_CAP_PCT)

    raw_score = (
        _W_AWARD_RATE * award_rate
        + _W_RANK_SCORE * rank_score
        + _W_SAVINGS    * savings_score
    )

    reliability    = 1.0 - math.exp(-n_appearances / _RELIABILITY_HALFLIFE)
    historic_score = reliability * raw_score + (1.0 - reliability) * _NEUTRAL_PRIOR

    return round(historic_score, 6)


def build_historical_store(
    csv_path: Path = AWARDS_CSV,
    store_path: Path = HISTORICAL_STORE,
) -> dict[str, Any]:
    """
    Read historical_awards.csv, compute per-category pricing stats AND
    per-(supplier, category) historic scores, and persist to
    stores/historical_data.json.

    Pricing stats (existing):
        Per-(category_l1, category_l2): avg_unit_price, std_dev, data_points.
        All rows used (awarded and not-awarded) for a broad market average.

    Historic scores (new):
        Per-(supplier_id, category_l1, category_l2): historic_score, plus the
        underlying signals (award_rate, avg_rank, avg_savings_pct, n_appearances).
        Used by supplier_matrix.py to replace the hardcoded 1.0 placeholder.

    Returns the store dict.
    """
    # --- Pricing buckets: {l1: {l2: [unit_price, ...]}} ---
    price_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    # --- Supplier signal buckets: {l1: {l2: {supplier_id: {signal: [...]} }}} ---
    # Each inner dict accumulates per-row values to be averaged later.
    SupplierBucket = dict  # type alias for readability
    sup_buckets: dict[str, dict[str, dict[str, SupplierBucket]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
            "n_appearances": 0,
            "n_awarded":     0,
            "ranks":         [],
            "savings":       [],
        })))
    )

    skipped = 0
    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            l1          = row.get("category_l1", "").strip()
            l2          = row.get("category_l2", "").strip()
            supplier_id = row.get("supplier_id", "").strip()

            if not l1 or not l2:
                skipped += 1
                continue

            # --- Pricing ---
            try:
                total_value = float(row["total_value"])
                quantity    = float(row["quantity"])
                if quantity > 0:
                    price_buckets[l1][l2].append(total_value / quantity)
            except (ValueError, KeyError, TypeError):
                pass  # pricing data absent — still process supplier signals below

            # --- Supplier historic signals ---
            if not supplier_id:
                continue

            bucket = sup_buckets[l1][l2][supplier_id]
            bucket["n_appearances"] += 1

            awarded = row.get("awarded", "").strip().lower() == "true"
            if awarded:
                bucket["n_awarded"] += 1

            try:
                rank = int(row["award_rank"])
                if rank > 0:
                    bucket["ranks"].append(rank)
            except (ValueError, KeyError, TypeError):
                pass

            try:
                sav = float(row["savings_pct"])
                bucket["savings"].append(sav)
            except (ValueError, KeyError, TypeError):
                pass

    # --- Build pricing stats ---
    categories: dict[str, dict[str, dict]] = {}
    for l1, l2_map in sorted(price_buckets.items()):
        categories[l1] = {}
        for l2, prices in sorted(l2_map.items()):
            n   = len(prices)
            avg = sum(prices) / n
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

    # --- Build supplier historic scores ---
    supplier_scores: dict[str, dict[str, dict[str, dict]]] = {}
    for l1, l2_map in sorted(sup_buckets.items()):
        supplier_scores[l1] = {}
        for l2, sup_map in sorted(l2_map.items()):
            supplier_scores[l1][l2] = {}
            for sup_id, b in sorted(sup_map.items()):
                n_app    = b["n_appearances"]
                n_aw     = b["n_awarded"]
                avg_rank = sum(b["ranks"]) / len(b["ranks"]) if b["ranks"] else 1.0
                avg_sav  = sum(b["savings"]) / len(b["savings"]) if b["savings"] else 0.0

                score = _compute_historic_score(n_app, n_aw, avg_rank, avg_sav)
                supplier_scores[l1][l2][sup_id] = {
                    "historic_score":    score,
                    "n_appearances":     n_app,
                    "n_awarded":         n_aw,
                    "award_rate":        round(n_aw / n_app, 6) if n_app else 0.0,
                    "avg_rank":          round(avg_rank, 4),
                    "avg_savings_pct":   round(avg_sav, 4),
                    "reliability":       round(
                        1.0 - math.exp(-n_app / _RELIABILITY_HALFLIFE), 6
                    ),
                }

    total_points = sum(
        v["data_points"]
        for l2_map in categories.values()
        for v in l2_map.values()
    )

    store: dict[str, Any] = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "source":            str(csv_path),
        "total_data_points": total_points,
        "categories":        categories,
        "supplier_scores":   supplier_scores,
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


def get_supplier_historic_score(
    supplier_id: str,
    category_l1: str,
    category_l2: str,
    store: dict[str, Any] | None = None,
) -> float:
    """
    Look up the pre-computed historic_score for one (supplier, category) pair.

    Returns _NEUTRAL_PRIOR (0.5) when the supplier has no historical record in
    this category — neither a boost nor a penalty for unknown track records.

    The score is in [0, 1]:
      → 1.0  supplier consistently wins in this category with high savings
      → 0.5  no historical data (neutral prior)
      → 0.0  supplier consistently evaluated but never selected
    """
    if store is None:
        store = load_historical_store()
    entry = (
        store.get("supplier_scores", {})
             .get(category_l1, {})
             .get(category_l2, {})
             .get(supplier_id)
    )
    if entry is None:
        return _NEUTRAL_PRIOR
    return float(entry["historic_score"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Reading {AWARDS_CSV} ...")
    store = build_historical_store()
    n_cats = sum(len(v) for v in store["categories"].values())
    n_sup_entries = sum(
        len(sup_map)
        for l2_map in store["supplier_scores"].values()
        for sup_map in l2_map.values()
    )
    print(
        f"Done — {store['total_data_points']} data points across "
        f"{n_cats} categories, {n_sup_entries} supplier×category historic scores "
        f"written to {HISTORICAL_STORE}\n"
    )
    print("── Pricing stats ──")
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
    print("\n── Historic scores (sample: top 5 per category by score) ──")
    for l1, l2_map in store["supplier_scores"].items():
        for l2, sup_map in l2_map.items():
            ranked = sorted(sup_map.items(), key=lambda x: -x[1]["historic_score"])[:5]
            print(f"  {l1} / {l2}")
            for sup_id, s in ranked:
                print(
                    f"    {sup_id}  score={s['historic_score']:.3f}"
                    f"  award_rate={s['award_rate']:.2f}"
                    f"  avg_rank={s['avg_rank']:.2f}"
                    f"  n={s['n_appearances']}"
                    f"  reliability={s['reliability']:.2f}"
                )
