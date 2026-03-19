"""
ChainIQ Slack Bot — Socket Mode (no Azure, no ngrok needed).
Run with: python bot_slack.py
"""
import json
import os

from dotenv import load_dotenv
load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from database import SessionLocal
import models

app = App(token=os.environ["SLACK_BOT_TOKEN"])

_APP_URL = os.environ.get("CHAINIQ_APP_URL", "http://localhost:3000")
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

        if low.startswith("procure "):
            _handle_new_request(say, text[8:].strip(), user, db)
        elif low.startswith("clarify "):
            parts = text[8:].strip().split(" ", 1)
            if len(parts) == 2:
                _handle_clarify(say, parts[0].upper(), parts[1], user, db)
            else:
                say("Usage: `clarify REQ-XXXXX <your clarification>`")
        elif low == "status":
            _handle_status(say, user, db)
        else:
            _send_help(say)
    finally:
        db.close()


# ── Handlers ───────────────────────────────────────────────────────────────────

def _handle_new_request(say, text: str, user: models.User, db):
    if not text:
        say("Please describe what you need after `procure`.")
        return

    req = models.Request(requester_id=user.id, plain_text=text, status="new")
    db.add(req)
    db.flush()  # assigns req.id before the AuditEntry references it
    db.add(models.AuditEntry(
        request_id=req.id, actor_id=user.id,
        action="submitted", notes="Submitted via Slack",
    ))
    db.commit()
    db.refresh(req)

    snippet = text[:200] + ("…" if len(text) > 200 else "")
    say(
        f"📋 *Request `{req.id}` submitted!*\n\n"
        f"> {snippet}\n\n"
        f"The AI pipeline will evaluate your request. "
        f"You'll receive a DM when action is required or a decision is made.\n"
        f"Track it: {_APP_URL}/dashboard/analysis?id={req.id}"
    )


def _handle_clarify(say, request_id: str, message: str, user: models.User, db):
    req = db.query(models.Request).filter(models.Request.id == request_id).first()
    if not req:
        say(f"❌ Request `{request_id}` not found.")
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
    req.status = "new"
    db.add(models.AuditEntry(
        request_id=req.id, actor_id=user.id,
        action="clarified", notes=f"Via Slack: {message}",
    ))
    db.commit()
    say(f"✅ Clarification recorded for `{request_id}`.")

    db.refresh(req)
    if req.requester and req.requester.slack_user_id and req.requester.slack_user_id != user.slack_user_id:
        from notifications import send_slack_dm
        send_slack_dm(
            req.requester.slack_user_id,
            f"💬 A clarification was added to your request `{request_id}`:\n> {message}",
        )


def _handle_status(say, user: models.User, db):
    reqs = (
        db.query(models.Request)
        .filter(models.Request.requester_id == user.id)
        .order_by(models.Request.created_at.desc())
        .limit(5).all()
    )
    if not reqs:
        say("You have no requests yet. Type `procure <what you need>` to submit one.")
        return

    lines = ["*Your 5 most recent requests:*\n"]
    for r in reqs:
        icon = _STATUS_ICON.get(r.status, "🔵")
        snippet = r.plain_text[:80] + ("…" if len(r.plain_text) > 80 else "")
        lines.append(f"{icon} `{r.id}` — {r.status.upper()}\n    _{snippet}_")
    say("\n\n".join(lines))


def _send_help(say):
    say(
        "👋 *Welcome to ChainIQ Procurement Bot!*\n\n"
        "• `procure <describe what you need>` — submit a new procurement request\n"
        "• `clarify REQ-XXXXX <your answer>` — respond to a clarification request\n"
        "• `status` — see your 5 most recent requests\n\n"
        "_You'll receive DMs here when escalations need your input or when a decision is made._"
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
