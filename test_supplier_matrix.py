"""
test_supplier_matrix.py — pytest suite for supplier_matrix.py.

Coverage:
  1.  delivery_country filtering
  2.  is_restricted exclusion
  3.  WHEN clause correctly gates OSLM/SRM
  4.  rank ordering in supplier_results output
  5.  missing fix_in key raises
  6.  AL and ALI evaluation correctness
  7.  load_suppliers (primary + join)
  8.  build_global_context (happy path, extra keys stripped)
  9.  _eval_when (unit tests for the condition evaluator)
  10. run_procurement_evaluation (integration)
"""

from __future__ import annotations

import csv
import pathlib
from typing import Any

import pytest

from supplier_matrix import (
    COUNTRY_TO_REGION,
    _eval_when,
    add_ranking_schema_entries,
    build_global_context,
    evaluate_actions,
    filter_suppliers,
    load_generated_actions,
    load_pricing_index,
    load_schema,
    load_suppliers,
    resolve_supplier_pricing,
    run_procurement_evaluation,
    save_generated_actions,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SCHEMA: list[tuple] = [
    ("category_l1", "fix_in", "L1 category", ""),
    ("category_l2", "fix_in", "L2 sub-category", ""),
    ("budget", "fix_in", "Total budget", ""),
    ("currency", "fix_in", "Currency code", ""),
    ("quantity", "fix_in", "Unit count", ""),
    ("amount_unit", "fix_in", "Unit of measure", ""),
    ("delivery_country", "fix_in", "ISO-2 delivery country", ""),
    ("days_until_required", "fix_in", "Days until required", ""),
    ("preferred_supplier_mentioned", "fix_in", "Preferred supplier name", ""),
    ("incumbent_supplier", "fix_in", "Incumbent supplier name", ""),
    ("data_residency_constraint", "fix_in", "Data residency flag", ""),
    ("esg_requirement", "fix_in", "ESG flag", ""),
    ("rank", "fix_out", "Supplier rank score", ""),
    ("min_supplier_quotes", "fix_out", "Minimum quotes required", ""),
    ("fast_track_eligible", "fix_out", "Fast-track eligible flag", ""),
    ("requires_security_review", "fix_out", "Security review flag", ""),
    ("escalate_to_requester", "fix_out", "Escalate to requester flag", ""),
    ("request_id", "meta", "Unique request ID", ""),
]

FIX_IN_KEYS: set[str] = {e[0] for e in SCHEMA if e[1] == "fix_in"}

BASE_REQUEST: dict[str, Any] = {
    "category_l1": "IT",
    "category_l2": "Laptops",
    "budget": 10_000,
    "currency": "EUR",
    "quantity": 5,
    "amount_unit": "devices",
    "delivery_country": "DE",
    "days_until_required": 30,
    "preferred_supplier_mentioned": None,
    "incumbent_supplier": None,
    "data_residency_constraint": False,
    "esg_requirement": False,
}


def _make_supplier(
    *,
    supplier_id: str = "SUP-001",
    supplier_name: str = "ACME Devices",
    category_l1: str = "IT",
    category_l2: str = "Laptops",
    country_hq: str = "DE",
    service_regions: str = "DE;FR;NL",
    is_restricted: bool = False,
    quality_score: int = 80,
    risk_score: int = 20,
    esg_score: int = 75,
    preferred_supplier: bool = False,
    rank: int = 50,
    cost_rank_score: float = 0.0,
    reputation_score: float = 0.0,
) -> dict[str, Any]:
    return {
        "identity": {
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "category_l1": category_l1,
            "category_l2": category_l2,
            "country_hq": country_hq,
            "service_regions": service_regions,
        },
        "attributes": {
            "is_restricted": is_restricted,
            "quality_score": quality_score,
            "risk_score": risk_score,
            "esg_score": esg_score,
            "preferred_supplier": preferred_supplier,
            "rank": rank,
            "cost_rank_score": cost_rank_score,
            "reputation_score": reputation_score,
        },
    }


# ===========================================================================
# 1. delivery_country filtering
# ===========================================================================

class TestDeliveryCountryFiltering:
    def test_supplier_serving_delivery_country_is_included(self) -> None:
        s = _make_supplier(service_regions="DE;FR;NL")
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 1

    def test_supplier_not_serving_delivery_country_is_excluded(self) -> None:
        s = _make_supplier(service_regions="FR;NL;ES")  # no DE
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 0

    def test_serves_delivery_country_flag_added_to_attributes(self) -> None:
        s = _make_supplier(service_regions="DE;FR")
        result = filter_suppliers([s], BASE_REQUEST)
        assert result[0]["attributes"]["serves_delivery_country"] is True

    def test_partial_country_code_does_not_match(self) -> None:
        # "DEX" must not satisfy a lookup for "DE"
        s = _make_supplier(service_regions="DEX;FR")
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 0

    def test_whitespace_around_region_code_is_normalised(self) -> None:
        s = _make_supplier(service_regions=" DE ; FR ")
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 1

    def test_single_region_match(self) -> None:
        s = _make_supplier(service_regions="DE")
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 1

    def test_multiple_suppliers_filtered_correctly(self) -> None:
        inside = _make_supplier(supplier_id="SUP-A", service_regions="DE;FR")
        outside = _make_supplier(supplier_id="SUP-B", service_regions="US;CA")
        result = filter_suppliers([inside, outside], BASE_REQUEST)
        assert len(result) == 1
        assert result[0]["identity"]["supplier_id"] == "SUP-A"


# ===========================================================================
# 2. is_restricted exclusion
# ===========================================================================

class TestIsRestrictedExclusion:
    def test_restricted_supplier_excluded(self) -> None:
        s = _make_supplier(is_restricted=True)
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 0

    def test_unrestricted_supplier_included(self) -> None:
        s = _make_supplier(is_restricted=False)
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 1

    def test_mixed_restricted_and_not(self) -> None:
        ok = _make_supplier(supplier_id="SUP-OK", is_restricted=False)
        blocked = _make_supplier(supplier_id="SUP-BLOCKED", is_restricted=True)
        result = filter_suppliers([ok, blocked], BASE_REQUEST)
        assert len(result) == 1
        assert result[0]["identity"]["supplier_id"] == "SUP-OK"

    def test_category_filter_applied_before_restriction_check(self) -> None:
        # Wrong category — shouldn't appear regardless of restriction status
        s = _make_supplier(category_l2="Monitors", is_restricted=False)
        result = filter_suppliers([s], BASE_REQUEST)
        assert len(result) == 0


# ===========================================================================
# 3. WHEN clause correctly gates OSLM / SRM
# ===========================================================================

class TestWhenClauseGating:
    """WHEN conditions must gate OSLM/SRM; AL/ALI should execute unconditionally."""

    def test_oslm_executes_when_condition_is_true(self) -> None:
        actions = [("OSLM", "rank", "10", "+", "rank", "preferred_supplier = True")]
        attrs = {"rank": 50, "preferred_supplier": True}
        state, _ = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)
        assert state["rank"] == 60

    def test_oslm_skipped_when_condition_is_false(self) -> None:
        actions = [("OSLM", "rank", "10", "+", "rank", "preferred_supplier = True")]
        attrs = {"rank": 50, "preferred_supplier": False}
        state, _ = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)
        assert state["rank"] == 50

    def test_srm_executes_when_condition_is_true(self) -> None:
        actions = [("SRM", "rank", "5", "-", "rank", "risk_score >= 30")]
        attrs = {"rank": 50, "risk_score": 35}
        state, _ = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)
        assert state["rank"] == 45

    def test_srm_skipped_when_condition_is_false(self) -> None:
        actions = [("SRM", "rank", "5", "-", "rank", "risk_score >= 30")]
        attrs = {"rank": 50, "risk_score": 20}
        state, _ = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)
        assert state["rank"] == 50

    def test_al_respects_when_clause(self) -> None:
        # AL respects WHEN — skipped when condition is False, runs when True
        actions = [("AL", "quality_score", "esg_score", "+", "rank", "preferred_supplier = True")]
        attrs_false = {"quality_score": 80, "esg_score": 70, "preferred_supplier": False}
        attrs_true  = {"quality_score": 80, "esg_score": 70, "preferred_supplier": True}
        state_false, _ = evaluate_actions(actions, BASE_REQUEST, attrs_false, FIX_IN_KEYS)
        state_true,  _ = evaluate_actions(actions, BASE_REQUEST, attrs_true,  FIX_IN_KEYS)
        # WHEN False → rank not written (action skipped)
        assert "rank" not in state_false
        # WHEN True → rank = 80 + 70 = 150
        assert state_true["rank"] == 150

    def test_when_with_and_condition(self) -> None:
        # Both sub-conditions must be True
        actions = [("SRM", "rank", "20", "+", "rank", "preferred_supplier = True AND risk_score <= 25")]
        attrs_both_true = {"rank": 50, "preferred_supplier": True, "risk_score": 20}
        attrs_one_false = {"rank": 50, "preferred_supplier": True, "risk_score": 30}

        s1, _ = evaluate_actions(actions, BASE_REQUEST, attrs_both_true, FIX_IN_KEYS)
        s2, _ = evaluate_actions(actions, BASE_REQUEST, attrs_one_false, FIX_IN_KEYS)

        assert s1["rank"] == 70
        assert s2["rank"] == 50

    def test_when_with_or_condition(self) -> None:
        actions = [("SRM", "rank", "15", "+", "rank", "preferred_supplier = True OR esg_score >= 90")]
        attrs_neither = {"rank": 50, "preferred_supplier": False, "esg_score": 75}
        attrs_second = {"rank": 50, "preferred_supplier": False, "esg_score": 95}

        s1, _ = evaluate_actions(actions, BASE_REQUEST, attrs_neither, FIX_IN_KEYS)
        s2, _ = evaluate_actions(actions, BASE_REQUEST, attrs_second, FIX_IN_KEYS)

        assert s1["rank"] == 50
        assert s2["rank"] == 65

    def test_when_with_not_condition(self) -> None:
        actions = [("OSLM", "rank", "5", "-", "rank", "NOT is_restricted")]
        # NOT is_restricted is True → action runs
        attrs_not_restricted = {"rank": 50, "is_restricted": False}
        # NOT is_restricted is False → action skipped
        attrs_restricted = {"rank": 50, "is_restricted": True}

        s1, _ = evaluate_actions(actions, BASE_REQUEST, attrs_not_restricted, FIX_IN_KEYS)
        s2, _ = evaluate_actions(actions, BASE_REQUEST, attrs_restricted, FIX_IN_KEYS)

        assert s1["rank"] == 45
        assert s2["rank"] == 50

    def test_malformed_when_skips_action_safely(self) -> None:
        # "budget >=" has no RHS token → IndexError inside parse_atom → caught,
        # action skipped safely; rank must remain unchanged.
        actions = [("SRM", "rank", "99", "+", "rank", "budget >=")]
        attrs = {"rank": 50}
        state, _ = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)
        assert state["rank"] == 50


