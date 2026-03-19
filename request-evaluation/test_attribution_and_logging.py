"""
test_attribution_and_logging.py — pytest suite for the attribution and execution
logging system added to the ChainIQ procurement pipeline.

Coverage:
  1. Attribution dict is correctly rekeyed after topo-sort reorders actions
  2. Skipped actions (WHEN failed) appear in log with skipped=True and no output values
  3. input_values snapshot captures values at execution time, not definition time
  4. render_log produces one block per supplier with correct rule_id attribution
  5. Log round-trips through JSON serialisation without data loss
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

import pytest

from sort_actions import sort_actions
from supplier_matrix import (
    ActionLogEntry,
    RequestExecutionLog,
    SupplierLog,
    _check_exclusion,
    evaluate_actions,
    render_log,
    run_procurement_evaluation,
    save_log,
)

# ---------------------------------------------------------------------------
# Shared helpers
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
    supplier_id: str = "SUP-001",
    supplier_name: str = "ACME Devices",
    category_l1: str = "IT",
    category_l2: str = "Laptops",
    service_regions: str = "DE;FR;NL",
    is_restricted: bool = False,
    quality_score: int = 80,
    risk_score: int = 20,
    esg_score: int = 75,
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
            "country_hq": "DE",
            "service_regions": service_regions,
        },
        "attributes": {
            "is_restricted": is_restricted,
            "quality_score": quality_score,
            "risk_score": risk_score,
            "esg_score": esg_score,
            "rank": rank,
            "cost_rank_score": cost_rank_score,
            "reputation_score": reputation_score,
        },
    }


# ===========================================================================
# 1. Attribution dict rekeyed correctly after topo-sort
# ===========================================================================

class TestAttributionRekeying:
    def test_attribution_preserved_when_order_unchanged(self) -> None:
        """Actions already in topological order: attribution indices unchanged."""
        A = ("AL", "_", "_", "=", "x")
        B = ("AL", "x", "_", "=", "y")
        attribution = {
            0: {"rule_id": "R1", "rule_description": "produces x"},
            1: {"rule_id": "R2", "rule_description": "produces y"},
        }
        sorted_acts, _, new_attr = sort_actions([A, B], set(), attribution=attribution)

        # A must come before B and keep its mapping
        assert sorted_acts[0] is A
        assert sorted_acts[1] is B
        assert new_attr[0]["rule_id"] == "R1"
        assert new_attr[1]["rule_id"] == "R2"

    def test_attribution_rekeyed_when_order_reversed(self) -> None:
        """
        Actions fed in reverse dependency order (B, A) where A writes key read by B.
        After sort A should be first → its attribution must appear at index 0.
        """
        A = ("AL", "_", "_", "=", "base")
        B = ("AL", "base", "_", "=", "result")
        attribution = {
            0: {"rule_id": "B-RULE", "rule_description": "reads base → result"},
            1: {"rule_id": "A-RULE", "rule_description": "produces base"},
        }
        # Feed in reverse order: [B, A]
        sorted_acts, low, new_attr = sort_actions([B, A], set(), attribution=attribution)

        assert low is False
        # A (original index 1) should come first after sorting
        assert sorted_acts[0] is A
        assert sorted_acts[1] is B
        # Original index 1 (A-RULE) should now be at new index 0
        assert new_attr[0]["rule_id"] == "A-RULE"
        # Original index 0 (B-RULE) should now be at new index 1
        assert new_attr[1]["rule_id"] == "B-RULE"

    def test_attribution_partial_coverage_handled(self) -> None:
        """Not all actions need attribution entries — missing indices stay absent."""
        A = ("AL", "_", "_", "=", "x")
        B = ("AL", "x", "_", "=", "y")
        C = ("AL", "y", "_", "=", "z")
        # Only attribute action B (original index 1)
        attribution = {1: {"rule_id": "ONLY-B", "rule_description": "only B"}}
        sorted_acts, _, new_attr = sort_actions([A, B, C], set(), attribution=attribution)

        # After sort: A(0)→B(1)→C(2), attribution for B should be at new_idx=1
        assert len(new_attr) == 1
        assert new_attr[1]["rule_id"] == "ONLY-B"

    def test_empty_attribution_returns_empty_dict(self) -> None:
        A = ("AL", "_", "_", "=", "x")
        _, _, new_attr = sort_actions([A], set(), attribution=None)
        assert new_attr == {}

    def test_attribution_rekeying_with_fix_in_keys(self) -> None:
        """fix_in keys don't create edges, but attribution must still survive sorting."""
        A = ("AL", "budget", "_", "=", "x")     # reads fix_in
        B = ("AL", "x", "_", "=", "y")
        attribution = {
            0: {"rule_id": "R-A", "rule_description": "uses budget"},
            1: {"rule_id": "R-B", "rule_description": "uses x"},
        }
        _, _, new_attr = sort_actions([A, B], fix_in_keys={"budget"}, attribution=attribution)
        assert new_attr[0]["rule_id"] == "R-A"
        assert new_attr[1]["rule_id"] == "R-B"


