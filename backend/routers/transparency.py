"""
Transparency endpoints:
  GET  /requests/{request_id}/execution-log  — serve execution log JSON (lru_cache)
  GET  /requests/{request_id}/ai-summary     — auto-generated plain-English summary (in-memory cache)
  POST /ai/chat                              — stream Azure OpenAI chat with execution log context
"""
import json
import os
import pathlib
from functools import lru_cache
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import get_current_user
from database import get_db
import models

router = APIRouter(tags=["transparency"])

USE_FILE_DATA = os.environ.get("USE_FILE_DATA", "false").lower() == "true"
_LOG_DIR = pathlib.Path(__file__).parent.parent.parent / "stores" / "execution_logs"


@lru_cache(maxsize=256)
def _load_log(log_id: str) -> str | None:
    """Return raw JSON text for the execution log, or None if not found. Cached per log_id."""
    path = _LOG_DIR / f"{log_id}.json"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@router.get("/requests/{request_id}/execution-log")
def get_execution_log(
    request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    if USE_FILE_DATA:
        # In file mode request_id IS the execution log id (REQ-000001 → REQ-000001.json)
        text = _load_log(request_id)
    else:
        req = db.query(models.Request).filter(models.Request.id == request_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        if not req.execution_log_id:
            raise HTTPException(status_code=404, detail="No execution log linked to this request")
        text = _load_log(req.execution_log_id)

    if text is None:
        raise HTTPException(status_code=404, detail="Execution log not found")

    return json.loads(text)


# ── AI Summary ────────────────────────────────────────────────────────────────

_summary_cache: dict[str, str] = {}

_SUMMARY_PROMPT = (
    "Summarise this procurement evaluation in exactly 3 bullet points.\n\n"
    "Rules you must follow:\n"
    "- Exactly 3 bullets, no more, no less\n"
    "- Each bullet is exactly ONE sentence — do not add a second sentence or a dash after it\n"
    "- Plain English only — no jargon, no raw numbers like 0.743\n"
    "- Bullet 1: which supplier was ranked first and the single main reason why\n"
    "- Bullet 2: one issue or escalation that needs human attention "
    "(or 'No issues — this can proceed automatically' if none)\n"
    "- Bullet 3: whether sign-off is required and from whom "
    "(or that it can proceed automatically)"
)


def _az_client():
    try:
        from openai import AzureOpenAI
    except ImportError:
        raise HTTPException(status_code=500, detail="openai package not installed — run: pip install openai")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key  = os.environ.get("AZURE_OPENAI_API_KEY",  "")
    if not endpoint or not api_key or "your-" in endpoint or "your-" in api_key:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI not configured — set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in backend/.env",
        )
    from openai import AzureOpenAI
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )


def _resolve_log_text(request_id: str, db) -> str:
    """Return raw log JSON text or raise 404."""
    if USE_FILE_DATA:
        text = _load_log(request_id)
    else:
        req = db.query(models.Request).filter(models.Request.id == request_id).first()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        if not req.execution_log_id:
            raise HTTPException(status_code=404, detail="No execution log linked to this request")
        text = _load_log(req.execution_log_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Execution log not found")
    return text


@router.get("/requests/{request_id}/ai-summary")
def get_ai_summary(
    request_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    if request_id in _summary_cache:
        return {"summary": _summary_cache[request_id]}

    text = _resolve_log_text(request_id, db)
    execution_log = json.loads(text)

    client = _az_client()
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        execution_log=json.dumps(execution_log, indent=2)
    )
    resp = client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": _SUMMARY_PROMPT},
        ],
        temperature=0.3,
        stream=False,
    )
    summary = resp.choices[0].message.content or ""
    _summary_cache[request_id] = summary
    return {"summary": summary}


# ── AI Chat ───────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    execution_log: dict


_SYSTEM_PROMPT_TEMPLATE = """You are a friendly procurement assistant helping a procurement professional understand why the system made specific decisions for their purchase request. Speak in plain, everyday business language — avoid technical jargon, JSON field names, and code terms.

How to answer:
- Use simple language a non-technical procurement manager would understand
- Explain decisions in terms of business impact: cost, risk, delivery time, compliance
- When referencing a rule, say what it means in plain English first, then mention the rule ID in parentheses — e.g. "Because the order is over €100,000, company policy requires at least 3 supplier quotes (rule AT-003)"
- Use bullet points and short paragraphs — never write walls of text
- If a supplier was ranked first, explain it like: "Bechtle came out on top because they offered the lowest total price and have delivered to Germany before"
- If something was escalated, explain it like: "This needs your manager's sign-off because the budget is higher than what can be approved automatically"
- Never show raw numbers like 0.7432 or field names like final_cost_rank_score — translate them into plain language
- Keep answers short: maximum 3 bullet points or 3 sentences — never more

Execution Log (for your reference only — do not quote raw field names or JSON in your answer):
{execution_log}"""


@router.post("/ai/chat")
def ai_chat(
    body: ChatRequest,
    current_user=Depends(get_current_user),
):
    client = _az_client()
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        execution_log=json.dumps(body.execution_log, indent=2)
    )

    def stream():
        try:
            resp = client.chat.completions.create(
                model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    *[m.model_dump() for m in body.messages],
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in resp:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield f"data: {json.dumps({'content': chunk.choices[0].delta.content})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