# ===========================================================================
# 4. rank ordering in supplier_results output
# ===========================================================================

def _make_supplier_with_pricing(
    supplier_id: str,
    unit_price: float,
    **kwargs,
) -> dict:
    """Set unit_price and cost_total (unit_price × BASE_REQUEST quantity=5) in attrs."""
    s = _make_supplier(supplier_id=supplier_id, **kwargs)
    s["attributes"]["unit_price"]  = unit_price
    s["attributes"]["cost_total"]  = unit_price * BASE_REQUEST["quantity"]  # qty=5
    return s


class TestRankOrdering:
    # BASE_REQUEST: quantity=5, budget=10_000, category_l2="Laptops"
    # Historical avg for IT/Laptops ≈ 965 EUR/unit (n=29)
    # budget per unit = 2_000; 5% overage cap → cost_total > 10_500 → penalty=0
    # All test unit_prices are above hist avg so base_cost_score = hist_avg/unit_price < 1

    def test_supplier_results_sorted_descending_by_normalized_rank(self) -> None:
        # Lower unit_price → higher base_cost_score → higher normalized_rank
        # SUP-A: 1100/unit (cheapest)  SUP-B: 1500  SUP-C: 2000 (most expensive)
        # All within budget (cost_totals: 5500, 7500, 10000)
        suppliers = [
            _make_supplier_with_pricing("SUP-A", 1100.0),
            _make_supplier_with_pricing("SUP-B", 1500.0),
            _make_supplier_with_pricing("SUP-C", 2000.0),
        ]
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], suppliers, FIX_IN_KEYS
        )
        scores = [r[1] for r in result["supplier_results"]]
        assert scores == sorted(scores, reverse=True)

    def test_lowest_unit_price_supplier_is_first(self) -> None:
        suppliers = [
            _make_supplier_with_pricing("SUP-EXPENSIVE", 2000.0),
            _make_supplier_with_pricing("SUP-CHEAP", 1200.0),
        ]
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], suppliers, FIX_IN_KEYS
        )
        assert result["supplier_results"][0][0]["supplier_id"] == "SUP-CHEAP"

    def test_over_budget_supplier_gets_zero_cost_score(self) -> None:
        # SUP-OVER: unit_price=2200 → cost_total=11_000 (10% over budget=10_000 → penalty=0)
        # SUP-UNDER: unit_price=1200 → cost_total=6_000 (within budget → penalty=1.0)
        # Supplier with rep_score=0: normalized_rank = 0.95*0 + 0.025*0 + 0.025*1 = 0.025
        suppliers = [
            _make_supplier_with_pricing("SUP-OVER", 2200.0),
            _make_supplier_with_pricing("SUP-UNDER", 1200.0),
        ]
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], suppliers, FIX_IN_KEYS
        )
        assert result["supplier_results"][0][0]["supplier_id"] == "SUP-UNDER"
        over_rank = result["supplier_results"][1][2].get("normalized_rank")
        import pytest as _pytest
        assert over_rank == _pytest.approx(0.025)  # 0.95*0 + 0.025*0 + 0.025*1

    def test_normalized_rank_is_in_zero_to_one_range(self) -> None:
        s = _make_supplier_with_pricing("SUP-X", 1500.0)
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        rank = result["supplier_results"][0][1]
        assert 0.0 <= rank <= 1.0

    def test_empty_filtered_list_returns_empty_results(self) -> None:
        # All suppliers are in wrong category
        s = _make_supplier(category_l2="Monitors")
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        assert result["supplier_results"] == []
        assert result["global_outputs"] == {}