# ===========================================================================
# 2. Skipped actions (WHEN failed) appear in log with skipped=True
# ===========================================================================

class TestSkippedActionLogging:
    def test_when_false_produces_skipped_entry(self) -> None:
        """OSLM with failing WHEN → skipped=True, output values both None."""
        actions = [("OSLM", "rank", "10", "+", "rank", "preferred_supplier = True")]
        attrs = {"rank": 50, "preferred_supplier": False}
        attribution = {
            0: {"rule_id": "SKIP-TEST", "rule_description": "preferred boost"},
        }
        state, logs = evaluate_actions(actions, {}, attrs, set(), attribution=attribution)

        assert state["rank"] == 50  # unchanged
        assert len(logs) == 1
        entry = logs[0]
        assert entry.skipped is True
        assert entry.when_evaluated is True
        assert entry.when_passed is False
        assert entry.output_value_before is None
        assert entry.output_value_after is None
        assert entry.rule_id == "SKIP-TEST"

    def test_when_true_produces_non_skipped_entry(self) -> None:
        """OSLM with passing WHEN → skipped=False, output values recorded."""
        actions = [("OSLM", "rank", "10", "+", "rank", "preferred_supplier = True")]
        attrs = {"rank": 50, "preferred_supplier": True}
        state, logs = evaluate_actions(actions, {}, attrs, set())

        assert state["rank"] == 60
        assert len(logs) == 1
        entry = logs[0]
        assert entry.skipped is False
        assert entry.when_passed is True
        assert entry.output_value_before == 50
        assert entry.output_value_after == 60

    def test_al_never_skipped_due_to_when(self) -> None:
        """AL (unconditional) is never skipped; when_evaluated=False."""
        actions = [("AL", "quality_score", "esg_score", "+", "rank")]
        attrs = {"quality_score": 80, "esg_score": 70}
        state, logs = evaluate_actions(actions, {}, attrs, set())

        assert state["rank"] == 150
        entry = logs[0]
        assert entry.skipped is False
        assert entry.when_evaluated is False
        assert entry.when_passed is True

    def test_malformed_when_produces_skipped_entry(self) -> None:
        """Malformed WHEN expression → action skipped safely, logged as skipped."""
        actions = [("SRM", "rank", "99", "+", "rank", "budget >=")]
        attrs = {"rank": 50}
        state, logs = evaluate_actions(actions, BASE_REQUEST, attrs, FIX_IN_KEYS)

        assert state["rank"] == 50
        assert logs[0].skipped is True

    def test_multiple_actions_mixed_skip(self) -> None:
        """Pipeline with one passing and one failing WHEN; both logged."""
        actions = [
            ("OSLM", "rank", "20", "+", "rank", "preferred_supplier = True"),
            ("OSLM", "rank", "5", "-", "rank", "risk_score >= 50"),
        ]
        attrs = {"rank": 50, "preferred_supplier": True, "risk_score": 10}
        state, logs = evaluate_actions(actions, {}, attrs, set())

        assert state["rank"] == 70  # +20 applied, -5 skipped
        assert len(logs) == 2
        assert logs[0].skipped is False
        assert logs[1].skipped is True


# ===========================================================================
# 3. input_values snapshot captures values at execution time
# ===========================================================================

