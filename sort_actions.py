"""
Topological sorting of procurement action tuples by data dependencies.

Action tuple format:
    (TYPE, in_param1, in_param2_or_immediate, operator, out_param [, WHEN condition])

- TYPE: one of AL, ALI, OSLM, SRM
- in_param1: dict key string, or '_' if unused
- in_param2_or_immediate: dict key (AL/OSLM/SRM) or literal constant (ALI)
- operator: +, -, *, /, =, AND, OR, XOR, >=, <=, etc.
- out_param: dict key this action writes to
- WHEN condition (optional, index 5): boolean expression; key-like tokens are read deps
"""
from __future__ import annotations

import re

_WHEN_KEYWORDS = {"AND", "OR", "XOR", "NOT", "WHEN"}


def _parse_when_keys(when_str: str, fix_in_keys: set[str]) -> set[str]:
    """
    Extract dict-key references from a WHEN condition string.

    A token is a dict-key reference if it is:
    - not a number
    - not a quoted string literal
    - not a boolean/conditional keyword (AND, OR, XOR, NOT, WHEN)
    """
    # Replace operator symbols with whitespace so they don't stick to identifiers
    cleaned = re.sub(r"[>=<!+\-*/(),]+", " ", when_str)
    keys: set[str] = set()
    for token in cleaned.split():
        if token in _WHEN_KEYWORDS:
            continue
        try:
            float(token)
            continue
        except ValueError:
            pass
        if len(token) >= 2 and (
            (token.startswith('"') and token.endswith('"'))
            or (token.startswith("'") and token.endswith("'"))
        ):
            continue
        if token not in fix_in_keys:
            keys.add(token)
    return keys


def _get_reads(action: tuple, fix_in_keys: set[str]) -> set[str]:
    """
    Return the set of dict keys that this action reads, excluding fix_in keys
    (which are never produced by any action).
    """
    typ = action[0]
    in1 = action[1] if len(action) > 1 else "_"
    in2 = action[2] if len(action) > 2 else "_"

    reads: set[str] = set()

    if in1 != "_" and in1 not in fix_in_keys:
        reads.add(in1)

    # ALI: in_param2_or_immediate is a literal constant, never a key dependency
    if typ != "ALI" and in2 != "_" and in2 not in fix_in_keys:
        reads.add(in2)

    # WHEN condition at index 5
    if len(action) > 5:
        reads.update(_parse_when_keys(str(action[5]), fix_in_keys))

    return reads


def _get_write(action: tuple) -> str | None:
    """Return the dict key this action writes, or None."""
    out = action[4] if len(action) > 4 else None
    return out if out and out != "_" else None


def sort_actions(
    actions: list[tuple], fix_in_keys: set[str]
) -> tuple[list[tuple], bool]:
    """
    Topologically sort *actions* by data dependencies using DFS.

    An edge A→B exists when A writes a key that B reads (via in_param1,
    in_param2 for non-ALI types, or any key token in the WHEN clause).
    fix_in keys are excluded from edge creation because no action writes them.

    Back edges (which form cycles) are identified and skipped; if any are
    found, *is_low_confidence* is set to True.

    Returns:
        (sorted_actions, is_low_confidence)
    """
    n = len(actions)
    if n == 0:
        return [], False

    # Map each written key to its producer action index
    writes: dict[str, int] = {}
    for i, action in enumerate(actions):
        key = _get_write(action)
        if key is not None:
            writes[key] = i

    # Build adjacency list: adj[i] = consumers that must come after i
    adj: list[list[int]] = [[] for _ in range(n)]
    for j, action in enumerate(actions):
        for key in _get_reads(action, fix_in_keys):
            if key in writes:
                producer = writes[key]
                if producer != j:
                    adj[producer].append(j)

    # DFS topological sort
    UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
    state = [UNVISITED] * n
    post_order: list[int] = []
    is_low_confidence = False

    def dfs(u: int) -> None:
        nonlocal is_low_confidence
        state[u] = IN_PROGRESS
        for v in adj[u]:
            if state[v] == IN_PROGRESS:
                # Back edge → cycle; skip to remove it from the ordering
                is_low_confidence = True
            elif state[v] == UNVISITED:
                dfs(v)
        state[u] = DONE
        post_order.append(u)

    for i in range(n):
        if state[i] == UNVISITED:
            dfs(i)

    sorted_actions = [actions[i] for i in reversed(post_order)]
    return sorted_actions, is_low_confidence
