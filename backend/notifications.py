"""
Proactive Slack notifications — used by escalations and request routers.
Uses slack_sdk to DM users directly via the Slack Web API.
Silently skips if SLACK_BOT_TOKEN is not configured.
"""
import json
import os

_APP_URL = os.environ.get("CHAINIQ_APP_URL", "http://localhost:3000")


def send_slack_dm(slack_user_id: str, text: str, blocks: list = None) -> None:
    """Open a DM channel with the user and send a message. Never raises."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token or token.startswith("xoxb-your"):
        return
    try:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        channel = client.conversations_open(users=slack_user_id)["channel"]["id"]
        client.chat_postMessage(channel=channel, text=text, blocks=blocks)
    except Exception:
        pass  # Never let Slack failures break the main request


def notify_escalation(escalation, request, target_user) -> None:
    """DM the target user when a new escalation is created."""
    if not target_user or not getattr(target_user, "slack_user_id", None):
        return

    snippet = request.plain_text[:150] + ("…" if len(request.plain_text) > 150 else "")
    text = f"🔔 *Action required on `{request.id}`*\n\n> {snippet}\n\n"

    if escalation.type == "requester_clarification":
        text += f"Please provide clarification. Reply: `clarify {request.id} <your answer>`"
    else:
        text += (
            f"Please review this request in ChainIQ: "
            f"{_APP_URL}/dashboard/transparency?id={request.id}"
        )

    if escalation.message:
        text += f"\n\n*Note from system:* {escalation.message}"

    send_slack_dm(target_user.slack_user_id, text)


def notify_decision(request, requester) -> None:
    """DM the requester when their request is approved or rejected."""
    if not requester or not getattr(requester, "slack_user_id", None):
        return

    icon = "✅" if request.status == "approved" else "❌"
    text = (
        f"{icon} Your request `{request.id}` was *{request.status.upper()}*.\n"
        f"View details: {_APP_URL}/dashboard/transparency?id={request.id}"
    )
    send_slack_dm(requester.slack_user_id, text)


def notify_evaluation_complete(request, requester) -> None:
    """
    DM the requester a brief confirmation that their request was received
    and evaluated. No supplier details are shared with the requester.
    """
    if not requester or not getattr(requester, "slack_user_id", None):
        return

    cat = request.category_l1 or ""
    if request.category_l2:
        cat += f" / {request.category_l2}" if cat else request.category_l2

    status_msg = (
        "🟠 Your request requires review by the procurement team."
        if request.status in ("escalated", "pending_review")
        else "🟢 Your request has been evaluated and is being processed."
    )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📋 Request Received: {request.id[:12]}…"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Your procurement request has been received and evaluated by ChainIQ.\n\n"
                    + (f"📂 *Category:* {cat}\n" if cat else "")
                    + (f"💰 *Budget:* {request.currency or 'EUR'} {request.budget_amount:,.0f}\n" if request.budget_amount else "")
                    + f"\n{status_msg}"
                ),
            }
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"You will be notified when a decision is made. | 🔗 <{_APP_URL}/dashboard|View Dashboard>"
                }
            ]
        },
    ]

    send_slack_dm(
        requester.slack_user_id,
        text=f"Your request {request.id} has been received and is being processed.",
        blocks=blocks,
    )


def _build_evaluation_summary(request) -> dict:
    """Build a Slack-formatted evaluation summary using Block Kit."""
    ai: dict = {}
    if request.ai_output:
        try:
            ai = json.loads(request.ai_output)
        except Exception:
            pass

    escalation_assess = ai.get("escalation_assessment")

    ranked = ai.get("ranked_suppliers", [])
    global_outputs = ai.get("global_outputs", {})
    flags = (ai.get("flag_assessment") or {}).get("flags", [])

    fallback_text = f"Evaluation complete for request {request.id[:8]}…"
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"✅ Evaluation Complete: REQ-{request.id[:6].upper()}…"
            }
        }
    ]

    # Category & budget
    cat_budget = []
    if request.category_l1:
        cat = request.category_l1
        if request.category_l2:
            cat += f" / {request.category_l2}"
        cat_budget.append(f"📂 *Category:* {cat}")
    if request.budget_amount:
        cat_budget.append(f"💰 *Budget:* {request.currency or 'EUR'} {request.budget_amount:,.0f}")
    
    if cat_budget:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(cat_budget)}
        })
    blocks.append({"type": "divider"})

    # Supplier ranking
    MEDALS = ["🥇", "🥈", "🥉"]
    rank_text = "🏆 *Top Suppliers*\n"
    if ranked:
        for s in ranked[:3]:
            medal = MEDALS[s["position"] - 1] if s["position"] <= 3 else f"#{s['position']}"
            cost = f"{s.get('currency', '')} {s['cost_total']:,.0f}" if s.get("cost_total") else "—"
            unit = f"{s.get('currency', '')} {s['unit_price']:,.2f}/unit" if s.get("unit_price") else ""
            preferred = " ⭐ _preferred_" if s.get("preferred_supplier") else ""
            rank_text += f"\n{medal} *{s['supplier_name']}*{preferred}\n`Total: {cost}`  |  `Unit: {unit}`\n"
    else:
        rank_text += "\n⚠️ *No suppliers could be ranked* — see warnings below"
    
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": rank_text}
    })

    # Escalations & Actions
    escalation_records = escalation_assess.get("records", []) if escalation_assess else []
    
    ACTION_FLAGS: dict[str, str] = {
        "fast_track_eligible":          "⚡ Fast-track eligible — single quote allowed",
        "requires_security_review":     "🔒 Security architecture review required",
        "requires_engineering_review":  "⚙️ Engineering review required",
        "requires_design_signoff":      "✏️ Business design sign-off required",
        "requires_cv_review":           "📄 Consultant CV review required",
        "requires_certification_check": "📋 Supplier certification check required",
        "requires_brand_safety_review": "🛡️ Brand safety review required",
        "requires_performance_baseline":"📊 SEM performance baseline required",
    }
    actions = [label for key, label in ACTION_FLAGS.items() if global_outputs.get(key)]

    if escalation_records or actions:
        blocks.append({"type": "divider"})
        req_actions_text = ""
        if escalation_records:
            req_actions_text += "🔔 *Escalations triggered:*\n"
            for rec in escalation_records:
                person = rec.get("person_to_escalate_to", "").replace("escalate_to_", "").replace("_", " ").title()
                req_actions_text += f"• *{person}:* {rec.get('reason_for_escalation', 'Review required')}\n"
            req_actions_text += "\n"
        if actions:
            req_actions_text += "📋 *Required process actions:*\n"
            for a in actions:
                req_actions_text += f"• {a}\n"
        
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": req_actions_text.strip()}
        })

    # Quotes and warnings
    min_quotes = global_outputs.get("min_supplier_quotes")
    warn_text = ""
    if min_quotes is not None:
        warn_text += f"📌 *Minimum quotes required:* {min_quotes}\n"
    if flags:
        warn_text += "\n⚠️ *Warnings:*\n"
        for f in flags[:3]:
            warn_text += f"• {f['description']}\n"
    
    if warn_text:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": warn_text.strip()}
        })

    blocks.append({"type": "divider"})
    # Overall status Context block
    status_msg = "🟠 *Status:* Pending review — escalation sent to the relevant team" if request.status == "pending_review" else "🟢 *Status:* Ready to proceed"
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"{status_msg} | 🔗 <{_APP_URL}/dashboard/transparency?id={request.id}|View full AI decision log>"
            }
        ]
    })

    return {"text": fallback_text, "blocks": blocks}
