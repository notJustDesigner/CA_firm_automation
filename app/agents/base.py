"""
app/agents/base.py
Base agent infrastructure: AgentState TypedDict + BaseGraph orchestrator
+ Playwright LangGraph ToolNode.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, TypedDict

from app.database import AsyncSessionLocal
from app.models.task import TaskResult

logger = logging.getLogger(__name__)


# ─── Shared state schema ──────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    # Core I/O
    input: str                          # raw user input / task description
    output: str | None                  # final answer / generated content

    # Routing
    task_type: str                      # e.g. "json_extraction", "email_drafting"
    client_id: int | None               # linked CA client (optional)

    # Execution trace
    intermediate_steps: list[dict]      # [{step, tool, input, output, ts}]

    # Human-in-the-loop
    hitl_needed: bool                   # True → pause for human review
    hitl_reason: str | None             # why HITL was triggered
    hitl_data: dict | None              # data to show the human reviewer

    # Error handling
    error: str | None                   # exception message if something went wrong

    # Extensible metadata
    metadata: dict[str, Any]            # arbitrary key-value bag per agent


# ─── Playwright LangGraph ToolNode ────────────────────────────────────────────

def build_playwright_tool_node():
    """
    Build a LangGraph ToolNode wrapping run_browser() as a callable tool.

    Usage in a subclass graph:
        from app.agents.base import build_playwright_tool_node
        browser_node = build_playwright_tool_node()
        graph.add_node("browser", browser_node)

    The node expects the AgentState to contain:
        metadata["browser_url"]     : str   — target URL
        metadata["browser_actions"] : list  — actions list
        metadata["browser_session"] : str   — optional session_id for HITL resume

    It writes back into AgentState:
        metadata["browser_result"]  : dict  — BrowserResult as dict
        hitl_needed                 : bool
        hitl_reason                 : str | None
        hitl_data                   : dict | None
    """
    from langgraph.prebuilt import ToolNode
    from langchain_core.tools import tool
    from app.tools.playwright_tool import run_browser

    @tool
    async def browser_tool(
        url: str,
        actions: list[dict],
        session_id: str = "",
    ) -> dict:
        """
        Run a headless browser session.
        Automatically handles CAPTCHA/login detection and saves HITL sessions.
        Returns a BrowserResult dict.
        """
        result = await run_browser(
            url=url,
            actions=actions,
            session_id=session_id or None,
        )
        return {
            "success": result.success,
            "data": result.data,
            "hitl_needed": result.hitl_needed,
            "reason": result.reason,
            "session_id": result.session_id,
            "screenshot_b64": result.screenshot_b64,
            "current_url": result.current_url,
            "error": result.error,
        }

    return ToolNode(tools=[browser_tool])


async def playwright_agent_node(state: AgentState) -> AgentState:
    """
    A ready-to-use LangGraph node function that reads browser config from
    state["metadata"] and writes results back.

    Add to your graph with:
        graph.add_node("browser", playwright_agent_node)
    """
    from app.tools.playwright_tool import run_browser

    meta: dict = state.get("metadata", {})
    url: str = meta.get("browser_url", "")
    actions: list = meta.get("browser_actions", [])
    session_id: str | None = meta.get("browser_session") or None

    if not url:
        state["error"] = "playwright_agent_node: no browser_url in metadata"
        return state

    steps: list[dict] = state.get("intermediate_steps", [])
    result = await run_browser(url=url, actions=actions, session_id=session_id)

    steps.append(
        BaseGraph.make_step(
            step="browser",
            tool="playwright",
            input_data={"url": url, "actions": actions},
            output_data={
                "success": result.success,
                "current_url": result.current_url,
                "hitl_needed": result.hitl_needed,
                "data_keys": list(result.data.keys()),
                "error": result.error,
            },
        )
    )

    meta["browser_result"] = {
        "success": result.success,
        "data": result.data,
        "hitl_needed": result.hitl_needed,
        "reason": result.reason,
        "session_id": result.session_id,
        "current_url": result.current_url,
        "error": result.error,
    }

    state["metadata"] = meta
    state["intermediate_steps"] = steps

    if result.hitl_needed:
        state["hitl_needed"] = True
        state["hitl_reason"] = result.reason
        state["hitl_data"] = {
            "session_id": result.session_id,
            "current_url": result.current_url,
            "screenshot_b64": result.screenshot_b64,
        }

    if result.error and not result.hitl_needed:
        state["error"] = result.error

    return state


# ─── BaseGraph ────────────────────────────────────────────────────────────────

class BaseGraph(ABC):
    """
    Abstract base for all LangGraph-based agents in this project.

    Subclasses must implement build_graph() which returns a compiled
    LangGraph CompiledGraph. The run() method handles:
      - invoking the graph with an initial AgentState
      - persisting the TaskResult to the database
      - catching all exceptions so the API never crashes
      - logging every intermediate step

    Browser automation example
    --------------------------
    To use Playwright inside a subclass graph:

        from app.agents.base import playwright_agent_node

        g = StateGraph(AgentState)
        g.add_node("browser", playwright_agent_node)
        g.add_node("parse",   self._parse_node)
        g.set_entry_point("browser")
        g.add_edge("browser", "parse")
        g.add_edge("parse", END)
        return g.compile()

    Or with the ToolNode variant for tool-calling LLMs:

        from app.agents.base import build_playwright_tool_node
        g.add_node("browser_tools", build_playwright_tool_node())
    """

    def __init__(self) -> None:
        self._graph = self.build_graph()

    @abstractmethod
    def build_graph(self):
        """Build and return a compiled LangGraph graph."""

    def compile(self):
        """Return the compiled graph (useful for inspection / testing)."""
        return self._graph

    async def run(self, initial_state: AgentState) -> AgentState:
        """
        Execute the agent graph and persist the result.

        Parameters
        ----------
        initial_state : Pre-populated AgentState with at least input and
                        task_type.

        Returns
        -------
        AgentState — final state after all nodes have executed (or after
                     an error has been captured).
        """
        state: AgentState = {
            "intermediate_steps": [],
            "hitl_needed": False,
            "hitl_reason": None,
            "hitl_data": None,
            "error": None,
            "output": None,
            "client_id": None,
            "metadata": {},
            **initial_state,
        }

        task_type = state.get("task_type", "unknown")
        client_id = state.get("client_id")
        start_ms = time.monotonic()

        try:
            logger.info("BaseGraph.run: starting task_type=%s", task_type)
            state = await self._graph.ainvoke(state)
            duration_ms = int((time.monotonic() - start_ms) * 1000)

            for step in state.get("intermediate_steps", []):
                logger.debug("  step: %s", step)

            if state.get("hitl_needed"):
                logger.info(
                    "BaseGraph.run: HITL triggered — reason: %s",
                    state.get("hitl_reason"),
                )

            await self._save_result(
                task_type=task_type,
                client_id=client_id,
                prompt=state.get("input"),
                result={
                    "output": state.get("output"),
                    "metadata": state.get("metadata"),
                    "hitl_needed": state.get("hitl_needed"),
                    "hitl_session_id": (
                        state.get("hitl_data", {}) or {}
                    ).get("session_id"),
                },
                status="hitl_pending" if state.get("hitl_needed") else "success",
                duration_ms=duration_ms,
            )
            logger.info(
                "BaseGraph.run: completed task_type=%s in %dms", task_type, duration_ms
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_ms) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error(
                "BaseGraph.run: error in task_type=%s — %s", task_type, error_msg
            )
            state["error"] = error_msg

            await self._save_result(
                task_type=task_type,
                client_id=client_id,
                prompt=state.get("input"),
                result=None,
                status="failed",
                duration_ms=duration_ms,
                error_message=error_msg,
            )

        return state

    # ─── Private helpers ──────────────────────────────────────────────────────

    async def _save_result(
        self,
        task_type: str,
        client_id: int | None,
        prompt: str | None,
        result: dict | None,
        status: str,
        duration_ms: int,
        error_message: str | None = None,
    ) -> None:
        """Persist a TaskResult row to the database."""
        try:
            async with AsyncSessionLocal() as session:
                record = TaskResult(
                    task_type=task_type,
                    client_id=client_id,
                    prompt=prompt,
                    result=result,
                    status=status,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    completed_at=datetime.now(timezone.utc),
                )
                session.add(record)
                await session.commit()
                logger.debug("BaseGraph: saved TaskResult id=%s", record.id)
        except Exception as db_exc:
            logger.error("BaseGraph: failed to save TaskResult: %s", db_exc)

    @staticmethod
    def make_step(
        step: str,
        tool: str,
        input_data: Any,
        output_data: Any,
    ) -> dict:
        """
        Helper for building a structured intermediate_step entry.

        Usage inside a node:
            state["intermediate_steps"].append(
                BaseGraph.make_step("extract", "ask_llm", prompt, raw_json)
            )
        """
        return {
            "step": step,
            "tool": tool,
            "input": input_data,
            "output": output_data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }