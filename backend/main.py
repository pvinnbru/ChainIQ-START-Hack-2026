from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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


@app.get("/")
def root():
    return {"status": "ok", "message": "ChainIQ API running"}
