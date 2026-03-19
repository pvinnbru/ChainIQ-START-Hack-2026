import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
import models  # noqa: F401 – ensures models are registered before create_all
from routers import auth, requests, escalations, transparency

Base.metadata.create_all(bind=engine)

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
