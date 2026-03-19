"""
ChainIQ Slack Bot — Socket Mode (no Azure, no ngrok needed).
Run with: python bot_slack.py

Commands (DM only):
  need / procure / order / buy / request / get  <text>  — submit new request
  status                                                 — list your 5 recent requests
  clarify <request-id> <message>                         — respond to a clarification
  help                                                   — show this message
"""
import json
import os

from dotenv import load_dotenv
load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from database import SessionLocal
import models
from notifications import _build_evaluation_summary

app = App(token=os.environ["SLACK_BOT_TOKEN"])

_APP_URL = os.environ.get("CHAINIQ_APP_URL", "http://localhost:3000")
_PROCURE_KEYWORDS = ["procure", "order", "buy", "request", "need", "get", "/need"]

_STATUS_ICON = {
    "new": "🔵", "escalated": "🟡", "pending_review": "🟠",
    "reviewed": "🔶", "approved": "✅", "rejected": "❌", "withdrawn": "⬜",
}


def _db():
    return SessionLocal()


# ── Message handler ────────────────────────────────────────────────────────────

@app.event("message")
def handle_message(event, say, client):
    # Only handle DMs (channel_type == "im"), ignore bot messages
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return

    slack_uid = event.get("user")
    text = (event.get("text") or "").strip()
    low = text.lower()

    db = _db()
    try:
        user = db.query(models.User).filter(models.User.slack_user_id == slack_uid).first()

        if not user:
            say(
                "👋 Your Slack account isn't linked to ChainIQ yet.\n"
                "Please ask your admin to add your Slack user ID to your ChainIQ profile.\n"
                f"Your Slack user ID is: `{slack_uid}`"
            )
            return

        _kw = next((k for k in _PROCURE_KEYWORDS if low.startswith(k + " ")), None)
        if _kw:
            _handle_new_request(say, text[len(_kw):].strip(), user, db)
        elif low.startswith("clarify "):
            parts = text[8:].strip().split(" ", 1)
            if len(parts) == 2:
                _handle_clarify(say, parts[0], parts[1], user, db)
            else:
                say("Usage: `clarify <request-id> <your clarification>`")
        elif low == "status":
            _handle_status(say, user, db)
        elif low in ("help", "/help"):
            _send_help(say)
        else:
            _send_help(say)
    finally:
        db.close()


# ── Handlers ───────────────────────────────────────────────────────────────────

def _handle_new_request(say, text: str, user: models.User, db):
    if not text:
        say("Please describe what you need after the keyword.\nExample: `need 50 laptops for the Berlin office by end of month`")
        return

    # Create the request record
    req = models.Request(
        requester_id=user.id,
        plain_text=text,
        status="new",
        business_unit=user.business_unit,
        country=user.country,
        site=user.site,
    )
    db.add(req)
    db.flush()
    db.add(models.AuditEntry(
        request_id=req.id,
        actor_id=user.id,
        action="submitted",
        notes="Submitted via Slack",
    ))
    db.commit()
    db.refresh(req)

    snippet = text[:200] + ("…" if len(text) > 200 else "")
    say(
        f"📋 *Request received!*\n\n"
        f"> {snippet}\n\n"
        f"⏳ Running AI evaluation — extracting fields, ranking suppliers, checking compliance…"
    )

    # Run full evaluation pipeline
    try:
        from services.evaluation import enrich_and_evaluate
        enrich_and_evaluate(req, db)
        db.refresh(req)
        say(_build_evaluation_summary(req))
    except Exception as e:
        say(
            f"⚠️ Evaluation could not complete: `{e}`\n"
            f"Your request was saved as `{req.id[:8]}…` and can be reviewed manually.\n"
            f"🔗 {_APP_URL}/dashboard/transparency?id={req.id}"
        )


