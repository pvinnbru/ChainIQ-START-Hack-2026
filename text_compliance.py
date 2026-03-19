"""
text_compliance.py — Free-text request compliance check via LLM.

Procurement requests often contain explicit directives that the structured
rule pipeline cannot capture:

  "Use Dell Enterprise Europe with no exception."
  "Must be ISO 27001 certified — no deviations accepted."
  "Avoid any supplier headquartered outside Switzerland."

This module makes ONE LLM call per request (not per supplier) that:

  1. Reads the free-text request_text.
  2. Receives the current compliance_score and key attributes for every
     non-excluded supplier.
  3. Returns per supplier either:
       - A hard exclusion (excluded=true) with a reason — supplier is removed
         from the shortlist entirely, just as if a structured rule had excluded it.
       - A soft compliance score ∈ [0.0, 1.0] — multiplied onto normalized_rank.

Hard exclusion fires when the text contains clear, unambiguous directives:
  "accept Dell only", "no supplier headquartered outside Switzerland",
  "must be ISO 27001 certified".

Soft scoring is used for preferences and soft requirements that don't warrant
complete exclusion: "we prefer", "ideally", performance baselines, etc.

The compliance_score can only be LOWERED — the LLM cannot raise it above what
the structured rule pipeline already computed.

If the LLM call fails or produces unparseable output, existing scores are left
unchanged and no exclusions are applied (fail-open — no silent exclusions).
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        )
    return _client


_SYSTEM_PROMPT = """\
You are a procurement compliance analyst. A requester has submitted a free-text procurement request. Your job is to evaluate every candidate supplier against ALL requirements in that text and decide — for each one — whether they are EXCLUDED or ELIGIBLE.

---

## Decision tree (apply in order)

### Step 1 — Does the text contain a hard directive about supplier selection?

Hard directives are unambiguous statements that restrict which suppliers are acceptable. Trigger phrases include (but are not limited to):
  "use X only", "use X with no exception", "only X", "must be X", "exclusively X",
  "no other supplier", "accept only", "must use", "restricted to", "mandatory: X"

If YES and the supplier violates the directive → **excluded: true**.
If YES and the supplier satisfies the directive → **excluded: false, compliance_score: existing**.

Examples:
  Text: "Please use Dell Enterprise Europe with no exception."
    → Dell Enterprise Europe: excluded: false, compliance_score: existing
    → Every other supplier: excluded: true, reason: "Request mandates Dell Enterprise Europe exclusively ('with no exception')"

  Text: "Only ISO 27001 certified suppliers are acceptable."
    → Supplier known to be certified: excluded: false
    → Supplier not known to be certified: excluded: true, reason: "Request mandates ISO 27001 certification"

### Step 2 — Does the text contain soft preferences that are not met?

Soft preferences use hedged language: "prefer", "ideally", "where possible", "we would like".
These do NOT trigger exclusion. Assign a compliance_score < 1.0:
  - 0.80–0.95: minor preference not met
  - 0.50–0.79: moderate named preference not met (e.g. supplier is not the requested one, but may still be used)
  - 0.20–0.49: strong soft requirement not met (stated firmly, but without exclusion language)

### Step 3 — No relevant requirements

If the text only mentions budget, delivery dates, specifications, or other non-supplier-identity requirements → return each supplier's existing_compliance_score unchanged, excluded: false.

---

## Absolute rules

1. Never raise a supplier's compliance_score above their existing_compliance_score.
2. When excluded is true, set compliance_score: 0.0.
3. Always provide a specific exclusion_reason when excluding; quote the relevant phrase from the request text.
4. Do NOT exclude based on budget amount, delivery date, or product specification alone.

---

Return ONLY valid JSON (no markdown, no explanation) mapping supplier_id → object with keys:
  "excluded": true | false
  "exclusion_reason": string  (specific and non-empty when excluded is true; "" otherwise)
  "compliance_score": number 0.0–1.0

Example — "use Dell only":
{
  "SUP-DELL": {"excluded": false, "exclusion_reason": "", "compliance_score": 1.0},
  "SUP-HP":   {"excluded": true,  "exclusion_reason": "Request mandates Dell Enterprise Europe exclusively ('with no exception')", "compliance_score": 0.0},
  "SUP-BCH":  {"excluded": true,  "exclusion_reason": "Request mandates Dell Enterprise Europe exclusively ('with no exception')", "compliance_score": 0.0}
}
"""


def _supplier_snapshot(
    identity: dict[str, Any],
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact supplier summary for the LLM prompt."""
    return {
        "supplier_id":               identity.get("supplier_id", ""),
        "supplier_name":             identity.get("supplier_name", ""),
        "category_l2":               identity.get("category_l2", ""),
        "existing_compliance_score": round(float(final_state.get("compliance_score") or 1.0), 4),
        "preferred_supplier":        final_state.get("preferred_supplier"),
        "contract_status":           final_state.get("contract_status"),
        "data_residency_supported":  final_state.get("data_residency_supported"),
        "esg_score":                 final_state.get("esg_score"),
        "risk_score":                final_state.get("risk_score"),
    }


