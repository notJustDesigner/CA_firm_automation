"""
app/api/system.py
System management endpoints: Ollama health, model pull (SSE), benchmark.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.config import settings
from app.tools.llm_client import ask_llm, check_ollama_health, ensure_model_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/system", tags=["System"])

# ─── Schemas ──────────────────────────────────────────────────────────────────

class PullModelRequest(BaseModel):
    model_name: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _required_models() -> list[str]:
    """Deduplicated list of models referenced in OLLAMA_MODELS config."""
    return list(dict.fromkeys(settings.OLLAMA_MODELS.values()))


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/ollama-status")
async def ollama_status(current_user: str = Depends(get_current_user)):
    """
    Returns Ollama health plus which required models are present / missing.
    """
    health = await check_ollama_health()
    required = _required_models()
    available = health["models"]

    # A required model is "present" if any locally-available name contains it
    # (handles "llama3.1:8b" matching "llama3.1:8b-instruct-q4" etc.)
    missing = [
        req for req in required
        if not any(req in avail or avail in req for avail in available)
    ]

    return {
        "running": health["available"],
        "models_available": available,
        "required_models": required,
        "missing_models": missing,
    }


@router.post("/pull-model")
async def pull_model(
    body: PullModelRequest,
    current_user: str = Depends(get_current_user),
):
    """
    Pull an Ollama model and stream progress as Server-Sent Events.

    Connect with:
        curl -N -X POST /api/v1/system/pull-model \\
             -H "Authorization: Bearer <token>" \\
             -H "Content-Type: application/json" \\
             -d '{"model_name": "mistral:7b"}'
    """
    model_name = body.model_name

    async def event_stream() -> AsyncGenerator[str, None]:
        yield _sse("started", {"model": model_name, "status": "starting pull"})
        try:
            import ollama as _ollama
            client = _ollama.AsyncClient(host=settings.OLLAMA_BASE_URL)
            async for progress in await client.pull(model_name, stream=True):
                payload = {
                    "status": progress.get("status", ""),
                    "completed": progress.get("completed"),
                    "total": progress.get("total"),
                    "digest": progress.get("digest", ""),
                }
                yield _sse("progress", payload)
                await asyncio.sleep(0)   # yield control to event loop

            yield _sse("done", {"model": model_name, "status": "pull complete"})

        except Exception as exc:
            logger.error("pull_model SSE error: %s", exc)
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


@router.get("/benchmark")
async def benchmark(current_user: str = Depends(get_current_user)):
    """
    Run a short test prompt through every configured model and report latency.
    """
    TEST_PROMPT = (
        "Reply with exactly one sentence confirming you are working correctly."
    )
    results = []
    health = await check_ollama_health()

    if not health["available"]:
        return {
            "ollama_running": False,
            "results": [],
            "message": f"Ollama not reachable at {settings.OLLAMA_BASE_URL}",
        }

    # Deduplicate so we test each model once even if used for multiple task types
    seen: set[str] = set()
    models_to_test: list[tuple[str, str]] = []
    for task_type, model in settings.OLLAMA_MODELS.items():
        if model not in seen:
            models_to_test.append((task_type, model))
            seen.add(model)

    for task_type, model in models_to_test:
        entry: dict = {"model": model, "task_type": task_type}
        # Skip if not locally available (avoid hanging on a missing model)
        model_present = any(
            model in avail or avail in model for avail in health["models"]
        )
        if not model_present:
            entry.update({"status": "skipped", "reason": "model not pulled locally"})
            results.append(entry)
            continue

        t0 = time.monotonic()
        try:
            output = await ask_llm(
                prompt=TEST_PROMPT,
                model=model,
                json_mode=False,
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            entry.update({
                "status": "ok",
                "response_time_ms": elapsed_ms,
                "test_output": output.strip()[:200],  # truncate for display
            })
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            entry.update({
                "status": "error",
                "response_time_ms": elapsed_ms,
                "error": str(exc),
            })

        results.append(entry)

    return {"ollama_running": True, "results": results}


# ─── SSE helper ───────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"