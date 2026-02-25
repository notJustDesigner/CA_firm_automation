"""
app/tools/llm_client.py
Async LLM client wrapping the Ollama Python SDK.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import ollama

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _resolve_model(model: str | None, task_type: str | None) -> str:
    """Pick the right model given explicit name, task_type, or fall back to default."""
    if model:
        return model
    if task_type and task_type in settings.OLLAMA_MODELS:
        return settings.OLLAMA_MODELS[task_type]
    return settings.OLLAMA_MODEL


def _get_client() -> ollama.AsyncClient:
    return ollama.AsyncClient(host=settings.OLLAMA_BASE_URL)


# ─── Public API ───────────────────────────────────────────────────────────────

async def ask_llm(
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    model: str | None = None,
    task_type: str | None = None,
) -> str:
    """
    Send a prompt to Ollama and return the response text.

    Parameters
    ----------
    prompt      : The user message / instruction.
    system      : Optional system prompt.
    json_mode   : If True, forces JSON output and validates it (up to 3 retries).
    model       : Explicit model name; overrides task_type and default.
    task_type   : Key into settings.OLLAMA_MODELS for automatic model routing.

    Returns
    -------
    str — raw text (or validated JSON string when json_mode=True).

    Raises
    ------
    ConnectionError  : Ollama server is unreachable.
    ValueError       : JSON mode failed after 3 retries (raw response included).
    """
    resolved_model = _resolve_model(model, task_type)
    client = _get_client()

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {"model": resolved_model, "messages": messages}
    if json_mode:
        kwargs["format"] = "json"

    max_retries = 3 if json_mode else 1
    last_raw = ""

    for attempt in range(1, max_retries + 1):
        backoff = 2 ** (attempt - 1)  # 1s, 2s, 4s
        try:
            logger.debug("ask_llm: model=%s attempt=%d", resolved_model, attempt)
            response = await client.chat(**kwargs)
            last_raw = response.message.content or ""

            if json_mode:
                try:
                    json.loads(last_raw)   # validate
                    return last_raw
                except json.JSONDecodeError:
                    logger.warning(
                        "ask_llm: JSON parse failed on attempt %d/%d. Raw: %.200s",
                        attempt, max_retries, last_raw,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(backoff)
                    continue
            else:
                return last_raw

        except (httpx.ConnectError, httpx.ConnectTimeout, ollama.ResponseError) as exc:
            # Distinguish "server not running" from transient errors
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                raise ConnectionError(
                    f"Ollama server not running at {settings.OLLAMA_BASE_URL}. "
                    "Run: ollama serve"
                ) from exc
            # Transient Ollama error — back off and retry
            logger.warning("ask_llm: transient error on attempt %d: %s", attempt, exc)
            if attempt < max_retries:
                await asyncio.sleep(backoff)
            else:
                raise

        except Exception as exc:
            logger.error("ask_llm: unexpected error: %s", exc)
            raise

    # json_mode exhausted all retries
    raise ValueError(
        f"ask_llm: JSON mode failed after {max_retries} attempts. "
        f"Last raw response: {last_raw!r}"
    )


async def check_ollama_health() -> dict:
    """
    Check whether the Ollama server is reachable and list available models.

    Returns
    -------
    {"available": bool, "models": list[str]}
    """
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"available": True, "models": models}
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return {"available": False, "models": []}


async def ensure_model_available(model_name: str) -> bool:
    """
    Ensure a model exists locally; pull it if not.

    Returns True when the model is ready.
    """
    health = await check_ollama_health()
    if not health["available"]:
        raise ConnectionError(
            f"Ollama server not running at {settings.OLLAMA_BASE_URL}. "
            "Run: ollama serve"
        )

    # Normalize: "llama3.1:8b" and "llama3.1" should both match "llama3.1:8b"
    available = health["models"]
    if any(model_name in m or m in model_name for m in available):
        logger.info("ensure_model_available: %s already present", model_name)
        return True

    logger.info("ensure_model_available: pulling %s …", model_name)
    client = _get_client()
    try:
        # ollama.AsyncClient.pull streams progress dicts; we consume and log them
        async for progress in await client.pull(model_name, stream=True):
            status = progress.get("status", "")
            if status:
                logger.debug("pull %s: %s", model_name, status)
        logger.info("ensure_model_available: %s pull complete", model_name)
        return True
    except Exception as exc:
        logger.error("ensure_model_available: failed to pull %s: %s", model_name, exc)
        return False