# ===========================================================================
# 5. missing fix_in raises
# ===========================================================================

class TestMissingFixIn:
    def test_raises_on_single_missing_key(self) -> None:
        incomplete = {k: v for k, v in BASE_REQUEST.items() if k != "delivery_country"}
        with pytest.raises(KeyError, match="delivery_country"):
            build_global_context(incomplete, SCHEMA)

    def test_raises_on_multiple_missing_keys(self) -> None:
        incomplete = {k: v for k, v in BASE_REQUEST.items() if k not in ("budget", "currency")}
        with pytest.raises(KeyError):
            build_global_context(incomplete, SCHEMA)

    def test_all_keys_present_succeeds(self) -> None:
        ctx = build_global_context(BASE_REQUEST, SCHEMA)
        assert set(ctx.keys()) == FIX_IN_KEYS

    def test_meta_and_fix_out_keys_not_included_in_context(self) -> None:
        # meta and fix_out fields in request should not leak into context
        request_with_extras = dict(BASE_REQUEST)
        request_with_extras["request_id"] = "REQ-123"  # meta
        request_with_extras["rank"] = 99               # fix_out
        ctx = build_global_context(request_with_extras, SCHEMA)
        assert "request_id" not in ctx
        assert "rank" not in ctx

    def test_missing_fix_in_raised_by_run_procurement_evaluation(self) -> None:
        incomplete = {k: v for k, v in BASE_REQUEST.items() if k != "category_l1"}
        with pytest.raises(KeyError, match="category_l1"):
            run_procurement_evaluation(incomplete, SCHEMA, [], [], FIX_IN_KEYS)


# ===========================================================================
# 6. AL and ALI evaluation correctness
# ===========================================================================

