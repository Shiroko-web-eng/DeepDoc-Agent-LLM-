from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.agents.graph import AgentGraph
from app.agents.multi_graph import MultiAgentGraph
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
        self.multi_graph = MultiAgentGraph(self.tools, llm, agent_repository)

    def create_run(self, *, question: str, knowledge_base_ids: list[str],
                   allow_web_search: bool, output_format: str,
                   budget_overrides: dict[str, Any],
                   execution_mode: str = "auto") -> dict[str, Any]:
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
            "max_subtasks": min(
                budget_overrides.get("max_subtasks")
                or self.settings.multi_agent_max_subtasks, 8,
            ),
            "max_parallel_agents": min(
                budget_overrides.get("max_parallel_agents")
                or self.settings.multi_agent_max_parallel, 4,
            ),
        }
        mode, reason = self.multi_graph.choose_mode(
            execution_mode, question, knowledge_base_ids,
            self.settings.multi_agent_enabled,
        )
        return self.agent_repository.create_run(
            question=question, knowledge_base_ids=knowledge_base_ids,
            allow_web_search=allow_web_search, output_format=output_format,
            budget=budget, execution_mode=mode, route_reason=reason,
        )

    def execute(self, run_id: str, resume: bool = False) -> None:
        run = self.agent_repository.get_run(run_id)
        if run["execution_mode"] == "multi":
            self._execute_multi(run, resume)
            return
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

    def _execute_multi(self, run: dict[str, Any], resume: bool) -> None:
        run_id = run["id"]
        initial = {
            "run_id": run_id, "question": run["question"],
            "knowledge_base_ids": run["knowledge_base_ids"],
            "budget": run["budget"], "started_epoch": time.time(),
            "tasks": [], "results": [], "evidence": [], "verified_claims": [],
            "citations": [], "answer": "", "status": "RUNNING",
            "phase": "queued", "usage": {}, "error_code": None,
        }
        self.agent_repository.append_event(run_id, "run.started", {"resume": resume})
        checkpoint_path = self.settings.database_path.with_name(
            self.settings.database_path.stem + "-langgraph.sqlite"
        )
        previous_version = int(run["state_version"])
        last_state = initial
        emitted_tasks: set[str] = set()
        try:
            with closing(sqlite3.connect(checkpoint_path, check_same_thread=False)) as conn:
                saver = SqliteSaver(
                    conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[])
                )
                graph = self.multi_graph.builder.compile(checkpointer=saver)
                config = {"configurable": {"thread_id": run_id},
                          "max_concurrency": run["budget"]["max_parallel_agents"],
                          "recursion_limit": 20}
                graph_input = initial
                if resume:
                    checkpoint = graph.get_state(config)
                    if checkpoint.next and checkpoint.values.get("run_id") == run_id:
                        graph_input = None
                    elif checkpoint.values:
                        # A completed graph must start on a fresh thread: the
                        # results reducer would otherwise retain the old run.
                        config["configurable"]["thread_id"] = (
                            f"{run_id}-resume-{previous_version}"
                        )
                for state in graph.stream(graph_input, config=config, stream_mode="values",
                                          durability="sync"):
                    last_state = state
                    if state.get("phase") == "queued":
                        continue
                    phase = ("researching" if state["phase"] == "planned"
                             and state.get("results") else state["phase"])
                    previous_version += 1
                    snapshot = {**state, "state_version": previous_version,
                                "current_node": phase}
                    self.agent_repository.save_checkpoint(run_id, snapshot)
                    self.agent_repository.update_from_state(run_id, snapshot)
                    if phase == "planned":
                        for task in state["tasks"]:
                            self.agent_repository.save_task(run_id, task)
                        self.agent_repository.append_event(
                            run_id, "task.delegated", {"count": len(state["tasks"])}
                        )
                    if state.get("results"):
                        for result in state["results"]:
                            task = next((task for task in state["tasks"]
                                         if task["task_id"] == result["task_id"]), None)
                            if task:
                                self.agent_repository.save_task(run_id, {
                                    **task, "status": result["status"], "result": result,
                                    "error_code": result.get("error_code"),
                                })
                                if result["task_id"] not in emitted_tasks:
                                    emitted_tasks.add(result["task_id"])
                                    self.agent_repository.append_event(
                                        run_id, f"task.{result['status'].lower()}",
                                        {"task_id": result["task_id"],
                                         "agent_type": result["agent_type"]},
                                    )
                    self.agent_repository.append_event(
                        run_id, f"multi.{phase}",
                        {"status": state["status"],
                         "completed_tasks": len(state.get("results", []))},
                    )
                if last_state["status"] in {"COMPLETED", "PARTIAL", "INSUFFICIENT",
                                             "CANCELLED", "BUDGET_EXCEEDED"}:
                    self.agent_repository.append_event(
                        run_id, f"run.{last_state['status'].lower()}",
                        {"status": last_state["status"]},
                    )
        except Exception:
            logger.exception("multi_agent_run_failed", extra={"run_id": run_id})
            failed = {**last_state, "status": "FAILED", "error_code": "MULTI_AGENT_FAILED",
                      "answer": "Multi-Agent 运行失败。", "current_node": "failed",
                      "state_version": previous_version + 1}
            self.agent_repository.update_from_state(run_id, failed)
            self.agent_repository.save_checkpoint(run_id, failed)
            self.agent_repository.append_event(
                run_id, "run.failed", {"code": "MULTI_AGENT_FAILED"}
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
