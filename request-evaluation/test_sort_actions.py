"""
Tests for sort_actions — topological sort of procurement action tuples.
"""
import pytest
from sort_actions import sort_actions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def indices_of(sorted_actions: list[tuple], actions: list[tuple]) -> list[int]:
    """Return the original indices of sorted_actions within *actions*."""
    lookup = {id(a): i for i, a in enumerate(actions)}
    return [lookup[id(a)] for a in sorted_actions]


def comes_before(sorted_actions: list[tuple], a: tuple, b: tuple) -> bool:
    """True if *a* appears earlier than *b* in sorted_actions."""
    pos = {id(t): i for i, t in enumerate(sorted_actions)}
    return pos[id(a)] < pos[id(b)]


# ---------------------------------------------------------------------------
# 1. Linear chain
# ---------------------------------------------------------------------------

def test_linear_chain_already_ordered():
    """A→B→C in correct order: sort should preserve / confirm the order."""
    A = ("AL", "_", "_", "=", "x")
    B = ("AL", "x", "_", "=", "y")
    C = ("AL", "y", "_", "=", "z")
    actions = [A, B, C]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert comes_before(result, A, B)
    assert comes_before(result, B, C)


def test_linear_chain_reverse_input():
    """A→B→C fed in reverse (C,B,A): sort must reorder correctly."""
    A = ("AL", "_", "_", "=", "x")
    B = ("AL", "x", "_", "=", "y")
    C = ("AL", "y", "_", "=", "z")
    actions = [C, B, A]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert comes_before(result, A, B)
    assert comes_before(result, B, C)


def test_linear_chain_all_present():
    """All actions must appear in the result exactly once."""
    A = ("AL", "_", "_", "=", "p")
    B = ("AL", "p", "_", "=", "q")
    C = ("AL", "q", "_", "=", "r")
    actions = [B, C, A]

    result, _, _ = sort_actions(actions, fix_in_keys=set())

    assert len(result) == 3
    assert set(map(id, result)) == set(map(id, actions))


# ---------------------------------------------------------------------------
# 2. Independent actions (no dependencies)
# ---------------------------------------------------------------------------

def test_independent_actions_all_present():
    """Actions with no shared keys: any order is valid; all must be returned."""
    A = ("AL", "_", "_", "=", "x")
    B = ("AL", "_", "_", "=", "y")
    C = ("AL", "_", "_", "=", "z")
    actions = [A, B, C]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert len(result) == 3
    assert set(map(id, result)) == set(map(id, actions))


def test_independent_actions_no_spurious_edges():
    """Two actions writing different keys must not impose ordering on each other."""
    A = ("AL", "_", "_", "=", "a_out")
    B = ("AL", "_", "_", "=", "b_out")
    actions = [A, B]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 3. Cycle detection → low_confidence flag
# ---------------------------------------------------------------------------

def test_simple_two_node_cycle():
    """A reads y (written by B), B reads x (written by A) → mutual cycle."""
    A = ("AL", "y", "_", "=", "x")
    B = ("AL", "x", "_", "=", "y")
    actions = [A, B]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is True
    assert len(result) == 2
    assert set(map(id, result)) == set(map(id, actions))


def test_three_node_cycle():
    """A→B→C→A cycle should trigger low_confidence."""
    A = ("AL", "c_out", "_", "=", "a_out")
    B = ("AL", "a_out", "_", "=", "b_out")
    C = ("AL", "b_out", "_", "=", "c_out")
    actions = [A, B, C]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is True
    assert len(result) == 3


def test_cycle_with_extra_independent_action():
    """Cycle between two nodes; a third independent action must still appear."""
    A = ("AL", "y", "_", "=", "x")
    B = ("AL", "x", "_", "=", "y")
    C = ("AL", "_", "_", "=", "z")
    actions = [A, B, C]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is True
    assert len(result) == 3
    assert any(id(t) == id(C) for t in result)


# ---------------------------------------------------------------------------
# 4. WHEN clause dependencies
# ---------------------------------------------------------------------------

def test_when_clause_creates_dependency():
    """Action B's WHEN clause references a key written by A → A before B."""
    A = ("ALI", "_", 0, "=", "threshold")
    B = ("ALI", "amount", 100, ">=", "flag", "WHEN threshold = high")
    actions = [B, A]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert comes_before(result, A, B)


def test_when_clause_keywords_not_treated_as_keys():
    """AND, OR, XOR, NOT, WHEN tokens in the condition are not key references."""
    # Nothing writes 'AND', 'OR', etc., so no spurious edges should appear.
    A = ("ALI", "_", 0, "=", "flag", "WHEN x AND y OR z")
    B = ("AL", "_", "_", "=", "x")
    C = ("AL", "_", "_", "=", "y")
    D = ("AL", "_", "_", "=", "z")
    actions = [A, B, C, D]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    # A reads x, y, z — all written by B, C, D respectively
    assert comes_before(result, B, A)
    assert comes_before(result, C, A)
    assert comes_before(result, D, A)


def test_when_clause_numeric_literal_not_a_key():
    """Numeric literals in WHEN clauses must not be treated as key references."""
    # 25000 and 99999 are numbers — no action writes them, no edge created
    A = ("ALI", "amount", 1, ">=", "flag", "WHEN amount >= 25000 AND amount <= 99999")
    actions = [A]

    result, low, _ = sort_actions(actions, fix_in_keys={"amount"})

    assert low is False
    assert result == [A]


