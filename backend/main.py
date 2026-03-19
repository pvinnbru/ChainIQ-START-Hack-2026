import os
import traceback

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from database import engine, Base
import models  # noqa: F401 – ensures models are registered before create_all
from routers import auth, requests, escalations, transparency

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ChainIQ API", version="0.1.0")

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

_adapter = None
_bot = None


def _get_bot_adapter():
    global _adapter, _bot
    if _adapter is None:
        app_id = os.environ.get("MicrosoftAppId", "")
        app_password = os.environ.get("MicrosoftAppPassword", "")
        print("BOT CONFIG:", {
            "app_id_present": bool(app_id),
            "app_password_present": bool(app_password),
        })

        if app_id and not app_id.startswith("your-"):
            from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
            from bot import ProcureBot

            tenant_id = os.environ.get("MicrosoftAppTenantId", "")
            settings = BotFrameworkAdapterSettings(
                app_id=app_id,
                app_password=app_password,
                app_tenant_id=tenant_id if tenant_id else None,
            )
            _adapter = BotFrameworkAdapter(settings)
            _bot = ProcureBot()
            print("BOT ADAPTER INITIALIZED")

    return _adapter, _bot


@app.post("/api/messages")
async def messages(req: FastAPIRequest):
    print("🔥 /api/messages HIT")

    adapter, bot = _get_bot_adapter()
    if adapter is None:
        print("❌ Bot not configured")
        return Response(status_code=503, content="Bot not configured (set MicrosoftAppId)")

    try:
        body = await req.json()
        auth_header = req.headers.get("Authorization", "")

        print("RAW BODY:", body)
        print("AUTH HEADER PRESENT:", bool(auth_header))

        if not body or "type" not in body:
            print("❌ Invalid Bot Framework payload")
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid Bot Framework activity payload"},
            )

        from botbuilder.schema import Activity
        activity = Activity().deserialize(body)

        invoke_response = await adapter.process_activity(
            activity,
            auth_header,
            bot.on_turn,
        )

        print("✅ process_activity completed")

        if invoke_response:
            return JSONResponse(
                status_code=invoke_response.status,
                content=invoke_response.body,
            )

        return Response(status_code=202)

    except Exception as e:
        print("❌ /api/messages ERROR:", repr(e))
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.get("/")
def root():
    return {"status": "ok", "message": "ChainIQ API running"}