class TestALandALI:
    # ---- ALI ---------------------------------------------------------------

    def test_ali_addition(self) -> None:
        actions = [("ALI", "quality_score", "5", "+", "rank")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80}, set())
        assert state["rank"] == 85

    def test_ali_subtraction(self) -> None:
        actions = [("ALI", "quality_score", "10", "-", "rank")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80}, set())
        assert state["rank"] == 70

    def test_ali_multiplication(self) -> None:
        actions = [("ALI", "quality_score", "2", "*", "rank")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80}, set())
        assert state["rank"] == 160

    def test_ali_equality_check(self) -> None:
        actions = [("ALI", "currency", "EUR", "=", "is_eur")]
        state, _ = evaluate_actions(actions, {}, {"currency": "EUR"}, set())
        assert state["is_eur"] is True

    def test_ali_immediate_is_never_resolved_from_state(self) -> None:
        """
        ALI's in_param2 is always a literal — even if a key with the same name
        (or numeric equivalent) exists in state its value must NOT be used.

        Trick: put key "50" → 999 in state.
          AL  would look up state["50"] = 999 → result = 80 + 999 = 1079
          ALI must parse "50" as the integer literal 50 → result = 80 + 50 = 130
        """
        # AL: in_param2 is a dict key → resolves state["50"] = 999
        al_actions = [("AL", "quality_score", "50", "+", "result")]
        al_state, _ = evaluate_actions(
            al_actions, {}, {"quality_score": 80, "50": 999}, set()
        )
        assert al_state["result"] == 1079  # looked up state key "50"

        # ALI: in_param2 is a literal → must be integer 50, ignoring state["50"]
        ali_actions = [("ALI", "quality_score", "50", "+", "result")]
        ali_state, _ = evaluate_actions(
            ali_actions, {}, {"quality_score": 80, "50": 999}, set()
        )
        assert ali_state["result"] == 130  # literal 50, not state["50"]

    def test_ali_boolean_immediate_true(self) -> None:
        actions = [("ALI", "_", "True", "=", "fast_track_eligible")]
        state, _ = evaluate_actions(actions, {}, {}, set())
        assert state["fast_track_eligible"] is True

    def test_ali_boolean_immediate_false(self) -> None:
        actions = [("ALI", "_", "False", "=", "requires_security_review")]
        state, _ = evaluate_actions(actions, {}, {}, set())
        assert state["requires_security_review"] is False

    def test_ali_float_immediate(self) -> None:
        actions = [("ALI", "quality_score", "0.5", "*", "adjusted")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80}, set())
        assert state["adjusted"] == pytest.approx(40.0)

    # ---- AL ----------------------------------------------------------------

    def test_al_addition(self) -> None:
        actions = [("AL", "quality_score", "esg_score", "+", "rank")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80, "esg_score": 70}, set())
        assert state["rank"] == 150

    def test_al_subtraction(self) -> None:
        actions = [("AL", "quality_score", "risk_score", "-", "rank")]
        state, _ = evaluate_actions(actions, {}, {"quality_score": 80, "risk_score": 20}, set())
        assert state["rank"] == 60

    def test_al_boolean_and(self) -> None:
        actions = [("AL", "flag_a", "flag_b", "AND", "result")]
        state, _ = evaluate_actions(actions, {}, {"flag_a": True, "flag_b": False}, set())
        assert state["result"] is False

    def test_al_boolean_or(self) -> None:
        actions = [("AL", "flag_a", "flag_b", "OR", "result")]
        state, _ = evaluate_actions(actions, {}, {"flag_a": False, "flag_b": True}, set())
        assert state["result"] is True

    def test_al_reads_from_global_context(self) -> None:
        # in_param2 lives in global_context, not supplier_attrs
        actions = [("AL", "quality_score", "budget", "+", "score")]
        state, _ = evaluate_actions(
            actions,
            {"budget": 1000},   # global context
            {"quality_score": 80},  # supplier attrs
            FIX_IN_KEYS,
        )
        assert state["score"] == 1080

    def test_al_chain_uses_previous_output(self) -> None:
        """Actions sorted in dependency order — second reads output of first."""
        actions = [
            ("ALI", "quality_score", "10", "+", "adjusted_quality"),
            ("AL", "adjusted_quality", "esg_score", "+", "rank"),
        ]
        state, _ = evaluate_actions(
            actions, {}, {"quality_score": 80, "esg_score": 70}, set()
        )
        assert state["adjusted_quality"] == 90
        assert state["rank"] == 160

    def test_al_overwrite_existing_key(self) -> None:
        # rank is already in attrs; AL should overwrite it
        actions = [("AL", "quality_score", "esg_score", "+", "rank")]
        state, _ = evaluate_actions(
            actions, {}, {"quality_score": 80, "esg_score": 70, "rank": 0}, set()
        )
        assert state["rank"] == 150

    def test_al_gte_comparison(self) -> None:
        actions = [("AL", "budget", "threshold", ">=", "qualifies")]
        state, _ = evaluate_actions(
            actions, {}, {"budget": 50_000, "threshold": 25_000}, set()
        )
        assert state["qualifies"] is True


# ===========================================================================
# 7. load_suppliers (CSV I/O)
# ===========================================================================

class TestLoadSuppliers:
    def _write_primary(self, path: pathlib.Path) -> None:
        rows = [
            {
                "supplier_id": "SUP-001",
                "supplier_name": "Alpha Corp",
                "category_l1": "IT",
                "category_l2": "Laptops",
                "country_hq": "DE",
                "service_regions": "DE;FR",
                "quality_score": "85",
                "risk_score": "20",
                "is_restricted": "False",
                "preferred_supplier": "True",
            },
            {
                "supplier_id": "SUP-002",
                "supplier_name": "Beta Ltd",
                "category_l1": "IT",
                "category_l2": "Monitors",
                "country_hq": "NL",
                "service_regions": "NL;BE",
                "quality_score": "75",
                "risk_score": "30",
                "is_restricted": "True",
                "preferred_supplier": "False",
            },
        ]
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def _write_extra(self, path: pathlib.Path) -> None:
        rows = [
            {"supplier_id": "SUP-001", "category_l2": "Laptops", "esg_score": "90"},
        ]
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_meta_columns_in_identity(self, tmp_path: pathlib.Path) -> None:
        primary = tmp_path / "suppliers.csv"
        self._write_primary(primary)
        suppliers = load_suppliers(str(primary), [])
        assert "supplier_id" in suppliers[0]["identity"]
        assert "service_regions" in suppliers[0]["identity"]

    def test_meta_columns_absent_from_attributes(self, tmp_path: pathlib.Path) -> None:
        primary = tmp_path / "suppliers.csv"
        self._write_primary(primary)
        suppliers = load_suppliers(str(primary), [])
        for meta_col in ("supplier_id", "supplier_name", "category_l1", "category_l2",
                         "country_hq", "service_regions"):
            assert meta_col not in suppliers[0]["attributes"]

    def test_attribute_types_coerced(self, tmp_path: pathlib.Path) -> None:
        primary = tmp_path / "suppliers.csv"
        self._write_primary(primary)
        suppliers = load_suppliers(str(primary), [])
        attrs = suppliers[0]["attributes"]
        assert isinstance(attrs["quality_score"], int)
        assert isinstance(attrs["is_restricted"], bool)
        assert isinstance(attrs["preferred_supplier"], bool)

    def test_extra_csv_joined_on_supplier_id_and_category_l2(
        self, tmp_path: pathlib.Path
    ) -> None:
        primary = tmp_path / "suppliers.csv"
        extra = tmp_path / "extra.csv"
        self._write_primary(primary)
        self._write_extra(extra)
        suppliers = load_suppliers(str(primary), [str(extra)])
        # SUP-001 / Laptops should gain esg_score from the extra CSV
        laptop_supplier = next(
            s for s in suppliers if s["identity"]["supplier_id"] == "SUP-001"
            and s["identity"]["category_l2"] == "Laptops"
        )
        assert laptop_supplier["attributes"]["esg_score"] == 90

    def test_no_extra_csvs_loads_primary_only(self, tmp_path: pathlib.Path) -> None:
        primary = tmp_path / "suppliers.csv"
        self._write_primary(primary)
        suppliers = load_suppliers(str(primary), [])
        assert len(suppliers) == 2

    def test_service_regions_retained_as_raw_string(self, tmp_path: pathlib.Path) -> None:
        primary = tmp_path / "suppliers.csv"
        self._write_primary(primary)
        suppliers = load_suppliers(str(primary), [])
        assert suppliers[0]["identity"]["service_regions"] == "DE;FR"


