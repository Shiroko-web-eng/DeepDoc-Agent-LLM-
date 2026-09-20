from __future__ import annotations

import re
import time
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.repository import AgentRepository
from app.agents.state import AgentState, TERMINAL_STATUSES
from app.agents.tools import ToolRegistry
from app.generation import LLMClient


class AgentGraph:
    def __init__(self, tools: ToolRegistry, llm: LLMClient,
                 repository: AgentRepository):
        self.tools = tools
        self.llm = llm
        self.repository = repository
        builder = StateGraph(AgentState)
        builder.add_node("guard", self.guard)
        builder.add_node("classify", self.classify)
        builder.add_node("plan", self.plan)
        builder.add_node("retrieve", self.retrieve)
        builder.add_node("tool_executor", self.tool_executor)
        builder.add_node("evaluate", self.evaluate)
        builder.add_node("report", self.report)
        builder.add_node("validate", self.validate)
        builder.add_node("finalize", self.finalize)
        builder.add_edge(START, "guard")
        builder.add_conditional_edges(
            "guard", self.route_guard, {"continue": "classify", "finalize": "finalize"}
        )
        builder.add_edge("classify", "plan")
        builder.add_edge("plan", "retrieve")
        builder.add_edge("retrieve", "tool_executor")
        builder.add_edge("tool_executor", "evaluate")
        builder.add_conditional_edges(
            "evaluate", self.route_evaluate,
            {"retry": "retrieve", "report": "report", "finalize": "finalize"},
        )
        builder.add_edge("report", "validate")
        builder.add_conditional_edges(
            "validate", self.route_validate,
            {"repair": "report", "finalize": "finalize"},
        )
        builder.add_edge("finalize", END)
        self.compiled = builder.compile(checkpointer=InMemorySaver())

    def _tick(self, state: AgentState, node: str) -> dict[str, Any]:
        budget = dict(state.get("budget", {}))
        budget["nodes_used"] = int(budget.get("nodes_used", 0)) + 1
        status = state.get("status", "RUNNING")
        if status not in TERMINAL_STATUSES:
            if self.repository.is_cancel_requested(state["run_id"]):
                status = "CANCELLED"
            elif budget["nodes_used"] > int(budget["max_nodes"]):
                status = "BUDGET_EXCEEDED"
            elif time.time() - float(state["started_epoch"]) > float(
                budget["max_duration_seconds"]
            ):
                status = "BUDGET_EXCEEDED"
        return {
            "budget": budget,
            "status": status,
            "current_node": node,
            "state_version": int(state.get("state_version", 0)) + 1,
            "last_event": {"type": f"node.{node}", "node": node, "status": status},
        }

    def guard(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "guard")
        if update["status"] in TERMINAL_STATUSES:
            return update
        question = state["question"].strip()
        if not question:
            update.update(status="REFUSED", error_code="EMPTY_QUESTION")
        elif len(question) > 4000:
            update.update(status="REFUSED", error_code="QUESTION_TOO_LONG")
        else:
            update["status"] = "RUNNING"
        return update

    @staticmethod
    def route_guard(state: AgentState) -> Literal["continue", "finalize"]:
        return "finalize" if state.get("status") in TERMINAL_STATUSES else "continue"

    def classify(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "classify")
        if update["status"] in TERMINAL_STATUSES:
            return update
        question = state["question"]
        if any(word in question for word in ("比较", "区别", "差异", "对比")):
            task_type = "COMPARISON"
        elif re.search(r"\d\s*[-+*/%]\s*\d", question):
            task_type = "CALCULATION"
        elif any(word in question for word in ("总结", "概括", "要点")):
            task_type = "SUMMARY"
        elif any(word in question for word in ("分析", "原因", "综合", "研究")):
            task_type = "MULTI_HOP"
        else:
            task_type = "SIMPLE_QA"
        update.update(task_type=task_type, status="RUNNING")
        return update

    def plan(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "plan")
        if update["status"] in TERMINAL_STATUSES:
            return update
        task_type = state["task_type"]
        steps = [{
            "id": "s1", "kind": "retrieve", "status": "PENDING",
            "description": state["question"], "depends_on": [],
            "success_criteria": ["至少获得一个可引用证据"],
        }]
        if task_type == "COMPARISON":
            steps.append({
                "id": "s2", "kind": "synthesize", "status": "PENDING",
                "description": "按共同点和差异组织比较结果", "depends_on": ["s1"],
                "success_criteria": ["比较维度明确且引用双方证据"],
            })
        elif task_type == "CALCULATION":
            steps.append({
                "id": "s2", "kind": "calculator", "status": "PENDING",
                "description": "执行问题中的受限算术表达式", "depends_on": ["s1"],
                "success_criteria": ["结果可追溯到表达式"],
            })
        else:
            steps.append({
                "id": "s2", "kind": "synthesize", "status": "PENDING",
                "description": "基于证据生成带引用报告", "depends_on": ["s1"],
                "success_criteria": ["事实结论均带引用"],
            })
        update.update(plan=steps, status="RUNNING")
        return update

    def retrieve(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "retrieve")
        if update["status"] in TERMINAL_STATUSES:
            return update
        rounds = int(state.get("retrieval_rounds", 0)) + 1
        evidence = self.tools.kb.execute(
            state["question"], state["knowledge_base_ids"], limit_per_base=6
        )
        existing = {item["chunk_id"]: item for item in state.get("evidence", [])}
        for item in evidence:
            existing.setdefault(item["chunk_id"], item)
        merged = list(existing.values())
        for index, item in enumerate(merged, 1):
            item["evidence_id"] = f"ev_{index:03d}"
        plan = [dict(step) for step in state.get("plan", [])]
        if plan:
            plan[0]["status"] = "SUCCEEDED" if merged else "FAILED"
        update.update(
            evidence=merged, retrieval_rounds=rounds, plan=plan, status="RUNNING",
            last_event={"type": "evidence.added", "count": len(merged), "round": rounds},
        )
        return update

    def tool_executor(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "tool_executor")
        if update["status"] in TERMINAL_STATUSES:
            return update
        results = list(state.get("tool_results", []))
        calls = int(state.get("tool_calls", 0))
        expression = self._extract_expression(state["question"])
        if expression and calls < int(state["budget"]["max_tool_calls"]):
            result = self.tools.calculate(state["run_id"], "s2", expression)
            results.append({"tool": "calculator.evaluate", **result})
            calls += 1
        update.update(tool_results=results, tool_calls=calls, status="RUNNING")
        return update

    @staticmethod
    def _extract_expression(question: str) -> str | None:
        match = re.search(r"(?<!\w)(-?\d+(?:\.\d+)?(?:\s*[-+*/%]\s*-?\d+(?:\.\d+)?)+)", question)
        return match.group(1) if match else None

    def evaluate(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "evaluate")
        if update["status"] in TERMINAL_STATUSES:
            update["next_action"] = "finalize"
            return update
        evidence_count = len(state.get("evidence", []))
        previous_count = int(state.get("last_evidence_count", 0))
        rounds = int(state.get("retrieval_rounds", 0))
        max_rounds = int(state["budget"]["max_retrieval_rounds"])
        if evidence_count:
            action = "report"
        elif rounds < max_rounds and (rounds == 1 or evidence_count > previous_count):
            action = "retry"
        else:
            action = "finalize"
            update.update(status="INSUFFICIENT", error_code="INSUFFICIENT_EVIDENCE")
        update.update(
            next_action=action,
            last_evidence_count=evidence_count,
            last_event={
                "type": "evaluation.completed", "decision": action,
                "evidence_count": evidence_count,
            },
        )
        return update

    @staticmethod
    def route_evaluate(state: AgentState) -> Literal["retry", "report", "finalize"]:
        if state.get("status") in TERMINAL_STATUSES:
            return "finalize"
        return state.get("next_action", "finalize")  # type: ignore[return-value]

    def report(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "report")
        if update["status"] in TERMINAL_STATUSES:
            return update
        evidence = [{
            "id": item["chunk_id"], "text": item["text"],
            "page_number": item["page_number"], "filename": item["filename"],
            "score": item["score"], "document_id": item["document_id"],
        } for item in state.get("evidence", [])]
        generated = self.llm.generate(state["question"], evidence)
        answer = generated.text
        tool_results = state.get("tool_results", [])
        if tool_results:
            calculation = tool_results[-1]
            answer += (
                f"\n\n计算结果：{calculation['expression']} = {calculation['value']}。"
            )
        citations = []
        for number in dict.fromkeys(generated.citation_numbers):
            if 1 <= number <= len(state.get("evidence", [])):
                item = state["evidence"][number - 1]
                citations.append({
                    "evidence_id": item["evidence_id"],
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "filename": item["filename"],
                    "page_number": item["page_number"],
                    "quote": item["quote"],
                })
        update.update(
            answer=answer, citations=citations, status="RUNNING",
            model_usage={"prompt_tokens": generated.prompt_tokens,
                         "completion_tokens": generated.completion_tokens},
            last_event={"type": "report.generated", "characters": len(answer)},
        )
        return update

    def validate(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "validate")
        if update["status"] in TERMINAL_STATUSES:
            update["next_action"] = "finalize"
            return update
        attempts = int(state.get("validation_attempts", 0))
        if state.get("citations"):
            update.update(next_action="finalize", status="RUNNING")
        elif attempts < 1 and state.get("evidence"):
            update.update(next_action="repair", validation_attempts=attempts + 1)
        else:
            update.update(
                next_action="finalize", status="INSUFFICIENT",
                error_code="CITATION_VALIDATION_FAILED",
                answer="现有证据不足以生成通过引用校验的报告。",
            )
        return update

    @staticmethod
    def route_validate(state: AgentState) -> Literal["repair", "finalize"]:
        if state.get("status") in TERMINAL_STATUSES:
            return "finalize"
        return "repair" if state.get("next_action") == "repair" else "finalize"

    def finalize(self, state: AgentState) -> dict[str, Any]:
        update = self._tick(state, "finalize")
        status = update["status"]
        answer = state.get("answer", "")
        if status not in TERMINAL_STATUSES:
            status = "COMPLETED"
        if status == "CANCELLED" and not answer:
            answer = "任务已取消。"
        elif status == "BUDGET_EXCEEDED" and not answer:
            answer = "任务已达到运行预算上限。"
        elif status in {"INSUFFICIENT", "REFUSED"} and not answer:
            answer = "现有证据不足以完成该研究任务。"
        usage = {
            "duration_ms": int((time.time() - float(state["started_epoch"])) * 1000),
            "nodes": update["budget"]["nodes_used"],
            "retrieval_rounds": state.get("retrieval_rounds", 0),
            "tool_calls": state.get("tool_calls", 0),
            **state.get("model_usage", {}),
        }
        update.update(
            status=status, answer=answer, usage=usage,
            last_event={"type": f"run.{status.lower()}", "status": status},
        )
        return update
