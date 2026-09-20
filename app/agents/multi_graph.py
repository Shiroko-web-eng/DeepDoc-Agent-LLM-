from __future__ import annotations

import operator
import re
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.repository import AgentRepository
from app.agents.tools import ToolRegistry
from app.generation import LLMClient


class ResearchState(TypedDict, total=False):
    task: dict[str, Any]
    evidence: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    status: str
    error_code: str | None


class VerifyState(TypedDict, total=False):
    evidence: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    verified_claims: list[dict[str, Any]]


class MultiState(TypedDict, total=False):
    run_id: str
    question: str
    knowledge_base_ids: list[str]
    budget: dict[str, Any]
    started_epoch: float
    tasks: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    results: Annotated[list[dict[str, Any]], operator.add]
    evidence: list[dict[str, Any]]
    verified_claims: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    answer: str
    status: str
    error_code: str | None
    phase: str
    usage: dict[str, Any]


class MultiAgentGraph:
    """Supervisor graph with isolated research and verification subgraphs."""

    def __init__(self, tools: ToolRegistry, llm: LLMClient,
                 repository: AgentRepository):
        self.tools = tools
        self.llm = llm
        self.repository = repository

        research = StateGraph(ResearchState)
        research.add_node("retrieve", self.retrieve)
        research.add_node("analyze", self.analyze)
        research.add_edge(START, "retrieve")
        research.add_edge("retrieve", "analyze")
        research.add_edge("analyze", END)
        self.research_agent = research.compile()

        verifier = StateGraph(VerifyState)
        verifier.add_node("check_claims", self.check_claims)
        verifier.add_edge(START, "check_claims")
        verifier.add_edge("check_claims", END)
        self.verifier_agent = verifier.compile()

        graph = StateGraph(MultiState)
        graph.add_node("supervisor_plan", self.plan)
        graph.add_node("research_agent", self.run_research)
        graph.add_node("join", self.join)
        graph.add_node("verifier_agent", self.verify)
        graph.add_node("report", self.report)
        graph.add_edge(START, "supervisor_plan")
        graph.add_conditional_edges("supervisor_plan", self.dispatch)
        graph.add_edge("research_agent", "join")
        graph.add_edge("join", "verifier_agent")
        graph.add_edge("verifier_agent", "report")
        graph.add_edge("report", END)
        self.builder = graph

    @staticmethod
    def choose_mode(requested: str, question: str,
                    knowledge_base_ids: list[str], enabled: bool) -> tuple[str, str]:
        if requested == "single":
            return "single", "requested_single"
        if not enabled:
            return "single", "multi_agent_disabled"
        if requested == "multi":
            return "multi", "requested_multi"
        if len(knowledge_base_ids) >= 2 and any(
            token in question for token in ("比较", "对比", "差异", "研究", "分析", "综合", "冲突")
        ):
            return "multi", "cross_knowledge_base_research"
        return "single", "simple_fast_path"

    def plan(self, state: MultiState) -> dict[str, Any]:
        budget = state["budget"]
        max_subtasks = int(budget["max_subtasks"])
        expression = self.extract_expression(state["question"])
        desired_count = len(state["knowledge_base_ids"]) + int(expression is not None)
        if self.repository.is_cancel_requested(state["run_id"]):
            return {"tasks": [], "plan": [], "phase": "planned", "status": "CANCELLED"}
        if (desired_count > max_subtasks or desired_count > budget["max_tool_calls"]
                or desired_count + 4 > budget["max_nodes"]):
            return {"tasks": [], "plan": [], "phase": "planned",
                    "status": "BUDGET_EXCEEDED", "error_code": "BUDGET_EXCEEDED"}
        tasks = []
        for knowledge_base_id in state["knowledge_base_ids"]:
            tasks.append({
                "task_id": f"t{len(tasks) + 1}", "agent_type": "research",
                "objective": state["question"],
                "knowledge_base_id": knowledge_base_id,
                "status": "PENDING", "depends_on": [],
                "success_criteria": ["获得可回查的文档证据"],
            })
        if expression:
            tasks.append({
                "task_id": f"t{len(tasks) + 1}", "agent_type": "calculation",
                "objective": expression,
                "knowledge_base_id": state["knowledge_base_ids"][0],
                "status": "PENDING", "depends_on": [],
                "success_criteria": ["得出受限算术结果"],
            })
        return {"tasks": tasks, "plan": tasks, "phase": "planned", "status": "RUNNING"}

    @staticmethod
    def extract_expression(question: str) -> str | None:
        match = re.search(
            r"(?<!\w)(-?\d+(?:\.\d+)?(?:\s*[-+*/%]\s*-?\d+(?:\.\d+)?)+)",
            question,
        )
        return match.group(1) if match else None

    @staticmethod
    def dispatch(state: MultiState) -> list[Send] | str:
        if state["status"] in {"CANCELLED", "BUDGET_EXCEEDED"}:
            return "report"
        return [Send("research_agent", {
            "run_id": state["run_id"], "task": task,
            "started_epoch": state["started_epoch"], "budget": state["budget"],
        }) for task in state["tasks"]]

    def run_research(self, state: dict[str, Any]) -> dict[str, Any]:
        task = state["task"]
        run_id = state["run_id"]
        budget = state["budget"]
        if self.repository.is_cancel_requested(run_id):
            result = {"task_id": task["task_id"], "status": "CANCELLED",
                      "agent_type": task["agent_type"], "evidence": [], "claims": [],
                      "error_code": "RUN_CANCELLED"}
        elif time.time() - state["started_epoch"] > budget["max_duration_seconds"]:
            result = {"task_id": task["task_id"], "status": "TIMED_OUT",
                      "agent_type": task["agent_type"], "evidence": [], "claims": [],
                      "error_code": "BUDGET_EXCEEDED"}
        elif task["agent_type"] == "calculation":
            try:
                value = self.tools.calculate(run_id, task["task_id"], task["objective"])
                result = {"task_id": task["task_id"], "status": "SUCCEEDED",
                          "agent_type": "calculation", "evidence": [], "claims": [],
                          "calculation": value}
            except Exception:
                result = {"task_id": task["task_id"], "status": "FAILED",
                          "agent_type": "calculation", "evidence": [], "claims": [],
                          "error_code": "CALCULATION_FAILED"}
        else:
            try:
                substate = self.research_agent.invoke({"task": task})
                result = {"task_id": task["task_id"],
                          "agent_type": "research", "status": substate["status"],
                          "evidence": substate.get("evidence", []),
                          "claims": substate.get("claims", []),
                          "error_code": substate.get("error_code")}
            except Exception:
                result = {"task_id": task["task_id"], "status": "FAILED",
                          "agent_type": "research", "evidence": [], "claims": [],
                          "error_code": "RESEARCH_FAILED"}
        return {"results": [result]}

    def retrieve(self, state: ResearchState) -> dict[str, Any]:
        task = state["task"]
        evidence = self.tools.kb.execute(
            task["objective"], [task["knowledge_base_id"]], limit_per_base=6
        )
        return {"evidence": evidence, "status": "SUCCEEDED" if evidence else "INSUFFICIENT"}

    @staticmethod
    def analyze(state: ResearchState) -> dict[str, Any]:
        task = state["task"]
        claims = []
        for item in state.get("evidence", [])[:4]:
            sentence = re.split(r"(?<=[。！？.!?])\s*", item["text"].strip())[0]
            if sentence:
                claims.append({
                    "claim_id": f"{task['task_id']}:{item['chunk_id']}",
                    "text": sentence, "chunk_id": item["chunk_id"],
                    "task_id": task["task_id"],
                })
        return {"claims": claims}

    @staticmethod
    def join(state: MultiState) -> dict[str, Any]:
        by_task = {result["task_id"]: result for result in state.get("results", [])}
        evidence_by_chunk: dict[str, dict[str, Any]] = {}
        claims = []
        for task_id in sorted(by_task):
            result = by_task[task_id]
            for item in result.get("evidence", []):
                evidence_by_chunk.setdefault(item["chunk_id"], item)
            claims.extend(result.get("claims", []))
        evidence = list(evidence_by_chunk.values())
        for number, item in enumerate(evidence, 1):
            item["evidence_id"] = f"ev_{number:03d}"
        return {"evidence": evidence, "verified_claims": claims, "phase": "joined"}

    def verify(self, state: MultiState) -> dict[str, Any]:
        checked = self.verifier_agent.invoke({
            "evidence": state.get("evidence", []),
            "claims": state.get("verified_claims", []),
        })
        return {"verified_claims": checked["verified_claims"], "phase": "verified"}

    @staticmethod
    def check_claims(state: VerifyState) -> dict[str, Any]:
        evidence = {item["chunk_id"]: item for item in state["evidence"]}
        verified = []
        for claim in state["claims"]:
            item = evidence.get(claim["chunk_id"])
            status = ("supported" if item and claim["text"] in item["quote"]
                      else "unknown")
            verified.append({**claim, "verification": status,
                             "evidence_id": item["evidence_id"] if item else None})
        return {"verified_claims": verified}

    def report(self, state: MultiState) -> dict[str, Any]:
        results = {result["task_id"]: result for result in state.get("results", [])}
        succeeded = [result for result in results.values() if result["status"] == "SUCCEEDED"]
        supported_ids = {claim["evidence_id"] for claim in state["verified_claims"]
                         if claim["verification"] == "supported"}
        evidence = [item for item in state["evidence"]
                    if item["evidence_id"] in supported_ids][:8]
        citations: list[dict[str, Any]] = []
        answer = "现有证据不足以完成该研究任务。"
        status = "INSUFFICIENT"
        error_code = "INSUFFICIENT_EVIDENCE"
        prompt_tokens = None
        completion_tokens = None
        if state.get("status") == "BUDGET_EXCEEDED":
            status, error_code, answer = (
                "BUDGET_EXCEEDED", "BUDGET_EXCEEDED", "任务已达到运行预算上限。"
            )
        elif state.get("status") == "CANCELLED" or self.repository.is_cancel_requested(state["run_id"]):
            status, error_code, answer = "CANCELLED", "RUN_CANCELLED", "任务已取消。"
        elif time.time() - state["started_epoch"] > state["budget"]["max_duration_seconds"]:
            status, error_code, answer = (
                "BUDGET_EXCEEDED", "BUDGET_EXCEEDED", "任务已达到运行预算上限。"
            )
        elif evidence:
            generated = self.llm.generate(state["question"], evidence)
            prompt_tokens = generated.prompt_tokens
            completion_tokens = generated.completion_tokens
            numbers = list(dict.fromkeys(generated.citation_numbers))
            if numbers and all(1 <= number <= len(evidence) for number in numbers):
                for number in numbers:
                    item = evidence[number - 1]
                    citations.append({key: item[key] for key in (
                        "evidence_id", "chunk_id", "document_id", "filename",
                        "page_number", "quote"
                    )})
            if citations:
                answer = generated.text
                status = "COMPLETED" if len(succeeded) == len(state["tasks"]) else "PARTIAL"
                error_code = None
            else:
                error_code = "CITATION_VALIDATION_FAILED"
        for result in results.values():
            calculation = result.get("calculation")
            if calculation and status in {"COMPLETED", "PARTIAL"}:
                operands = re.findall(r"\d+(?:\.\d+)?", calculation["expression"])
                source_text = "\n".join(item["text"] for item in evidence)
                if all(operand in source_text for operand in operands):
                    answer += f"\n\n计算结果：{calculation['expression']} = {calculation['value']}。"
                else:
                    status = "PARTIAL"
                    error_code = "UNVERIFIED_CALCULATION"
        usage = {"duration_ms": int((time.time() - state["started_epoch"]) * 1000),
                 "nodes": 4 + len(results), "tool_calls": len(results),
                 "subtasks": len(state["tasks"]),
                 "parallel_limit": state["budget"]["max_parallel_agents"],
                 "prompt_tokens": prompt_tokens,
                 "completion_tokens": completion_tokens}
        return {"answer": answer, "citations": citations, "status": status,
                "error_code": error_code, "usage": usage, "phase": "finished"}
