"""
app/tools/playwright_tool.py
Async Playwright browser tool with CAPTCHA/login detection and HITL support.
"""
from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from app.tools.hitl_manager import pause_for_hitl, resume_hitl

logger = logging.getLogger(__name__)

# ─── CAPTCHA / login selectors to watch for ───────────────────────────────────

HITL_SELECTORS: list[str] = [
    "#captcha",
    ".g-recaptcha",
    ".h-captcha",
    "#loginForm",
    "#login-form",
    '[name="captcha"]',
    ".captcha-container",
    'input[name="username"]:not([value])',
]


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class BrowserResult:
    success: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    hitl_needed: bool = False
    reason: str = ""
    session_id: str = ""
    screenshot_b64: str = ""
    current_url: str = ""
    error: str = ""


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _take_screenshot(page: Page) -> str:
    """Return a base64-encoded PNG screenshot of the current page."""
    try:
        raw = await page.screenshot(type="png", full_page=False)
        return base64.b64encode(raw).decode("utf-8")
    except Exception as exc:
        logger.warning("screenshot failed: %s", exc)
        return ""


async def _check_for_hitl(page: Page) -> tuple[bool, str]:
    """
    Inspect the current page for CAPTCHA or login walls.
    Returns (hitl_needed: bool, matched_selector: str).
    """
    for selector in HITL_SELECTORS:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                logger.info("HITL trigger detected: selector=%s", selector)
                return True, selector
        except Exception:
            continue
    return False, ""


async def _apply_cookies(context: BrowserContext, cookies: list[dict]) -> None:
    """Inject saved cookies into the browser context."""
    if cookies:
        await context.add_cookies(cookies)


