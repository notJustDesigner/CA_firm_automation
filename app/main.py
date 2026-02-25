"""
app/main.py
FastAPI application entry point.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine
from app.api.auth import get_current_user, router as auth_router
from app.api.system import router as system_router

logger = logging.getLogger(__name__)
APP_VERSION = "0.1.0"


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    # Check Ollama — warn but never crash the app
    try:
        from app.tools.llm_client import check_ollama_health
        health = await check_ollama_health()

        if not health["available"]:
            logger.warning(
                "⚠️  Ollama is NOT running at %s. "
                "AI features will be unavailable until you run: ollama serve",
                settings.OLLAMA_BASE_URL,
            )
        else:
            available_models = health["models"]
            required_models = list(dict.fromkeys(settings.OLLAMA_MODELS.values()))
            missing = [
                m for m in required_models
                if not any(m in avail or avail in m for avail in available_models)
            ]
            if missing:
                logger.warning(
                    "⚠️  Ollama is running but these models are missing: %s. "
                    "Pull them with: ollama pull <model>",
                    ", ".join(missing),
                )
            else:
                logger.info(
                    "✅ Ollama healthy at %s — all required models present: %s",
                    settings.OLLAMA_BASE_URL,
                    ", ".join(available_models),
                )
    except Exception as exc:
        logger.warning("⚠️  Ollama health check failed at startup: %s", exc)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await engine.dispose()
    logger.info("Database engine disposed.")


# ─── App factory ──────────────────────────────────────────────────────────────

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

# ─── Middleware ───────────────────────────────────────────────────────────────

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
app.include_router(system_router)


# ─── Health endpoint ──────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """
    Public health check — no auth required.
    Returns DB status and Ollama availability.
    """
    from sqlalchemy import text
    from app.database import AsyncSessionLocal
    from app.tools.llm_client import check_ollama_health

    # DB check
    db_status = "unreachable"
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    # Ollama check
    ollama_health = await check_ollama_health()
    required_models = list(dict.fromkeys(settings.OLLAMA_MODELS.values()))
    missing_models = [
        m for m in required_models
        if not any(m in avail or avail in m for avail in ollama_health["models"])
    ]

    return {
        "status": "ok",
        "version": APP_VERSION,
        "db": db_status,
        "ollama": {
            "running": ollama_health["available"],
            "models_available": ollama_health["models"],
            "missing_models": missing_models,
        },
    }


@app.get("/api/v1/ping", tags=["System"])
async def ping(current_user: str = Depends(get_current_user)):
    """Protected ping — confirms auth is working."""
    return {"pong": True, "user": current_user}