class TestInputValuesSnapshot:
    def test_snapshot_reflects_state_at_evaluation_moment(self) -> None:
        """
        Second action reads 'x' which was written by the first.
        Its input_values must show the value written by action 1, not the
        initial state.
        """
        actions = [
            ("ALI", "_", "100", "=", "x"),          # sets x = 100
            ("ALI", "x", "5", "+", "y"),             # reads x, adds 5
        ]
        _, logs = evaluate_actions(actions, {}, {}, set())

        # After action 0 executes, x=100
        # Action 1's snapshot must show x=100 (the updated value)
        assert logs[1].input_values["x"] == 100

    def test_snapshot_for_ali_includes_immediate_not_state_lookup(self) -> None:
        """ALI's in_param2 is a literal; snapshot key is 'immediate'."""
        actions = [("ALI", "quality_score", "42", "+", "result")]
        _, logs = evaluate_actions(actions, {}, {"quality_score": 80}, set())

        entry = logs[0]
        assert "quality_score" in entry.input_values
        assert entry.input_values["quality_score"] == 80
        assert "immediate" in entry.input_values
        assert entry.input_values["immediate"] == 42

    def test_snapshot_for_al_includes_both_keys(self) -> None:
        """AL reads both in_param1 and in_param2 as dict keys."""
        actions = [("AL", "quality_score", "esg_score", "+", "rank")]
        _, logs = evaluate_actions(
            actions, {}, {"quality_score": 80, "esg_score": 70}, set()
        )
        entry = logs[0]
        assert entry.input_values["quality_score"] == 80
        assert entry.input_values["esg_score"] == 70

    def test_snapshot_for_global_context_key(self) -> None:
        """Keys from global_context are also captured in the snapshot."""
        actions = [("AL", "quality_score", "budget", "+", "score")]
        _, logs = evaluate_actions(
            actions,
            {"budget": 1000},
            {"quality_score": 80},
            FIX_IN_KEYS,
        )
        assert logs[0].input_values["budget"] == 1000
        assert logs[0].input_values["quality_score"] == 80

    def test_snapshot_for_when_skipped_action_still_captured(self) -> None:
        """Even when an action is skipped, its input snapshot is populated."""
        actions = [
            ("OSLM", "rank", "10", "+", "rank", "preferred_supplier = True"),
        ]
        attrs = {"rank": 50, "preferred_supplier": False}
        _, logs = evaluate_actions(actions, {}, attrs, set())

        entry = logs[0]
        assert entry.skipped is True
        # Input values should still have been captured before the WHEN check
        assert "rank" in entry.input_values
        assert entry.input_values["rank"] == 50


# ===========================================================================
# 4. render_log produces correct structure
# ===========================================================================

class TestRenderLog:
    def _build_log(
        self,
        supplier_name: str = "Test Corp",
        supplier_id: str = "SUP-001",
        rule_id: str = "AT-001",
        rule_description: str = "Set quotes",
        skipped: bool = False,
    ) -> RequestExecutionLog:
        entry = ActionLogEntry(
            action_index=0,
            rule_id=rule_id,
            rule_description=rule_description,
            action_type="ALI",
            action_tuple=("ALI", "_", "3", "=", "min_supplier_quotes"),
            when_condition=None,
            when_evaluated=False,
            when_passed=True,
            input_values={},
            output_key="min_supplier_quotes",
            output_value_before=None if skipped else 0,
            output_value_after=None if skipped else 3,
            skipped=skipped,
        )
        sl = SupplierLog(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            category_l2="Laptops",
            pricing_resolved={"unit_price": 980.0, "currency": "EUR"},
            action_logs=[entry],
            final_state={"cost_total": 4900.0},
            final_cost_rank_score=67.5,
            final_reputation_score=72.0,
            final_compliance_score=None,
            final_normalized_rank=None,
            excluded=False,
            exclusion_reason=None,
        )
        return RequestExecutionLog(
            request_id="REQ-TEST-001",
            timestamp="2026-03-19T10:00:00+00:00",
            global_context_snapshot={"quantity": 5, "delivery_country": "DE"},
            supplier_logs=[sl],
            global_action_logs=[],
        )

    def test_render_contains_supplier_name(self) -> None:
        log = self._build_log(supplier_name="Alpha Devices")
        rendered = render_log(log)
        assert "Alpha Devices" in rendered

    def test_render_contains_supplier_id(self) -> None:
        log = self._build_log(supplier_id="SUP-XYZ")
        rendered = render_log(log)
        assert "SUP-XYZ" in rendered

    def test_render_contains_rule_id(self) -> None:
        log = self._build_log(rule_id="AT-007")
        rendered = render_log(log)
        assert "AT-007" in rendered

    def test_render_contains_rule_description(self) -> None:
        log = self._build_log(rule_description="Minimum three quotes required")
        rendered = render_log(log)
        assert "Minimum three quotes required" in rendered

    def test_render_contains_request_id(self) -> None:
        log = self._build_log()
        rendered = render_log(log)
        assert "REQ-TEST-001" in rendered

    def test_render_one_supplier_block_per_supplier(self) -> None:
        """Each supplier produces exactly one SUPPLIER: header."""
        log = self._build_log()
        rendered = render_log(log)
        assert rendered.count("SUPPLIER:") == 1

    def test_render_two_suppliers_two_blocks(self) -> None:
        """Two suppliers → two SUPPLIER: headers."""
        sl1 = SupplierLog(
            supplier_id="SUP-A", supplier_name="Alpha", category_l2="Laptops",
            pricing_resolved={}, action_logs=[], final_state={},
            final_cost_rank_score=None, final_reputation_score=None,
            final_compliance_score=None, final_normalized_rank=None, excluded=False, exclusion_reason=None,
        )
        sl2 = SupplierLog(
            supplier_id="SUP-B", supplier_name="Beta", category_l2="Laptops",
            pricing_resolved={}, action_logs=[], final_state={},
            final_cost_rank_score=None, final_reputation_score=None,
            final_compliance_score=None, final_normalized_rank=None, excluded=False, exclusion_reason=None,
        )
        log = RequestExecutionLog(
            request_id="REQ-999",
            timestamp="2026-03-19T00:00:00+00:00",
            global_context_snapshot={},
            supplier_logs=[sl1, sl2],
            global_action_logs=[],
        )
        rendered = render_log(log)
        assert rendered.count("SUPPLIER:") == 2
        assert "Alpha" in rendered
        assert "Beta" in rendered

    def test_render_excluded_supplier_shows_exclusion_reason(self) -> None:
        sl = SupplierLog(
            supplier_id="SUP-BAD", supplier_name="Blocked Corp", category_l2="Laptops",
            pricing_resolved={}, action_logs=[], final_state={},
            final_cost_rank_score=None, final_reputation_score=None,
            final_compliance_score=None, final_normalized_rank=None, excluded=True, exclusion_reason="supplier is restricted",
        )
        log = RequestExecutionLog(
            request_id="REQ-EXC", timestamp="2026-03-19T00:00:00+00:00",
            global_context_snapshot={}, supplier_logs=[sl], global_action_logs=[],
        )
        rendered = render_log(log)
        assert "EXCLUDED" in rendered
        assert "supplier is restricted" in rendered

    def test_render_skipped_action_shows_skipped(self) -> None:
        log = self._build_log(skipped=True)
        rendered = render_log(log)
        assert "SKIPPED" in rendered

    def test_render_when_na_for_no_condition(self) -> None:
        log = self._build_log()
        rendered = render_log(log)
        assert "N/A" in rendered


# ===========================================================================
# 5. JSON round-trip without data loss
# ===========================================================================

