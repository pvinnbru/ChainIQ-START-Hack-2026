"""
Proactive Slack notifications — used by escalations and request routers.
Uses slack_sdk to DM users directly via the Slack Web API.
Silently skips if SLACK_BOT_TOKEN is not configured.
"""
import os


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
            f"{os.environ.get('CHAINIQ_APP_URL', 'http://localhost:3000')}/dashboard/analysis?id={request.id}"
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
        f"View details: {os.environ.get('CHAINIQ_APP_URL', 'http://localhost:3000')}/dashboard/analysis?id={request.id}"
    )
    send_slack_dm(requester.slack_user_id, text)