async def _load_session_from_redis(session_id: str) -> dict | None:
    """Load a saved HITL session from Redis."""
    try:
        import json
        from app.tools.hitl_manager import _get_redis
        r = await _get_redis()
        raw = await r.get(f"hitl:{session_id}")
        if raw:
            return json.loads(raw)
        return None
    except Exception as exc:
        logger.error("Failed to load session %s from Redis: %s", session_id, exc)
        return None


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_browser(
    url: str,
    actions: list[dict],
    session_id: str | None = None,
) -> BrowserResult:
    """
    Launch a headless Chromium browser, navigate to `url`, and execute
    a sequence of `actions`.

    Parameters
    ----------
    url        : Starting URL (used only if no session_id is provided or
                 session has no saved URL).
    actions    : List of action dicts — see supported types below.
    session_id : If provided, restore cookies/URL from a saved Redis HITL
                 session and continue from where it left off.

    Supported action types
    ----------------------
    navigate          : {type, url}
    fill              : {type, selector, value}
    click             : {type, selector}
    wait_for_selector : {type, selector, timeout?}
    get_text          : {type, selector}          → data[selector] = str
    get_attribute     : {type, selector, attribute} → data[f"{selector}.{attribute}"] = str
    screenshot        : {type}                    → result.screenshot_b64
    get_all_text      : {type, selector}          → data[selector] = list[str]
    """
    result = BrowserResult()
    saved_session: dict | None = None
    actions_to_run = list(actions)

    # ── Restore session if requested ─────────────────────────────────────────
    if session_id:
        saved_session = await _load_session_from_redis(session_id)
        if saved_session:
            # Remaining actions may have been saved at pause time
            remaining = saved_session.get("actions_remaining")
            if remaining is not None:
                actions_to_run = remaining
            # Use saved URL as starting point
            url = saved_session.get("current_url", url)
            logger.info("Restored HITL session %s, resuming at %s", session_id, url)
        else:
            logger.warning("Session %s not found in Redis, starting fresh", session_id)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # Restore cookies from saved session
        if saved_session and saved_session.get("cookies"):
            await _apply_cookies(context, saved_session["cookies"])

        page: Page = await context.new_page()

        try:
            # Navigate to starting URL
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            result.current_url = page.url

            # ── Execute actions ───────────────────────────────────────────────
            for i, action in enumerate(actions_to_run):
                action_type = action.get("type", "")
                logger.debug("action[%d]: %s", i, action_type)

                try:
                    if action_type == "navigate":
                        await page.goto(
                            action["url"],
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        result.current_url = page.url

                    elif action_type == "fill":
                        await page.wait_for_selector(
                            action["selector"], timeout=10_000
                        )
                        await page.fill(action["selector"], action["value"])

                    elif action_type == "click":
                        await page.wait_for_selector(
                            action["selector"], timeout=10_000
                        )
                        await page.click(action["selector"])
                        # Brief wait for any navigation/AJAX triggered by click
                        await page.wait_for_load_state(
                            "domcontentloaded", timeout=15_000
                        )
                        result.current_url = page.url

                    elif action_type == "wait_for_selector":
                        timeout = action.get("timeout", 10_000)
                        await page.wait_for_selector(
                            action["selector"], timeout=timeout
                        )

                    elif action_type == "get_text":
                        selector = action["selector"]
                        el = await page.query_selector(selector)
                        text = await el.inner_text() if el else ""
                        result.data[selector] = text

                    elif action_type == "get_attribute":
                        selector = action["selector"]
                        attr = action["attribute"]
                        el = await page.query_selector(selector)
                        value = await el.get_attribute(attr) if el else ""
                        result.data[f"{selector}.{attr}"] = value

                    elif action_type == "screenshot":
                        result.screenshot_b64 = await _take_screenshot(page)

                    elif action_type == "get_all_text":
                        selector = action["selector"]
                        elements = await page.query_selector_all(selector)
                        texts = []
                        for el in elements:
                            try:
                                texts.append(await el.inner_text())
                            except Exception:
                                pass
                        result.data[selector] = texts

                    else:
                        logger.warning("Unknown action type: %s — skipping", action_type)

                    # ── HITL check after every action ─────────────────────────
                    hitl_needed, matched = await _check_for_hitl(page)
                    if hitl_needed:
                        screenshot = await _take_screenshot(page)
                        cookies = await context.cookies()
                        remaining_actions = actions_to_run[i + 1:]

                        session_data = {
                            "cookies": cookies,
                            "current_url": page.url,
                            "screenshot_b64": screenshot,
                            "actions_remaining": remaining_actions,
                            "matched_selector": matched,
                        }
                        new_session_id = await pause_for_hitl(
                            reason=f"HITL trigger detected: {matched}",
                            session_data=session_data,
                        )

                        result.hitl_needed = True
                        result.reason = f"CAPTCHA or login wall detected ({matched})"
                        result.session_id = new_session_id
                        result.screenshot_b64 = screenshot
                        result.current_url = page.url
                        return result

                except PlaywrightTimeoutError as te:
                    logger.warning(
                        "Timeout on action %s selector=%s: %s",
                        action_type,
                        action.get("selector", ""),
                        te,
                    )
                    # Non-fatal: continue to next action
                    continue

            # ── Final HITL check after all actions ────────────────────────────
            hitl_needed, matched = await _check_for_hitl(page)
            if hitl_needed:
                screenshot = await _take_screenshot(page)
                cookies = await context.cookies()
                session_data = {
                    "cookies": cookies,
                    "current_url": page.url,
                    "screenshot_b64": screenshot,
                    "actions_remaining": [],
                    "matched_selector": matched,
                }
                new_session_id = await pause_for_hitl(
                    reason=f"HITL trigger at end of actions: {matched}",
                    session_data=session_data,
                )
                result.hitl_needed = True
                result.reason = f"CAPTCHA or login wall at end ({matched})"
                result.session_id = new_session_id
                result.screenshot_b64 = screenshot
                result.current_url = page.url
                return result

            result.success = True
            result.current_url = page.url

        except Exception as exc:
            logger.error("run_browser error: %s", exc)
            result.error = f"{type(exc).__name__}: {exc}"
            result.current_url = page.url if page else url
            # Take error screenshot if possible
            try:
                result.screenshot_b64 = await _take_screenshot(page)
            except Exception:
                pass

        finally:
            await context.close()
            await browser.close()

    return result