# ---------------------------------------------------------------------------
# Return type for one supplier's LLM verdict
# ---------------------------------------------------------------------------

class SupplierVerdict:
    __slots__ = ("excluded", "exclusion_reason", "compliance_score")

    def __init__(self, excluded: bool, exclusion_reason: str, compliance_score: float) -> None:
        self.excluded         = excluded
        self.exclusion_reason = exclusion_reason
        self.compliance_score = compliance_score


def _parse_verdict(raw: Any, existing_score: float) -> SupplierVerdict:
    """Parse one supplier's entry from the LLM JSON response."""
    if isinstance(raw, (int, float)):
        # Backward-compatible: LLM returned a bare number instead of an object
        score = max(0.0, min(1.0, float(raw)))
        return SupplierVerdict(excluded=False, exclusion_reason="", compliance_score=score)

    if not isinstance(raw, dict):
        return SupplierVerdict(excluded=False, exclusion_reason="", compliance_score=existing_score)

    excluded = bool(raw.get("excluded", False))
    reason   = str(raw.get("exclusion_reason", "")).strip()
    try:
        score = max(0.0, min(1.0, float(raw.get("compliance_score", existing_score))))
    except (TypeError, ValueError):
        score = existing_score

    if excluded and not reason:
        reason = "Excluded by text compliance check"
    if excluded:
        score = 0.0

    return SupplierVerdict(excluded=excluded, exclusion_reason=reason, compliance_score=score)


def apply_text_compliance(
    request_text: str,
    supplier_results: list[tuple[dict[str, Any], Any, dict[str, Any]]],
) -> dict[str, SupplierVerdict]:
    """
    Make one LLM call to check request_text against all surviving suppliers.

    Returns a dict mapping supplier_id → SupplierVerdict.
    On failure, returns {} (caller keeps existing scores, no exclusions).
    """
    if not request_text or not supplier_results:
        return {}

    snapshots = [
        _supplier_snapshot(identity, final_state)
        for identity, _, final_state in supplier_results
    ]

    user_message = (
        f"### Request Text\n{request_text}\n\n"
        f"### Candidate Suppliers\n```json\n{json.dumps(snapshots, indent=2)}\n```"
    )

    try:
        response = _get_client().chat.completions.create(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
    except Exception as exc:
        print(f"[text_compliance] LLM call failed — keeping existing scores. Error: {exc}")
        return {}

    # Build a lookup of existing scores for fallback
    existing: dict[str, float] = {
        identity.get("supplier_id", ""): float(final_state.get("compliance_score") or 1.0)
        for identity, _, final_state in supplier_results
    }

    results: dict[str, SupplierVerdict] = {}
    for sid, entry in parsed.items():
        sid = str(sid)
        verdict = _parse_verdict(entry, existing.get(sid, 1.0))
        # Enforce: compliance_score can only be lowered, not raised
        if not verdict.excluded:
            verdict.compliance_score = min(verdict.compliance_score, existing.get(sid, 1.0))
        results[sid] = verdict

    return results


def update_compliance_scores(
    request_text: str | None,
    supplier_results: list[tuple[dict[str, Any], Any, dict[str, Any]]],
) -> set[str]:
    """
    Apply text-based compliance decisions in-place to each supplier's final_state.

    For hard exclusions: sets final_state["excluded"] = True and
    final_state["text_exclusion_reason"] = reason.

    For soft scoring: updates final_state["compliance_score"] (only lowers),
    and records final_state["text_compliance_score"] for auditability.

    Returns
    -------
    set of supplier_ids that were hard-excluded by text compliance.
    Empty set if no text, LLM call failed, or no exclusions were issued.
    """
    if not request_text:
        return set()

    verdicts = apply_text_compliance(request_text, supplier_results)
    if not verdicts:
        return set()

    excluded_ids: set[str] = set()

    for identity, _, final_state in supplier_results:
        sid = identity.get("supplier_id", "")
        if sid not in verdicts:
            continue

        verdict = verdicts[sid]
        final_state["text_compliance_score"] = verdict.compliance_score

        if verdict.excluded:
            final_state["excluded"]              = True
            final_state["text_exclusion_reason"] = verdict.exclusion_reason
            final_state["compliance_score"]      = 0.0
            excluded_ids.add(sid)
        else:
            existing = float(final_state.get("compliance_score") or 1.0)
            final_state["compliance_score"] = min(existing, verdict.compliance_score)

    return excluded_ids
