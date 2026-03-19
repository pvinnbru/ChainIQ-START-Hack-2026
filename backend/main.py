import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
import models  # noqa: F401 – ensures models are registered before create_all
from routers import auth, requests, escalations, transparency

Base.metadata.create_all(bind=engine)


def _ensure_escalation_users():
    """Create demo escalation-reviewer users if they don't exist yet."""
    from database import SessionLocal
    DEMO_USERS = [
        {
            "id": "user-carol",
            "name": "Carol Dupont",
            "email": "carol@chainiq.demo",
            "role": "category_head",
            "business_unit": "IT",
            "country": "FR",
            "site": "Paris",
            "requester_role": "Head of IT Category",
        },
        {
            "id": "user-dave",
            "name": "Dave Patel",
            "email": "dave@chainiq.demo",
            "role": "compliance_reviewer",
            "business_unit": "Legal & Compliance",
            "country": "GB",
            "site": "London",
            "requester_role": "Compliance Reviewer",
        },
    ]
    db = SessionLocal()
    try:
        for u in DEMO_USERS:
            if not db.query(models.User).filter(models.User.id == u["id"]).first():
                db.add(models.User(**u))
                print(f"✅ Created demo user: {u['name']} [{u['role']}]")
        db.commit()
    finally:
        db.close()


_ensure_escalation_users()

app = FastAPI(title="ChainIQ API", version="0.1.0")


def _start_slack_bot():
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not app_token or app_token.startswith("xapp-your"):
        print("⚠️  SLACK_APP_TOKEN not set — Slack bot not started.")
        return
    try:
        from bot_slack import app as slack_app
        from slack_bolt.adapter.socket_mode import SocketModeHandler
        print("✅ ChainIQ Slack Bot starting (Socket Mode)...")
        SocketModeHandler(slack_app, app_token).start()
    except Exception as e:
        print(f"⚠️  Slack bot failed to start: {e}")


threading.Thread(target=_start_slack_bot, daemon=True, name="slack-bot").start()


def _prewarm_evaluation_pipeline():
    try:
        from services.evaluation import _ensure_paths
        _ensure_paths()
        from evaluate_request import _get_pipeline
        print("⏳ Pre-warming evaluation pipeline...")
        _get_pipeline()
        print("✅ Evaluation pipeline ready.")
    except Exception as e:
        print(f"⚠️  Evaluation pipeline pre-warm failed: {e}")


threading.Thread(target=_prewarm_evaluation_pipeline, daemon=True, name="eval-prewarm").start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(requests.router)
app.include_router(escalations.router)
app.include_router(transparency.router)


@app.get("/")
def root():
    return {"status": "ok", "message": "ChainIQ API running"}