class TestJsonRoundTrip:
    def _build_minimal_log(self) -> RequestExecutionLog:
        entry = ActionLogEntry(
            action_index=0,
            rule_id="AT-001",
            rule_description="Sets min quotes",
            action_type="ALI",
            action_tuple=("ALI", "_", "2", "=", "min_supplier_quotes"),
            when_condition="budget >= 25000",
            when_evaluated=True,
            when_passed=True,
            input_values={"budget": 30000, "immediate": 2},
            output_key="min_supplier_quotes",
            output_value_before=None,
            output_value_after=2,
            skipped=False,
        )
        sl = SupplierLog(
            supplier_id="SUP-001",
            supplier_name="Round Trip Corp",
            category_l2="Laptops",
            pricing_resolved={"unit_price": 980.0, "currency": "EUR"},
            action_logs=[entry],
            final_state={"rank": 7500.0, "cost_total": 4900.0},
            final_cost_rank_score=67.5,
            final_reputation_score=72.0,
            final_compliance_score=None,
            final_normalized_rank=0.512,
            excluded=False,
            exclusion_reason=None,
        )
        return RequestExecutionLog(
            request_id="REQ-RT-001",
            timestamp="2026-03-19T10:00:00+00:00",
            global_context_snapshot={"quantity": 5, "budget": 30000},
            supplier_logs=[sl],
            global_action_logs=[],
        )

    def test_save_log_creates_json(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "test_log")
        save_log(log, path)
        assert (tmp_path / "test_log.json").exists()
        assert not (tmp_path / "test_log.txt").exists()

    def test_json_round_trip_request_id(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        assert payload["request_id"] == "REQ-RT-001"

    def test_json_round_trip_supplier_id(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        assert payload["supplier_logs"][0]["supplier_id"] == "SUP-001"

    def test_json_round_trip_rule_id(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        entry = payload["supplier_logs"][0]["action_logs"][0]
        assert entry["rule_id"] == "AT-001"

    def test_json_round_trip_action_tuple_as_list(self, tmp_path: pathlib.Path) -> None:
        """action_tuple is serialised as a JSON list (tuples → lists in JSON)."""
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        entry = payload["supplier_logs"][0]["action_logs"][0]
        assert isinstance(entry["action_tuple"], list)
        assert entry["action_tuple"][0] == "ALI"

    def test_json_round_trip_input_values(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        entry = payload["supplier_logs"][0]["action_logs"][0]
        assert entry["input_values"]["budget"] == 30000

    def test_json_round_trip_skipped_false(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        entry = payload["supplier_logs"][0]["action_logs"][0]
        assert entry["skipped"] is False

    def test_json_round_trip_global_context(self, tmp_path: pathlib.Path) -> None:
        log = self._build_minimal_log()
        path = str(tmp_path / "log")
        save_log(log, path)

        with open(f"{path}.json", encoding="utf-8") as fh:
            payload = json.load(fh)

        assert payload["global_context_snapshot"]["quantity"] == 5



# ===========================================================================
# 6. Integration: run_procurement_evaluation returns log
# ===========================================================================

class TestRunProcurementEvaluationLogging:
    def test_returns_tuple_of_dict_and_log(self) -> None:
        s = _make_supplier()
        outcome, log = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        assert isinstance(outcome, dict)
        assert isinstance(log, RequestExecutionLog)

    def test_log_has_one_supplier_log_per_included_supplier(self) -> None:
        s1 = _make_supplier(supplier_id="SUP-A")
        s2 = _make_supplier(supplier_id="SUP-B")
        _, log = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s1, s2], FIX_IN_KEYS
        )
        included = [sl for sl in log.supplier_logs if not sl.excluded]
        assert len(included) == 2

    def test_excluded_supplier_appears_in_log_with_reason(self) -> None:
        included = _make_supplier(supplier_id="SUP-OK", is_restricted=False)
        excluded = _make_supplier(supplier_id="SUP-BLOCKED", is_restricted=True)
        _, log = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [included, excluded], FIX_IN_KEYS
        )
        exc_logs = [sl for sl in log.supplier_logs if sl.excluded]
        assert len(exc_logs) == 1
        assert exc_logs[0].supplier_id == "SUP-BLOCKED"
        assert exc_logs[0].exclusion_reason is not None
        assert "restricted" in exc_logs[0].exclusion_reason.lower()

    def test_log_request_id_matches_request(self) -> None:
        request = dict(BASE_REQUEST)
        request["request_id"] = "REQ-XYZ-999"
        s = _make_supplier()
        _, log = run_procurement_evaluation(
            request, SCHEMA, [], [s], FIX_IN_KEYS
        )
        assert log.request_id == "REQ-XYZ-999"

    def test_log_global_context_snapshot_matches(self) -> None:
        s = _make_supplier()
        _, log = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, [], [s], FIX_IN_KEYS
        )
        assert log.global_context_snapshot["delivery_country"] == "DE"
        assert log.global_context_snapshot["quantity"] == 5

    def test_attribution_passed_through_to_log_entries(self) -> None:
        """Actions with attribution: rule_id must appear in log entries."""
        actions = [("ALI", "_", "3", "=", "min_supplier_quotes")]
        attribution = {
            0: {"rule_id": "AT-007", "rule_description": "Three quotes required"},
        }
        s = _make_supplier()
        _, log = run_procurement_evaluation(
            BASE_REQUEST, SCHEMA, actions, [s], FIX_IN_KEYS,
            attribution=attribution,
        )
        included = [sl for sl in log.supplier_logs if not sl.excluded]
        assert len(included) == 1
        # Find the entry with our rule
        entries_with_rule = [
            e for e in included[0].action_logs if e.rule_id == "AT-007"
        ]
        assert len(entries_with_rule) == 1
        assert entries_with_rule[0].rule_description == "Three quotes required"
