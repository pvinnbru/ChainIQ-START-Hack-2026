"""
api.py — FastAPI backend for the ChainIQ procurement automation system.

Run with:
    uvicorn api:app --reload

Endpoints
---------
GET /actions-store/{ruleset_id}
    Return the sorted actions list for the given ruleset, using the on-disk
    store when it is up to date or rebuilding it via the LLM pipeline when
    the data folder has changed.

    Path parameters:
        ruleset_id  One of: approval_thresholds | category_rules | escalation_rules

    Response (200):
        {
            "ruleset_id":        str,
            "data_hash":         str,
            "is_low_confidence": bool,
            "created_at":        str,   // ISO-8601
            "sorted_actions":    list,  // list of action arrays
            "cache_hit":         bool
        }

    Response (422) when ruleset_id is not a supported section in policies.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from actions_store import (
    SUPPORTED_RULESETS,
    get_or_build_actions_store,
)

app = FastAPI(title="ChainIQ Procurement API", version="0.1.0")

# Allow the Next.js dev server to call this API without CORS errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/actions-store/{ruleset_id}")
def get_actions_store(ruleset_id: str) -> dict[str, Any]:
    """
    Return (and if necessary rebuild) the sorted actions store for *ruleset_id*.

    The store is rebuilt automatically whenever the SHA-256 hash of the
    ``data/`` folder differs from the hash recorded in the persisted store,
    ensuring the returned actions always reflect the current data files.
    """
    if ruleset_id not in SUPPORTED_RULESETS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown ruleset '{ruleset_id}'. "
                f"Supported values: {sorted(SUPPORTED_RULESETS)}"
            ),
        )

    try:
        result = get_or_build_actions_store(ruleset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Convert tuples to lists for JSON serialisation
    result["sorted_actions"] = [list(a) for a in result["sorted_actions"]]
    return result
