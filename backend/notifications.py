"""
Proactive Slack notifications — used by escalations and request routers.
Uses slack_sdk to DM users directly via the Slack Web API.
Silently skips if SLACK_BOT_TOKEN is not configured.
"""
import json
import os

_APP_URL = os.environ.get("CHAINIQ_APP_URL", "http://localhost:3000")


def send_slack_dm(slack_user_id: str, text: str) -> None:
    """Open a DM channel with the user and send a message. Never raises."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token or token.startswith("xoxb-your"):
        return
    try:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        channel = client.conversations_open(users=slack_user_id)["channel"]["id"]
        client.chat_postMessage(channel=channel, text=text)
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
    DM the requester a summary of the AI evaluation result.
    Called after enrich_and_evaluate completes for API-submitted requests.
    """
    if not requester or not getattr(requester, "slack_user_id", None):
        return

    text = _build_evaluation_summary(request)
    send_slack_dm(requester.slack_user_id, text)


def _build_evaluation_summary(request) -> str:
    """Build a Slack-formatted evaluation summary from the request's ai_output."""
    ai: dict = {}
    if request.ai_output:
        try:
            ai = json.loads(request.ai_output)
        except Exception:
            pass

    ranked = ai.get("ranked_suppliers", [])
    global_outputs = ai.get("global_outputs", {})
    flags = (ai.get("flag_assessment") or {}).get("flags", [])

    lines = [f"✅ *Evaluation complete for request `{request.id[:8]}…`*\n"]

    # Category & budget
    if request.category_l1:
        cat = request.category_l1
        if request.category_l2:
            cat += f" / {request.category_l2}"
        lines.append(f"📂 *Category:* {cat}")
    if request.budget_amount:
        lines.append(f"💰 *Budget:* {request.currency or 'EUR'} {request.budget_amount:,.0f}")
    lines.append("")

    # Supplier ranking
    MEDALS = ["🥇", "🥈", "🥉"]
    if ranked:
        lines.append("🏆 *Top Suppliers:*")
        for s in ranked[:3]:
            medal = MEDALS[s["position"] - 1] if s["position"] <= 3 else f"#{s['position']}"
            cost = f"{s.get('currency', '')} {s['cost_total']:,.0f}" if s.get("cost_total") else "—"
            unit = f"{s.get('currency', '')} {s['unit_price']:,.2f}/unit" if s.get("unit_price") else ""
            preferred = " ⭐ _preferred_" if s.get("preferred_supplier") else ""
            lines.append(f"  {medal} *{s['supplier_name']}*{preferred}")
            lines.append(f"       Total: {cost}  |  Unit: {unit}")
    else:
        lines.append("⚠️ *No suppliers could be ranked* — see warnings below")
    lines.append("")

    # Escalations triggered
    ESCALATION_LABELS: dict[str, str] = {
        "escalate_to_requester":               "Your clarification is needed",
        "escalate_to_procurement_manager":     "Procurement Manager approval required",
        "escalate_to_head_of_category":        "Head of Category escalation",
        "escalate_to_security_compliance":     "Security & Compliance review required",
        "escalate_to_regional_compliance":     "Regional Compliance approval required",
        "escalate_to_head_of_strategic_sourcing": "Head of Strategic Sourcing required",
        "escalate_to_cpo":                     "CPO approval required",
        "escalate_to_sourcing_excellence":     "Sourcing Excellence Lead escalation",
        "escalate_to_marketing_governance":    "Marketing Governance review required",
    }
    escalation_flags = [k for k, v in global_outputs.items() if k.startswith("escalate_") and v]
    if escalation_flags:
        lines.append("🔔 *Escalations triggered:*")
        for flag in escalation_flags:
            lines.append(f"  • {ESCALATION_LABELS.get(flag, flag.replace('_', ' ').title())}")
        lines.append("")

    # Required process actions
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
    if actions:
        lines.append("📋 *Required process actions:*")
        for a in actions:
            lines.append(f"  • {a}")
        lines.append("")

    # Min quotes
    min_quotes = global_outputs.get("min_supplier_quotes")
    if min_quotes is not None:
        lines.append(f"📌 *Minimum quotes required:* {min_quotes}")

    # Warnings / flags
    if flags:
        lines.append("⚠️ *Warnings:*")
        for f in flags[:3]:
            lines.append(f"  • {f['description']}")
        lines.append("")

    # Overall status
    if request.status == "pending_review":
        lines.append("🟠 *Status:* Pending review — escalation sent to the relevant team")
    else:
        lines.append("🟢 *Status:* Ready to proceed")

    lines.append(f"\n🔗 View full AI decision log: {_APP_URL}/dashboard/transparency?id={request.id}")

    return "\n".join(lines)
