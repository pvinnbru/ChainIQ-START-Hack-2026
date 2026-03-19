"""
ChainIQ Procurement Bot — Azure Bot Framework handler.
Users DM this bot in Slack to submit requests, provide clarifications, and check status.
"""
import json
import os

from botbuilder.core import ActivityHandler, TurnContext
from database import SessionLocal
import models


def _db():
    return SessionLocal()


_STATUS_ICON = {
    "new": "🔵",
    "escalated": "🟡",
    "pending_review": "🟠",
    "reviewed": "🔶",
    "approved": "✅",
    "rejected": "❌",
    "withdrawn": "⬜",
}

_APP_URL = os.environ.get("CHAINIQ_APP_URL", "http://localhost:3000")


class ProcureBot(ActivityHandler):
    # ── Incoming message ──────────────────────────────────────────────────────

    async def on_message_activity(self, turn_context: TurnContext):
        slack_uid = turn_context.activity.from_property.id
        text = (turn_context.activity.text or "").strip()
        low = text.lower()

        db = _db()
        try:
            user = db.query(models.User).filter(models.User.slack_user_id == slack_uid).first()

            if not user:
                await turn_context.send_activity(
                    "👋 Your Slack account isn't linked to ChainIQ yet.\n"
                    "Please ask your admin to add your Slack user ID to your ChainIQ profile."
                )
                return

            if low.startswith("procure "):
                await self._handle_new_request(turn_context, text[8:].strip(), user, db)
            elif low.startswith("clarify "):
                parts = text[8:].strip().split(" ", 1)
                if len(parts) == 2:
                    await self._handle_clarify(turn_context, parts[0].upper(), parts[1], user, db)
                else:
                    await turn_context.send_activity(
                        "Usage: `clarify REQ-XXXXX <your clarification>`"
                    )
            elif low == "status":
                await self._handle_status(turn_context, user, db)
            elif low in ("help", "hi", "hello", "hey"):
                await self._send_help(turn_context)
            else:
                await self._send_help(turn_context)
        finally:
            db.close()

    # ── Handle: new procurement request ───────────────────────────────────────

    async def _handle_new_request(self, turn_context: TurnContext, text: str, user: models.User, db):
        if not text:
            await turn_context.send_activity("Please describe what you need after `procure`.")
            return

        req = models.Request(requester_id=user.id, plain_text=text, status="new")
        db.add(req)
        db.add(models.AuditEntry(
            request_id=req.id,
            actor_id=user.id,
            action="submitted",
            notes="Submitted via Slack",
        ))
        db.commit()
        db.refresh(req)

        snippet = text[:200] + ("…" if len(text) > 200 else "")
        await turn_context.send_activity(
            f"📋 *Request `{req.id}` submitted!*\n\n"
            f"> {snippet}\n\n"
            f"The AI pipeline will evaluate your request. "
            f"You'll receive a DM here when action is required or a decision is made.\n"
            f"Track it: {_APP_URL}/dashboard/analysis?id={req.id}"
        )

    # ── Handle: clarification reply ────────────────────────────────────────────

    async def _handle_clarify(self, turn_context: TurnContext, request_id: str, message: str, user: models.User, db):
        req = db.query(models.Request).filter(models.Request.id == request_id).first()
        if not req:
            await turn_context.send_activity(f"❌ Request `{request_id}` not found.")
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
            request_id=req.id,
            actor_id=user.id,
            action="clarified",
            notes=f"Via Slack: {message}",
        ))
        db.commit()

        await turn_context.send_activity(f"✅ Clarification recorded for `{request_id}`.")

        # Notify the requester if someone else provided the clarification
        db.refresh(req)
        if req.requester and req.requester.slack_user_id and req.requester.slack_user_id != user.slack_user_id:
            from notifications import send_slack_dm
            send_slack_dm(
                req.requester.slack_user_id,
                f"💬 A clarification was added to your request `{request_id}`:\n> {message}",
            )

    # ── Handle: status ─────────────────────────────────────────────────────────

    async def _handle_status(self, turn_context: TurnContext, user: models.User, db):
        reqs = (
            db.query(models.Request)
            .filter(models.Request.requester_id == user.id)
            .order_by(models.Request.created_at.desc())
            .limit(5)
            .all()
        )
        if not reqs:
            await turn_context.send_activity(
                "You have no requests yet. Type `procure <what you need>` to submit one."
            )
            return

        lines = ["*Your 5 most recent requests:*\n"]
        for r in reqs:
            icon = _STATUS_ICON.get(r.status, "🔵")
            snippet = r.plain_text[:80] + ("…" if len(r.plain_text) > 80 else "")
            lines.append(f"{icon} `{r.id}` — {r.status.upper()}\n    _{snippet}_")

        await turn_context.send_activity("\n\n".join(lines))

    # ── Greeting when bot is added to DM ──────────────────────────────────────

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await self._send_help(turn_context)

    # ── Help message ──────────────────────────────────────────────────────────

    async def _send_help(self, turn_context: TurnContext):
        await turn_context.send_activity(
            "👋 *Welcome to ChainIQ Procurement Bot!*\n\n"
            "Here's what you can do:\n\n"
            "• `procure <describe what you need>` — submit a new procurement request\n"
            "• `clarify REQ-XXXXX <your answer>` — respond to a clarification request\n"
            "• `status` — see your 5 most recent requests\n\n"
            "_You'll receive DMs here when escalations need your input or when a decision is made._"
        )
