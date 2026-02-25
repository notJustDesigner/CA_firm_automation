"""
app/agents/base.py
Base agent infrastructure: AgentState TypedDict + BaseGraph orchestrator.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, TypedDict

from sqlalchemy import select

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


# ─── BaseGraph ────────────────────────────────────────────────────────────────

class BaseGraph(ABC):
    """
    Abstract base for all LangGraph-based agents in this project.

    Subclasses must implement `build_graph()` which returns a compiled
    LangGraph `CompiledGraph`.  The `run()` method handles:
      - invoking the graph with an initial AgentState
      - persisting the TaskResult to the database
      - catching all exceptions so the API never crashes due to an agent error
      - logging every intermediate step
    """

    def __init__(self) -> None:
        self._graph = self.build_graph()

    @abstractmethod
    def build_graph(self):
        """
        Build and return a compiled LangGraph graph.

        Example:
            from langgraph.graph import StateGraph, END
            g = StateGraph(AgentState)
            g.add_node("step1", self._step1)
            g.set_entry_point("step1")
            g.add_edge("step1", END)
            return g.compile()
        """

    def compile(self):
        """Return the compiled graph (useful for inspection / testing)."""
        return self._graph

    async def run(self, initial_state: AgentState) -> AgentState:
        """
        Execute the agent graph and persist the result.

        Parameters
        ----------
        initial_state : Pre-populated AgentState with at least `input` and
                        `task_type`.

        Returns
        -------
        AgentState — final state after all nodes have executed (or after
                     an error has been captured).
        """
        # Ensure required defaults are present
        state: AgentState = {
            "intermediate_steps": [],
            "hitl_needed": False,
            "hitl_reason": None,
            "hitl_data": None,
            "error": None,
            "output": None,
            "client_id": None,
            "metadata": {},
            **initial_state,  # caller values override defaults
        }

        task_type = state.get("task_type", "unknown")
        client_id = state.get("client_id")
        start_ms = time.monotonic()

        try:
            logger.info("BaseGraph.run: starting task_type=%s", task_type)
            state = await self._graph.ainvoke(state)
            duration_ms = int((time.monotonic() - start_ms) * 1000)

            # Log all intermediate steps
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
                result={"output": state.get("output"), "metadata": state.get("metadata")},
                status="success",
                duration_ms=duration_ms,
            )
            logger.info(
                "BaseGraph.run: completed task_type=%s in %dms", task_type, duration_ms
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_ms) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("BaseGraph.run: error in task_type=%s — %s", task_type, error_msg)
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
            # Never let a DB write failure crash the agent response
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