def test_when_clause_quoted_string_not_a_key():
    """Quoted tokens in WHEN clauses are string literals, not key references."""
    A = ("ALI", "_", 0, "=", "approved", 'WHEN status = "pending"')
    actions = [A]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert result == [A]


# ---------------------------------------------------------------------------
# 5. ALI: second parameter is an immediate, not a key dependency
# ---------------------------------------------------------------------------

def test_ali_immediate_not_treated_as_key():
    """For ALI, in_param2 is a literal — must not create an edge."""
    # If '42' were treated as a key, we'd look for an action writing '42'.
    # There is none, so this test verifies no spurious dependency is created.
    A = ("ALI", "_", 0, "=", "base")
    B = ("ALI", "base", 42, "+", "result")
    actions = [B, A]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    # B reads 'base' (written by A) via in_param1 — A must come first
    assert low is False
    assert comes_before(result, A, B)


def test_ali_immediate_string_not_a_key():
    """ALI with a string immediate must not generate a dependency on that string."""
    A = ("ALI", "_", 0, "=", "x")
    B = ("ALI", "x", "some_string_constant", "=", "y")
    actions = [B, A]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert comes_before(result, A, B)


def test_ali_vs_al_in_param2_difference():
    """AL treats in_param2 as a key; ALI does not."""
    # With AL: B reads both 'base' and 'factor'
    writer_base = ("AL", "_", "_", "=", "base")
    writer_factor = ("AL", "_", "_", "=", "factor")
    consumer_AL = ("AL", "base", "factor", "+", "total")
    actions_al = [consumer_AL, writer_base, writer_factor]

    result_al, low_al, _ = sort_actions(actions_al, fix_in_keys=set())
    assert low_al is False
    assert comes_before(result_al, writer_base, consumer_AL)
    assert comes_before(result_al, writer_factor, consumer_AL)

    # With ALI: 'factor' is an immediate — no dependency on writer_factor
    consumer_ALI = ("ALI", "base", "factor", "+", "total")
    actions_ali = [consumer_ALI, writer_base, writer_factor]

    result_ali, low_ali, _ = sort_actions(actions_ali, fix_in_keys=set())
    assert low_ali is False
    # base must come before consumer_ALI
    assert comes_before(result_ali, writer_base, consumer_ALI)
    # writer_factor has no forced ordering relative to consumer_ALI


# ---------------------------------------------------------------------------
# 6. fix_in keys must not create dependency edges
# ---------------------------------------------------------------------------

def test_fix_in_key_not_a_dependency():
    """A fix_in key read by two actions must not impose ordering between them."""
    A = ("AL", "invoice_amount", "_", "=", "x")
    B = ("AL", "invoice_amount", "_", "=", "y")
    fix_in = {"invoice_amount"}
    actions = [A, B]

    result, low, _ = sort_actions(actions, fix_in_keys=fix_in)

    assert low is False
    assert len(result) == 2
    # No ordering constraint — both orderings are valid


def test_fix_in_key_in_when_clause_not_a_dependency():
    """fix_in keys referenced in WHEN clauses must not create edges."""
    A = ("ALI", "_", 0, "=", "flag", "WHEN invoice_amount >= 1000")
    B = ("ALI", "_", 0, "=", "other")
    fix_in = {"invoice_amount"}
    actions = [A, B]

    result, low, _ = sort_actions(actions, fix_in_keys=fix_in)

    assert low is False
    assert len(result) == 2


def test_fix_in_key_mixed_with_non_fix_in():
    """Only non-fix_in reads create edges; fix_in reads are ignored."""
    writer = ("AL", "_", "_", "=", "computed")
    consumer = ("AL", "invoice_amount", "computed", "+", "adjusted")
    fix_in = {"invoice_amount"}
    actions = [consumer, writer]

    result, low, _ = sort_actions(actions, fix_in_keys=fix_in)

    assert low is False
    # 'computed' is not fix_in → edge from writer to consumer
    assert comes_before(result, writer, consumer)


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------

def test_empty_actions():
    result, low, _ = sort_actions([], fix_in_keys=set())
    assert result == []
    assert low is False


def test_single_action():
    A = ("AL", "_", "_", "=", "x")
    result, low, _ = sort_actions([A], fix_in_keys=set())
    assert result == [A]
    assert low is False


def test_all_types_accepted():
    """AL, ALI, OSLM, SRM all work; OSLM/SRM treat in_param2 as a key."""
    al   = ("AL",   "a", "b", "+", "c")
    ali  = ("ALI",  "c", 10,  "*", "d")
    oslm = ("OSLM", "d", "e", "=", "f")
    srm  = ("SRM",  "f", "_", "=", "rank")
    writer_a = ("AL", "_", "_", "=", "a")
    writer_b = ("AL", "_", "_", "=", "b")
    writer_e = ("AL", "_", "_", "=", "e")
    actions = [srm, oslm, ali, al, writer_a, writer_b, writer_e]

    result, low, _ = sort_actions(actions, fix_in_keys=set())

    assert low is False
    assert comes_before(result, writer_a, al)
    assert comes_before(result, writer_b, al)
    assert comes_before(result, al, ali)
    assert comes_before(result, ali, oslm)
    assert comes_before(result, writer_e, oslm)
    assert comes_before(result, oslm, srm)
