"""
backend/services/evaluation.py

Wires llm_extractor.py (field + category extraction) and evaluate_request.py
(supplier ranking + compliance pipeline) into the FastAPI create_request flow.

Call:
    enrich_and_evaluate(req, db)

after the Request record has been flushed to the DB (so req.id exists).
The function is fully async-safe to call from a synchronous FastAPI route.
All errors are caught and logged — the request record is always preserved.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    import models

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# sys.path bootstrap — must run before any cross-directory imports
# ---------------------------------------------------------------------------

_paths_added = False
_PROJECT_ROOT = Path(__file__).parent.parent.parent   # repo root
_EVAL_DIR     = _PROJECT_ROOT / "request-evaluation"


def _ensure_paths() -> None:
    global _paths_added
    if _paths_added:
        return
    for p in (str(_EVAL_DIR), str(_PROJECT_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    _paths_added = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _needs_extraction(req) -> bool:
    """Return True if any key structured field is missing."""
    return any([
        not req.category_l1,
        req.budget_amount is None,
        req.quantity is None,
        not req.currency,
        not req.delivery_countries,
    ])


def _merge_extracted(req, extracted: dict) -> None:
    """Write LLM-extracted fields onto req only where currently empty/None."""
    mapping = {
        "currency":                    "currency",
        "budget_amount":               "budget_amount",
        "quantity":                    "quantity",
        "unit_of_measure":             "unit_of_measure",
        "required_by_date":            "required_by_date",
        "preferred_supplier_mentioned":"preferred_supplier_mentioned",
        "incumbent_supplier":          "incumbent_supplier",
    }
    for src_key, dst_attr in mapping.items():
        val = extracted.get(src_key)
        if val is not None and not getattr(req, dst_attr, None):
            setattr(req, dst_attr, val)

    # delivery_countries: store as JSON string "[\"DE\"]"
    countries = extracted.get("delivery_countries")
    if countries and not req.delivery_countries:
        if isinstance(countries, list):
            req.delivery_countries = json.dumps(countries)
        else:
            req.delivery_countries = json.dumps([countries])


def _compute_days_until_required(date_str: str | None) -> int:
    """Convert required_by_date (YYYY-MM-DD) to days from today; default 30."""
    if not date_str:
        return 30
    try:
        target = date.fromisoformat(str(date_str))
        return max(0, (target - date.today()).days)
    except (ValueError, TypeError):
        return 30


# Categories whose pricing tiers only exist for EU/Americas/APAC/MEA regions.
# CH has its own pricing region but only covers cloud and professional services —
# hardware suppliers (IT hardware, Facilities) only have EU-region pricing tiers.
# For these categories, CH → DE so COUNTRY_TO_REGION resolves to EU.
_CH_HARDWARE_FALLBACK_CATEGORIES = {"IT", "Facilities"}


def _get_delivery_country(req) -> str:
    """Return first delivery country from JSON array, or fallback to req.country / 'DE'.

    Special case: Switzerland (CH) maps to its own pricing region in the pipeline,
    which only contains cloud and professional-services tiers. For hardware categories
    (IT, Facilities) we substitute DE so the EU pricing tiers are used instead.
    """
    if req.delivery_countries:
        try:
            countries = json.loads(req.delivery_countries)
            if countries and isinstance(countries, list):
                country = countries[0]
            else:
                country = req.delivery_countries
        except (json.JSONDecodeError, TypeError):
            country = req.delivery_countries
    else:
        country = req.country or "DE"

    if country == "CH" and (req.category_l1 or "") in _CH_HARDWARE_FALLBACK_CATEGORIES:
        return "DE"
    return country


# ---------------------------------------------------------------------------
# Escalation flag → DB Escalation mapping
# ---------------------------------------------------------------------------

ESCALATION_TYPE_MAPPING = {
    "escalate_to_requester":               "requester_clarification",
    "escalate_to_procurement_manager":     "procurement_manager",
    "escalate_to_head_of_category":        "category_head",
    "escalate_to_head_of_strategic_sourcing": "category_head",
    "escalate_to_cpo":                     "category_head",
    "escalate_to_security_compliance":     "compliance",
    "escalate_to_regional_compliance":     "compliance",
    "escalate_to_sourcing_excellence":     "procurement_manager",
    "escalate_to_marketing_governance":    "compliance",
}


# ---------------------------------------------------------------------------
# Main exported function
# ---------------------------------------------------------------------------

def enrich_and_evaluate(req, db) -> None:
    """
    1. Run llm_extractor on req.plain_text if structured fields are missing.
       Capture `text_output` (English translation) for use in evaluation.
    2. Determine category if still unknown.
    3. Build evaluate_request input dict — using the English text_output as
       request_text, not the raw (possibly non-English) plain_text.
    4. Run evaluate_request, persist ai_output + escalations.

    On any error: logs exception, rolls back, returns silently.
    """
    _ensure_paths()

    try:
        # ------------------------------------------------------------------
        # Step 1 — LLM field extraction
        # ------------------------------------------------------------------
        english_text: str = req.plain_text  # fallback if extraction skipped

        if _needs_extraction(req):
            logger.info("Running LLM field extraction for request %s", req.id)
            from llm_extractor import extract_fields_with_llm
            extracted = extract_fields_with_llm(req.plain_text)

            if "llm_error" not in extracted:
                # text_output is the English translation — use it downstream
                english_text = extracted.get("text_output") or req.plain_text
                _merge_extracted(req, extracted)
                db.flush()
            else:
                logger.warning("LLM extraction error for %s: %s", req.id, extracted.get("llm_error"))

        # ------------------------------------------------------------------
        # Step 2 — Category detection (if still missing)
        # ------------------------------------------------------------------
        if not req.category_l1:
            logger.info("Determining category for request %s", req.id)
            from llm_extractor import determine_category_with_llm

            cats_path = _PROJECT_ROOT / "data" / "categories.csv"
            cats_text = ""
            if cats_path.exists():
                with open(cats_path, encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            cats_text += ",".join(parts[:3]) + "\n"

            cat = determine_category_with_llm(req.plain_text, cats_text)
            if "llm_error_category" not in cat:
                req.category_l1 = cat.get("category_l1")
                req.category_l2 = cat.get("category_l2")
                db.flush()

        # ------------------------------------------------------------------
        # Step 3 — Build evaluate_request input dict
        # NOTE: request_text uses the English-translated text from the LLM
        #       extractor (text_output), not the raw plain_text, so the text
        #       compliance module always receives English content.
        # ------------------------------------------------------------------
        input_dict = {
            "request_id":                  req.id,
            "category_l1":                 req.category_l1 or "IT",
            "category_l2":                 req.category_l2 or "",
            "budget":                      req.budget_amount or 0,
            "currency":                    req.currency or "EUR",
            "quantity":                    req.quantity or 1,
            "amount_unit":                 req.unit_of_measure or "units",
            "delivery_country":            _get_delivery_country(req),
            "days_until_required":         _compute_days_until_required(req.required_by_date),
            "preferred_supplier_mentioned":req.preferred_supplier_mentioned,
            "incumbent_supplier":          req.incumbent_supplier,
            "data_residency_constraint":   bool(req.data_residency_constraint),
            "esg_requirement":             bool(req.esg_requirement),
            "request_text":                english_text,   # ← English translation
        }

        logger.info(
            "Evaluating request %s | category=%s/%s | budget=%s %s | qty=%s | country=%s | days=%s",
            req.id,
            input_dict["category_l1"], input_dict["category_l2"],
            input_dict["budget"], input_dict["currency"],
            input_dict["quantity"],
            input_dict["delivery_country"],
            input_dict["days_until_required"],
        )

        # ------------------------------------------------------------------
        # Step 4 — Run evaluation
        # ------------------------------------------------------------------
        from evaluate_request import evaluate_request as _run_eval
        result_str = _run_eval(json.dumps(input_dict))
        result = json.loads(result_str)
        
        # Write raw evaluate_request output to debug file for inspection
        debug_dir = _PROJECT_ROOT / "stores" / "debug_outputs"
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / f"{req.id}.json"
        with open(debug_path, "w", encoding="utf-8") as _fh:
            _fh.write(result_str)
        logger.info("Raw evaluate_request output written to %s", debug_path)

        if result.get("status") != "ok":
            logger.warning(
                "Evaluation returned non-ok status for %s: %s",
                req.id, result.get("error"),
            )
            return

        # ------------------------------------------------------------------
        # Step 5 — Persist ai_output on the request record
        # ------------------------------------------------------------------
        global_outputs    = result.get("global_outputs", {})
        ranked_suppliers  = result.get("ranked_suppliers", [])
        # IMPORTANT: evaluate_request.py outputs the key as "escalation",
        # NOT "escalation_assessment".  Try both for safety.
        escalation_assess = result.get("escalation") or result.get("escalation_assessment")
        flag_assess       = result.get("flag_assessment")

        logger.info(
            "[ESCALATION DEBUG] result keys: %s", list(result.keys())
        )
        logger.info(
            "[ESCALATION DEBUG] escalation_assess type=%s, value=%s",
            type(escalation_assess).__name__,
            json.dumps(escalation_assess, default=str)[:1000] if escalation_assess else "None",
        )

        req.ai_output = json.dumps({
            "global_outputs":        global_outputs,
            "ranked_suppliers":      ranked_suppliers,
            "escalation_assessment": escalation_assess,
            "flag_assessment":       flag_assess,
        })
        req.execution_log_id = req.id
        db.flush()

        # ------------------------------------------------------------------
        # Step 6 — Create Escalation DB records from escalation_assessment
        # ------------------------------------------------------------------
        import models

        escalations_created: list[tuple] = []  # (Escalation, target_user | None)
        seen_types: set[str] = set()

        from routers.escalations import _route_escalation

        needs_esc = False
        esc_records: list = []

        if escalation_assess:
            needs_esc = escalation_assess.get("needs_escalation", False)
            esc_records = escalation_assess.get("records", [])
            logger.info(
                "[ESCALATION DEBUG] needs_escalation=%s, num_records=%d",
                needs_esc, len(esc_records),
            )
        else:
            logger.info("[ESCALATION DEBUG] escalation_assess is None/empty — no escalations from AI")

        if needs_esc and esc_records:
            for record in esc_records:
                person = record.get("person_to_escalate_to", "")
                p_norm = person.lower().strip()
                # Strip common prefixes
                for prefix in ("escalate_to_", "escalate to "):
                    if p_norm.startswith(prefix):
                        p_norm = p_norm[len(prefix):]

                logger.info(
                    "[ESCALATION DEBUG] Processing record: person=%r → p_norm=%r",
                    person, p_norm,
                )

                if p_norm in ("requester",):
                    esc_type = "requester_clarification"
                elif p_norm in ("procurement_manager", "procurement manager",
                                "buyer", "sourcing_excellence", "sourcing excellence",
                                "sourcing excellence lead"):
                    esc_type = "procurement_manager"
                elif p_norm in ("category_head", "category head",
                                "cpo", "head_of_strategic_sourcing",
                                "head of strategic sourcing",
                                "head_of_category", "head of category"):
                    esc_type = "category_head"
                elif "compliance" in p_norm or "governance" in p_norm:
                    esc_type = "compliance"
                else:
                    # Default fallback — still create an escalation
                    esc_type = "procurement_manager"
                    logger.warning(
                        "[ESCALATION] Unknown person %r — defaulting to procurement_manager",
                        person,
                    )

                if esc_type in seen_types:
                    logger.info("[ESCALATION DEBUG] Skipping duplicate esc_type=%s", esc_type)
                    continue
                seen_types.add(esc_type)

                message = record.get("reason_for_escalation") or "Review required"
                task_desc = record.get("task_for_escalation")
                if task_desc:
                    message += f" (Action: {task_desc})"

                esc_target_user = _route_escalation(esc_type, req, db)
                target_user_id = esc_target_user.id if esc_target_user else None
                logger.info(
                    "[ESCALATION] Creating DB record: type=%s, target_user=%s (id=%s)",
                    esc_type,
                    esc_target_user.name if esc_target_user else "None",
                    target_user_id,
                )

                esc = models.Escalation(
                    request_id=req.id,
                    type=esc_type,
                    status="pending",
                    target_user_id=target_user_id,
                    message=message[:500],
                )
                db.add(esc)
                escalations_created.append((esc, esc_target_user))

        if escalations_created:
            req.status = "escalated"
            logger.info(
                "✅ Created %d escalation(s) for request %s: %s",
                len(escalations_created), req.id, [e.type for e, _ in escalations_created],
            )
        else:
            logger.info(
                "ℹ️  No escalations created for request %s (needs_esc=%s, records=%d)",
                req.id, needs_esc, len(esc_records),
            )

        db.commit()
        db.refresh(req)
        logger.info("Evaluation complete for request %s — %d supplier(s) ranked", req.id, len(ranked_suppliers))

        # Notify escalation target users via Slack DM
        try:
            from notifications import notify_escalation
            for esc, target_user in escalations_created:
                if target_user:
                    notify_escalation(esc, req, target_user)
        except Exception:
            logger.warning("Failed to send escalation Slack notifications for %s", req.id)

    except Exception:
        logger.exception("enrich_and_evaluate failed for request %s — rolling back", getattr(req, "id", "?"))
        try:
            db.rollback()
        except Exception:
            pass