# ===========================================================================
# 8. build_global_context (additional happy-path tests)
# ===========================================================================

class TestBuildGlobalContext:
    def test_returns_only_fix_in_keys(self) -> None:
        ctx = build_global_context(BASE_REQUEST, SCHEMA)
        for key in ctx:
            assert key in FIX_IN_KEYS

    def test_values_match_request(self) -> None:
        ctx = build_global_context(BASE_REQUEST, SCHEMA)
        for key in FIX_IN_KEYS:
            assert ctx[key] == BASE_REQUEST[key]

    def test_supplier_matrix_fix_in_fields_not_required_from_request(self) -> None:
        """
        supplier_matrix fix_in fields (quality_score, risk_score, …) must NOT
        be demanded from the request dict — they come from supplier data.
        build_global_context must succeed without them.
        """
        schema_with_supplier_fields = list(SCHEMA) + [
            ("quality_score", "fix_in", "Quality score 0-100", "supplier_matrix"),
            ("risk_score",    "fix_in", "Risk score 0-100",    "supplier_matrix"),
            ("preferred_supplier", "fix_in", "Preferred flag", "supplier_matrix"),
        ]
        # BASE_REQUEST has none of these keys — must NOT raise
        ctx = build_global_context(BASE_REQUEST, schema_with_supplier_fields)
        assert "quality_score" not in ctx
        assert "risk_score" not in ctx
        assert "preferred_supplier" not in ctx

    def test_request_level_fix_in_still_required(self) -> None:
        """
        Adding supplier_matrix entries to the schema must not suppress
        validation of missing request-level fix_in fields.
        """
        schema_with_supplier_fields = list(SCHEMA) + [
            ("quality_score", "fix_in", "Quality score 0-100", "supplier_matrix"),
        ]
        incomplete = {k: v for k, v in BASE_REQUEST.items() if k != "budget"}
        with pytest.raises(KeyError, match="budget"):
            build_global_context(incomplete, schema_with_supplier_fields)


# ===========================================================================
# 8b. load_schema integration with real start_dict.csv
# ===========================================================================

REAL_CSV = pathlib.Path(__file__).parent / "start_dict.csv"


@pytest.mark.skipif(not REAL_CSV.exists(), reason="start_dict.csv not present")
class TestLoadSchema:
    def test_returns_four_tuples(self) -> None:
        schema, _ = load_schema(str(REAL_CSV))
        for entry in schema:
            assert len(entry) == 4, f"Expected 4-tuple, got {len(entry)}: {entry}"

    def test_tuple_order_is_name_type_desc_relevance(self) -> None:
        schema, _ = load_schema(str(REAL_CSV))
        # budget is a known request-level fix_in with no relevance tag
        budget = next(e for e in schema if e[0] == "budget")
        assert budget[1] == "fix_in"
        assert "budget" in budget[2].lower()
        assert budget[3] == ""

    def test_supplier_matrix_fields_have_relevance_tag(self) -> None:
        schema, _ = load_schema(str(REAL_CSV))
        supplier_matrix_entries = [e for e in schema if e[3] == "supplier_matrix"]
        names = {e[0] for e in supplier_matrix_entries}
        for expected in ("quality_score", "risk_score", "esg_score",
                         "preferred_supplier", "capacity_per_month",
                         "serves_delivery_country"):
            assert expected in names, f"{expected!r} not tagged supplier_matrix"

    def test_fix_in_keys_includes_both_request_and_supplier_matrix(self) -> None:
        _, fix_in_keys = load_schema(str(REAL_CSV))
        # Request-level
        assert "budget" in fix_in_keys
        assert "delivery_country" in fix_in_keys
        # Supplier-matrix
        assert "quality_score" in fix_in_keys
        assert "risk_score" in fix_in_keys

    def test_meta_entries_included_in_schema_but_not_fix_in_keys(self) -> None:
        schema, fix_in_keys = load_schema(str(REAL_CSV))
        meta_names = {e[0] for e in schema if e[1] == "meta"}
        assert "request_id" in meta_names
        assert "supplier_id" in meta_names
        for name in meta_names:
            assert name not in fix_in_keys

    def test_build_global_context_with_real_schema(self) -> None:
        """
        Using the real start_dict.csv, build_global_context must succeed with
        only request-level fields in the request — supplier_matrix fix_in fields
        must not be demanded.
        """
        schema, _ = load_schema(str(REAL_CSV))
        real_request = {
            "category_l1": "IT",
            "category_l2": "Laptops",
            "budget": 10_000,
            "currency": "EUR",
            "quantity": 5,
            "amount_unit": "devices",
            "delivery_country": "DE",
            "days_until_required": 30,
            "preferred_supplier_mentioned": None,
            "incumbent_supplier": None,
            "data_residency_constraint": False,
            "esg_requirement": False,
        }
        ctx = build_global_context(real_request, schema)
        # Must contain request-level fix_in fields
        assert ctx["budget"] == 10_000
        assert ctx["delivery_country"] == "DE"
        # Must NOT contain supplier-matrix fields
        assert "quality_score" not in ctx
        assert "risk_score" not in ctx
        assert "rank" not in ctx


