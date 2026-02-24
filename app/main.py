#app/main.py
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os

from app.config import settings
from app.database import engine
from app.api.auth import router as auth_router, get_current_user
from app.schemas.base import HealthResponse

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title="CA Firm SaaS",
    description=(
        "Internal automation platform for CA compliance workflows. "
        "All processing is 100% local — no external API calls."
    ),
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Static files ─────────────────────────────────────────────────────────────
if os.path.isdir("templates"):
    app.mount("/static", StaticFiles(directory="templates"), name="static")

# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api/v1")


# ─── Health endpoint ──────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Public health check — no auth required."""
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    db_status = "unreachable"
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    return HealthResponse(status="ok", version=APP_VERSION, db=db_status)


@app.get("/api/v1/ping", tags=["System"])
async def ping(current_user: str = Depends(get_current_user)):
    """Protected ping — confirms auth is working."""
    return {"pong": True, "user": current_user}