def _handle_clarify(say, request_id: str, message: str, user: models.User, db):
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        say(f"❌ Request `{request_id}` not found.")
        return
    if req.requester_id != user.id:
        say(f"❌ You can only clarify your own requests.")
        return

    db.add(models.Clarification(
        request_id=req.id,
        submitted_fields=json.dumps({"clarification": message}),
    ))
    db.query(models.Escalation).filter(
        models.Escalation.request_id == req.id,
        models.Escalation.type == "requester_clarification",
        models.Escalation.status == "pending",
    ).update({"status": "resolved"})
    req.status = "pending_review"
    db.add(models.AuditEntry(
        request_id=req.id,
        actor_id=user.id,
        action="clarified",
        notes=f"Via Slack: {message}",
    ))
    db.commit()
    say(f"✅ Clarification recorded for `{request_id}`. The request is back in review.")

    # Re-run evaluation with the new clarification appended
    try:
        db.refresh(req)
        req.plain_text = req.plain_text + f"\n\n[Clarification: {message}]"
        from services.evaluation import enrich_and_evaluate
        enrich_and_evaluate(req, db)
        db.refresh(req)
        say("🔄 *Re-evaluated with your clarification:*\n\n" + _build_evaluation_summary(req))
    except Exception:
        pass  # Clarification is already saved; re-eval failure is non-critical

    # Notify the reviewer if there's a target user
    db.refresh(req)
    pending_escs = db.query(models.Escalation).filter(
        models.Escalation.request_id == req.id,
        models.Escalation.status == "pending",
    ).all()
    for esc in pending_escs:
        if esc.target_user and esc.target_user.slack_user_id and esc.target_user.slack_user_id != user.slack_user_id:
            from notifications import send_slack_dm
            send_slack_dm(
                esc.target_user.slack_user_id,
                f"💬 Requester added a clarification to `{request_id}`:\n> {message}\n"
                f"🔗 {_APP_URL}/dashboard/transparency?id={req.id}",
            )


def _handle_status(say, user: models.User, db):
    reqs = (
        db.query(models.Request)
        .filter(models.Request.requester_id == user.id)
        .order_by(models.Request.created_at.desc())
        .limit(5).all()
    )
    if not reqs:
        say("You have no requests yet.\nTry: `need <what you need>` to submit one.")
        return

    lines = ["*Your 5 most recent requests:*\n"]
    for r in reqs:
        icon = _STATUS_ICON.get(r.status, "🔵")
        snippet = (r.title or r.plain_text)[:80]
        if len(r.plain_text) > 80:
            snippet += "…"
        cat = f" · {r.category_l1}" if r.category_l1 else ""
        budget = f" · {r.currency or 'EUR'} {r.budget_amount:,.0f}" if r.budget_amount else ""
        lines.append(
            f"{icon} `{r.id[:8]}…` — *{r.status.replace('_', ' ').upper()}*{cat}{budget}\n"
            f"    _{snippet}_\n"
            f"    🔗 {_APP_URL}/dashboard/transparency?id={r.id}"
        )
    say("\n\n".join(lines))


def _send_help(say):
    keywords = " · ".join(f"`{k}`" for k in _PROCURE_KEYWORDS)
    say(
        "👋 *Welcome to ChainIQ Procurement Bot!*\n\n"
        f"• {keywords} `<describe what you need>` — submit a new procurement request\n"
        "• `clarify <request-id> <your answer>` — respond to a clarification request\n"
        "• `status` — see your 5 most recent requests with supplier rankings\n"
        "• `help` — show this message\n\n"
        "_You'll receive DMs here when:_\n"
        "  • Evaluation completes with ranked suppliers\n"
        "  • A clarification is needed from you\n"
        "  • Your request is approved or rejected"
    )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not app_token or app_token.startswith("xapp-your"):
        print("❌ SLACK_APP_TOKEN not set in .env")
        exit(1)
    print("✅ ChainIQ Slack Bot starting (Socket Mode)...")
    handler = SocketModeHandler(app, app_token)
    handler.start()
