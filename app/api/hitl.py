"""
app/api/hitl.py
Human-in-the-Loop management endpoints.
The CA uses these to see what needs manual intervention, resolve it,
and resume blocked browser sessions.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.tools.hitl_manager import (
    cancel_hitl,
    get_hitl_status,
    list_pending_hitl,
    resume_hitl,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/hitl", tags=["Human-in-the-Loop"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    session_id: str
    captcha_token: str | None = None
    cookies: dict | None = None
    manual_data: dict | None = None


class ResolveResponse(BaseModel):
    success: bool
    message: str
    session_id: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/pending")
async def get_pending_sessions(current_user: str = Depends(get_current_user)):
    """
    Return all pending HITL sessions.
    Each entry includes a base64 screenshot so the CA can see
    exactly what the browser encountered.
    """
    sessions = await list_pending_hitl()
    return {
        "count": len(sessions),
        "sessions": [
            {
                "session_id": s["session_id"],
                "reason": s["reason"],
                "current_url": s["current_url"],
                "age_seconds": s["age_seconds"],
                "ttl_seconds": s.get("ttl_seconds", 0),
                "status": s["status"],
                # Include screenshot only if present (can be large)
                "has_screenshot": bool(s.get("screenshot_b64")),
                "screenshot_b64": s.get("screenshot_b64", ""),
            }
            for s in sessions
        ],
    }


@router.get("/status/{session_id}")
async def session_status(
    session_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    Return the full status and data for a specific HITL session.
    Includes screenshot, saved cookies, and remaining actions.
    """
    result = await get_hitl_status(session_id)
    if not result["found"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL session '{session_id}' not found or expired",
        )
    return result


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_session(
    body: ResolveRequest,
    current_user: str = Depends(get_current_user),
):
    """
    Submit a resolution for a pending HITL session.

    The CA provides one or more of:
    - `captcha_token`  : solved CAPTCHA response token
    - `cookies`        : manually copied browser cookies (JSON)
    - `manual_data`    : any other free-form input the agent needs

    The resolution is saved to Redis under `hitl_resolved:{session_id}`.
    The blocked agent/workflow should poll `GET /hitl/status/{session_id}`
    and resume once a resolution is available.
    """
    resolution = {}
    if body.captcha_token is not None:
        resolution["captcha_token"] = body.captcha_token
    if body.cookies is not None:
        resolution["cookies"] = body.cookies
    if body.manual_data is not None:
        resolution["manual_data"] = body.manual_data

    if not resolution:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of captcha_token, cookies, or manual_data must be provided",
        )

    try:
        merged = await resume_hitl(body.session_id, resolution)
        logger.info(
            "HITL session %s resolved by user %s", body.session_id, current_user
        )
        return ResolveResponse(
            success=True,
            message=f"Session {body.session_id} resolved successfully. "
                    "The agent will resume on its next poll.",
            session_id=body.session_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("resolve_session error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resolve session: {exc}",
        )


@router.delete("/{session_id}")
async def cancel_session(
    session_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    Cancel and discard a HITL session.
    Use this when the CA decides not to proceed with the blocked task.
    """
    deleted = await cancel_hitl(session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL session '{session_id}' not found or already expired",
        )
    logger.info("HITL session %s cancelled by %s", session_id, current_user)
    return {
        "success": True,
        "message": f"Session {session_id} cancelled and removed.",
        "session_id": session_id,
    }