# ===========================================================================
# 9. _eval_when unit tests
# ===========================================================================

class TestEvalWhen:
    def test_simple_equality_true(self) -> None:
        assert _eval_when("currency = EUR", {"currency": "EUR"}) is True

    def test_simple_equality_false(self) -> None:
        assert _eval_when("currency = CHF", {"currency": "EUR"}) is False

    def test_greater_than_or_equal(self) -> None:
        assert _eval_when("budget >= 10000", {"budget": 10_000}) is True
        assert _eval_when("budget >= 10001", {"budget": 10_000}) is False

    def test_less_than(self) -> None:
        assert _eval_when("risk_score < 25", {"risk_score": 20}) is True
        assert _eval_when("risk_score < 20", {"risk_score": 20}) is False

    def test_not_equal(self) -> None:
        assert _eval_when("currency != CHF", {"currency": "EUR"}) is True

    def test_and_both_true(self) -> None:
        state = {"a": True, "b": True}
        assert _eval_when("a AND b", state) is True

    def test_and_one_false(self) -> None:
        state = {"a": True, "b": False}
        assert _eval_when("a AND b", state) is False

    def test_or_one_true(self) -> None:
        state = {"a": False, "b": True}
        assert _eval_when("a OR b", state) is True

    def test_or_both_false(self) -> None:
        state = {"a": False, "b": False}
        assert _eval_when("a OR b", state) is False

    def test_not_inverts_value(self) -> None:
        assert _eval_when("NOT is_restricted", {"is_restricted": False}) is True
        assert _eval_when("NOT is_restricted", {"is_restricted": True}) is False

    def test_when_keyword_prefix_is_stripped(self) -> None:
        assert _eval_when("WHEN budget >= 5000", {"budget": 10_000}) is True

    def test_parenthesised_subexpr(self) -> None:
        state = {"a": True, "b": False, "c": True}
        # a AND (b OR c) should be True
        assert _eval_when("a AND (b OR c)", state) is True

    def test_bare_boolean_key_reference(self) -> None:
        assert _eval_when("preferred_supplier", {"preferred_supplier": True}) is True
        assert _eval_when("preferred_supplier", {"preferred_supplier": False}) is False

    def test_numeric_comparison_with_state_key(self) -> None:
        assert _eval_when("quality_score >= 80", {"quality_score": 85}) is True
        assert _eval_when("quality_score >= 90", {"quality_score": 85}) is False


# ===========================================================================
# 10. run_procurement_evaluation (integration)
# ===========================================================================

class TestRunProcurementEvaluation:
    def test_global_outputs_contain_fix_out_keys(self) -> None:
        actions = [("ALI", "_", "3", "=", "min_supplier_quotes")]
        s = _make_supplier()
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, actions, [s], FIX_IN_KEYS
        )
        assert "min_supplier_quotes" in result["global_outputs"]
        assert result["global_outputs"]["min_supplier_quotes"] == 3

    def test_rank_not_in_global_outputs(self) -> None:
        s = _make_supplier()
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        assert "rank" not in result["global_outputs"]

    def test_supplier_results_contain_identity_rank_state(self) -> None:
        s = _make_supplier()
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        identity, rank, state = result["supplier_results"][0]
        assert "supplier_id" in identity
        assert isinstance(rank, (int, float))
        assert "quality_score" in state

    def test_restricted_suppliers_absent_from_results(self) -> None:
        ok = _make_supplier(supplier_id="SUP-OK", is_restricted=False)
        bad = _make_supplier(supplier_id="SUP-BAD", is_restricted=True)
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [ok, bad], FIX_IN_KEYS
        )
        ids = [r[0]["supplier_id"] for r in result["supplier_results"]]
        assert "SUP-BAD" not in ids
        assert "SUP-OK" in ids

    def test_suppliers_outside_delivery_country_absent(self) -> None:
        inside = _make_supplier(supplier_id="SUP-IN", service_regions="DE;FR")
        outside = _make_supplier(supplier_id="SUP-OUT", service_regions="US;CA")
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [inside, outside], FIX_IN_KEYS
        )
        ids = [r[0]["supplier_id"] for r in result["supplier_results"]]
        assert "SUP-OUT" not in ids
        assert "SUP-IN" in ids


# ===========================================================================
# 11. Pricing tier resolution
# ===========================================================================

def _make_pricing_index(
    supplier_id: str = "SUP-001",
    category_l2: str = "Laptops",
    region: str = "EU",
    tiers: list[dict] | None = None,
) -> dict:
    """Build a minimal pricing index for tests."""
    if tiers is None:
        tiers = [
            {
                "min_quantity": 1,
                "max_quantity": 99,
                "unit_price": 980.0,
                "standard_lead_time_days": 27,
                "expedited_lead_time_days": 22,
                "expedited_unit_price": 1058.4,
                "pricing_model": "tiered",
                "currency": "EUR",
            },
            {
                "min_quantity": 100,
                "max_quantity": 499,
                "unit_price": 930.0,
                "standard_lead_time_days": 25,
                "expedited_lead_time_days": 17,
                "expedited_unit_price": 1004.4,
                "pricing_model": "tiered",
                "currency": "EUR",
            },
        ]
    return {(supplier_id, category_l2, region): tiers}


