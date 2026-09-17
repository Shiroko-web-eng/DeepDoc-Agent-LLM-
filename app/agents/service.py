from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from app.agents.graph import AgentGraph
from app.agents.repository import AgentRepository
from app.agents.state import AgentState
from app.agents.tools import ToolRegistry
from app.config import Settings
from app.errors import AppError
from app.generation import LLMClient
from app.repository import Repository
from app.services import QAService

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, settings: Settings, repository: Repository,
                 qa: QAService, llm: LLMClient, agent_repository: AgentRepository):
        self.settings = settings
        self.repository = repository
        self.agent_repository = agent_repository
        self.tools = ToolRegistry(qa, agent_repository)
        self.graph = AgentGraph(self.tools, llm, agent_repository)

    def create_run(self, *, question: str, knowledge_base_ids: list[str],
                   allow_web_search: bool, output_format: str,
                   budget_overrides: dict[str, Any]) -> dict[str, Any]:
        for knowledge_base_id in knowledge_base_ids:
            knowledge_base = self.repository.get_knowledge_base(knowledge_base_id)
            if not knowledge_base["document_count"]:
                raise AppError(
                    "KNOWLEDGE_BASE_NOT_READY", "知识库中没有可用文档", 409, True
                )
        budget = {
            "max_duration_seconds": min(
                budget_overrides.get("max_duration_seconds")
                or self.settings.agent_max_duration_seconds,
                120,
            ),
            "max_nodes": min(
                budget_overrides.get("max_nodes") or self.settings.agent_max_nodes, 32
            ),
            "max_retrieval_rounds": min(
                budget_overrides.get("max_retrieval_rounds")
                or self.settings.agent_max_retrieval_rounds,
                12,
            ),
            "max_tool_calls": min(
                budget_overrides.get("max_tool_calls")
                if budget_overrides.get("max_tool_calls") is not None
                else self.settings.agent_max_tool_calls,
                12,
            ),
            "nodes_used": 0,
        }
        return self.agent_repository.create_run(
            question=question, knowledge_base_ids=knowledge_base_ids,
            allow_web_search=allow_web_search, output_format=output_format,
            budget=budget,
        )

    def execute(self, run_id: str, resume: bool = False) -> None:
        run = self.agent_repository.get_run(run_id)
        initial = self.agent_repository.get_latest_checkpoint(run_id) if resume else None
        if initial:
            initial.update(
                status="RUNNING", error_code=None, started_epoch=time.time(),
                current_node="resume",
            )
        else:
            initial = self._initial_state(run)
        self.agent_repository.append_event(run_id, "run.started", {"resume": resume})
        last_version = -1
        final_state: AgentState = initial
        try:
            config = {"configurable": {"thread_id": run_id}}
            for state in self.graph.compiled.stream(
                initial, config=config, stream_mode="values"
            ):
                final_state = state
                version = int(state.get("state_version", 0))
                if version <= last_version:
                    continue
                last_version = version
                self.agent_repository.save_checkpoint(run_id, state)
                self.agent_repository.update_from_state(run_id, state)
                event = state.get("last_event", {})
                if event.get("type"):
                    self.agent_repository.append_event(
                        run_id, event["type"],
                        {key: value for key, value in event.items() if key != "type"},
                    )
        except Exception:
            logger.exception("agent_run_failed", extra={"run_id": run_id})
            final_state = {
                **final_state,
                "status": "FAILED",
                "error_code": "AGENT_RUN_FAILED",
                "answer": "Agent 运行失败。",
                "current_node": "failed",
                "state_version": int(final_state.get("state_version", 0)) + 1,
            }
            self.agent_repository.update_from_state(run_id, final_state)
            self.agent_repository.save_checkpoint(run_id, final_state)
            self.agent_repository.append_event(
                run_id, "run.failed", {"code": "AGENT_RUN_FAILED"}
            )

    def _initial_state(self, run: dict[str, Any]) -> AgentState:
        return {
            "run_id": run["id"],
            "question": run["question"],
            "knowledge_base_ids": run["knowledge_base_ids"],
            "allow_web_search": run["allow_web_search"],
            "output_format": run["output_format"],
            "status": "RUNNING",
            "task_type": "",
            "plan": [],
            "evidence": [],
            "tool_results": [],
            "answer": "",
            "citations": [],
            "budget": run["budget"],
            "usage": {},
            "retrieval_rounds": 0,
            "tool_calls": 0,
            "last_evidence_count": 0,
            "next_action": "",
            "validation_attempts": 0,
            "current_node": "queued",
            "state_version": 0,
            "last_event": {},
            "error_code": None,
            "started_epoch": time.time(),
        }

    def event_stream(self, run_id: str, after: int = 0) -> Iterator[str]:
        for event in self.agent_repository.list_events(run_id, after):
            yield (
                f"id: {event['sequence_number']}\n"
                f"event: {event['event_type']}\n"
                f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            )