class TestPricingTierResolution:
    def test_resolves_correct_tier_for_quantity_in_range(self) -> None:
        index = _make_pricing_index()
        identity = {"supplier_id": "SUP-001", "category_l2": "Laptops"}
        ctx = {"delivery_country": "DE", "quantity": 5}
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result["unit_price"] == 980.0
        assert result["standard_lead_time_days"] == 27

    def test_resolves_higher_tier_for_larger_quantity(self) -> None:
        index = _make_pricing_index()
        identity = {"supplier_id": "SUP-001", "category_l2": "Laptops"}
        ctx = {"delivery_country": "DE", "quantity": 150}
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result["unit_price"] == 930.0

    def test_returns_empty_dict_for_quantity_outside_all_tiers(self) -> None:
        index = _make_pricing_index()
        identity = {"supplier_id": "SUP-001", "category_l2": "Laptops"}
        ctx = {"delivery_country": "DE", "quantity": 10_000}
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result == {}

    def test_returns_empty_dict_for_unknown_supplier(self) -> None:
        index = _make_pricing_index()
        identity = {"supplier_id": "SUP-UNKNOWN", "category_l2": "Laptops"}
        ctx = {"delivery_country": "DE", "quantity": 5}
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result == {}

    def test_returns_empty_dict_for_unknown_delivery_country(self) -> None:
        index = _make_pricing_index()
        identity = {"supplier_id": "SUP-001", "category_l2": "Laptops"}
        ctx = {"delivery_country": "XX", "quantity": 5}  # unmapped country
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result == {}

    def test_pricing_fields_injected_into_surviving_supplier_attrs(self) -> None:
        index = _make_pricing_index()
        s = _make_supplier(supplier_id="SUP-001", service_regions="DE;FR")
        ctx = {**BASE_REQUEST, "quantity": 5}
        filtered = filter_suppliers([s], ctx, pricing_index=index)
        assert len(filtered) == 1
        assert filtered[0]["attributes"]["unit_price"] == 980.0
        assert filtered[0]["attributes"]["currency"] == "EUR"

    def test_supplier_excluded_when_no_matching_pricing_tier(self) -> None:
        # quantity=10000 exceeds all tiers in the index
        index = _make_pricing_index()
        s = _make_supplier(supplier_id="SUP-001", service_regions="DE;FR")
        ctx = {**BASE_REQUEST, "quantity": 10_000}
        filtered = filter_suppliers([s], ctx, pricing_index=index)
        assert len(filtered) == 0

    def test_load_pricing_index_from_real_csv(
        self, tmp_path: pathlib.Path
    ) -> None:
        csv_path = tmp_path / "pricing.csv"
        rows = [
            {
                "pricing_id": "PR-001",
                "supplier_id": "SUP-001",
                "category_l1": "IT",
                "category_l2": "Laptops",
                "region": "EU",
                "currency": "EUR",
                "pricing_model": "tiered",
                "min_quantity": "1",
                "max_quantity": "99",
                "unit_price": "980.0",
                "moq": "1",
                "standard_lead_time_days": "27",
                "expedited_lead_time_days": "22",
                "expedited_unit_price": "1058.4",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "notes": "",
            }
        ]
        with open(csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        index = load_pricing_index(str(csv_path))
        assert ("SUP-001", "Laptops", "EU") in index
        tier = index[("SUP-001", "Laptops", "EU")][0]
        assert tier["unit_price"] == 980.0
        assert tier["min_quantity"] == 1


# ===========================================================================
# 12. Country-to-region mapping
# ===========================================================================

class TestCountryToRegionMapping:
    def test_ch_maps_to_ch_not_eu(self) -> None:
        assert COUNTRY_TO_REGION["CH"] == "CH"

    def test_eu_countries_map_to_eu(self) -> None:
        for code in ("DE", "FR", "NL", "AT", "BE", "IT", "ES", "PL", "PT"):
            assert COUNTRY_TO_REGION[code] == "EU", f"{code} should map to EU"

    def test_americas_countries_map_correctly(self) -> None:
        for code in ("US", "CA", "MX", "BR"):
            assert COUNTRY_TO_REGION[code] == "Americas"

    def test_apac_countries_map_correctly(self) -> None:
        for code in ("AU", "JP", "SG", "IN"):
            assert COUNTRY_TO_REGION[code] == "APAC"

    def test_mea_countries_map_correctly(self) -> None:
        for code in ("AE", "UAE", "ZA"):
            assert COUNTRY_TO_REGION[code] == "MEA"

    def test_ch_priority_over_eu_in_pricing_resolution(self) -> None:
        """Delivery to CH must resolve the CH pricing tier, not EU."""
        ch_tier = [
            {
                "min_quantity": 1,
                "max_quantity": 999,
                "unit_price": 1050.0,  # CH-specific price
                "standard_lead_time_days": 5,
                "expedited_lead_time_days": 2,
                "expedited_unit_price": 1200.0,
                "pricing_model": "tiered",
                "currency": "CHF",
            }
        ]
        eu_tier = [
            {
                "min_quantity": 1,
                "max_quantity": 999,
                "unit_price": 980.0,
                "standard_lead_time_days": 27,
                "expedited_lead_time_days": 22,
                "expedited_unit_price": 1058.4,
                "pricing_model": "tiered",
                "currency": "EUR",
            }
        ]
        index = {
            ("SUP-001", "Laptops", "CH"): ch_tier,
            ("SUP-001", "Laptops", "EU"): eu_tier,
        }
        identity = {"supplier_id": "SUP-001", "category_l2": "Laptops"}
        ctx = {"delivery_country": "CH", "quantity": 10}
        result = resolve_supplier_pricing(identity, index, ctx)
        assert result["unit_price"] == 1050.0
        assert result["currency"] == "CHF"


# ===========================================================================
# 13. cost_total base computation
# ===========================================================================

class TestCostTotalBaseComputation:
    def test_cost_total_is_quantity_times_unit_price(self) -> None:
        actions = [("AL", "quantity", "unit_price", "*", "cost_total")]
        state, _ = evaluate_actions(
            actions,
            {"quantity": 10},
            {"unit_price": 980.0},
            FIX_IN_KEYS,
        )
        assert state["cost_total"] == pytest.approx(9800.0)

    def test_cost_total_adjustable_by_rules_modifier(self) -> None:
        """Rules can add a surcharge on top of base cost_total."""
        actions = [
            ("AL", "quantity", "unit_price", "*", "cost_total"),
            ("ALI", "cost_total", "500", "+", "cost_total"),  # switching cost
        ]
        state, _ = evaluate_actions(
            actions,
            {"quantity": 10},
            {"unit_price": 980.0},
            FIX_IN_KEYS,
        )
        assert state["cost_total"] == pytest.approx(10300.0)

    def test_cost_total_from_real_pricing_context(self) -> None:
        """End-to-end: pricing fields injected by filter_suppliers feed cost_total action."""
        index = _make_pricing_index()
        s = _make_supplier(supplier_id="SUP-001", service_regions="DE;FR")
        ctx = {**BASE_REQUEST, "quantity": 5}
        filtered = filter_suppliers([s], ctx, pricing_index=index)
        assert len(filtered) == 1

        actions = [("AL", "quantity", "unit_price", "*", "cost_total")]
        final_state, _ = evaluate_actions(
            actions,
            ctx,
            filtered[0]["attributes"],
            FIX_IN_KEYS,
        )
        # 5 units * 980.0 = 4900.0
        assert final_state["cost_total"] == pytest.approx(4900.0)


# ===========================================================================
# 14. Lexicographic sort
# ===========================================================================

class TestLexicographicSort:
    def test_reputation_score_breaks_tie_when_unit_price_equal(self) -> None:
        """Same unit_price → same cost component; reputation_score (2.5%) decides order."""
        sup_a = _make_supplier_with_pricing("SUP-A", 1500.0, reputation_score=80.0)
        sup_b = _make_supplier_with_pricing("SUP-B", 1500.0, reputation_score=60.0)
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [sup_a, sup_b], FIX_IN_KEYS
        )
        ids = [r[0]["supplier_id"] for r in result["supplier_results"]]
        assert ids[0] == "SUP-A"  # higher reputation wins when cost tied

    def test_cost_dominates_over_reputation(self) -> None:
        """Lower unit_price (95% weight) always beats higher reputation (2.5% weight)."""
        # SUP-A: 1200/unit, rep=0  → high cost_score, low rep_norm → wins on cost
        # SUP-B: 2000/unit, rep=100 → lower cost_score, max rep_norm → still loses
        sup_a = _make_supplier_with_pricing("SUP-A", 1200.0, reputation_score=0.0)
        sup_b = _make_supplier_with_pricing("SUP-B", 2000.0, reputation_score=100.0)
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [sup_a, sup_b], FIX_IN_KEYS
        )
        ids = [r[0]["supplier_id"] for r in result["supplier_results"]]
        assert ids[0] == "SUP-A"

    def test_normalized_rank_returned_as_tuple_rank(self) -> None:
        """The rank value in the supplier_results tuple is normalized_rank in [0, 1]."""
        s = _make_supplier_with_pricing("SUP-X", 1500.0)
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        _, rank, _ = result["supplier_results"][0]
        assert 0.0 <= rank <= 1.0

    def test_three_suppliers_sorted_by_normalized_rank(self) -> None:
        # Lower unit_price → cheaper vs blended avg → higher base_cost_score → first
        # SUP-A: 1100/unit (cheapest, cost_total=5500)
        # SUP-B: 1500/unit (mid,     cost_total=7500)
        # SUP-C: 2000/unit (priciest, cost_total=10000, at budget)
        suppliers = [
            _make_supplier_with_pricing("SUP-C", 2000.0),
            _make_supplier_with_pricing("SUP-A", 1100.0),
            _make_supplier_with_pricing("SUP-B", 1500.0),
        ]
        result, _ = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], suppliers, FIX_IN_KEYS
        )
        ids = [r[0]["supplier_id"] for r in result["supplier_results"]]
        assert ids == ["SUP-A", "SUP-B", "SUP-C"]


# ===========================================================================
# 15. Generated actions JSON round-trip
# ===========================================================================

class TestGeneratedActionsRoundTrip:
    def test_tuples_preserved_after_save_and_load(
        self, tmp_path: pathlib.Path
    ) -> None:
        ranking = [
            ("AL", "quantity", "unit_price", "*", "cost_total"),
            ("OSLM", "_rep_q", "0.5", "*", "_rep_q_w"),
        ]
        rules = [
            ("SRM", "rank", "10", "+", "rank", "preferred_supplier = True"),
            ("ALI", "cost_total", "500", "+", "cost_total"),
        ]
        path = str(tmp_path / "actions.json")
        save_generated_actions(ranking, rules, path)
        loaded_ranking, loaded_rules, _, _ = load_generated_actions(path)

        assert loaded_ranking == ranking
        assert loaded_rules == rules

    def test_round_trip_with_six_element_tuples(
        self, tmp_path: pathlib.Path
    ) -> None:
        ranking = [
            ("OSLM", "cost_total", "100", "/", "cost_rank_score", "cost_total > 0"),
        ]
        rules: list[tuple] = []
        path = str(tmp_path / "actions2.json")
        save_generated_actions(ranking, rules, path)
        loaded_ranking, loaded_rules, _, _ = load_generated_actions(path)

        assert loaded_ranking[0] == ("OSLM", "cost_total", "100", "/", "cost_rank_score", "cost_total > 0")
        assert loaded_rules == []

    def test_empty_lists_round_trip(self, tmp_path: pathlib.Path) -> None:
        path = str(tmp_path / "empty.json")
        save_generated_actions([], [], path)
        r, s, _, _ = load_generated_actions(path)
        assert r == []
        assert s == []
