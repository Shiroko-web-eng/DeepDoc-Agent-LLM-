from __future__ import annotations

import ast
import operator
import time
from dataclasses import dataclass
from typing import Any

from app.agents.repository import AgentRepository
from app.errors import AppError
from app.services import QAService


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    permission: str
    available: bool = True


class CalculatorTool:
    spec = ToolSpec(
        name="calculator.evaluate",
        version="1.0.0",
        description="计算受限的算术表达式",
        permission="compute:basic",
    )
    _binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def execute(self, expression: str) -> dict[str, Any]:
        if not expression or len(expression) > 200:
            raise AppError("INVALID_CALCULATION", "算术表达式为空或过长", 400)
        try:
            tree = ast.parse(expression, mode="eval")
            value = self._evaluate(tree.body)
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
            raise AppError("INVALID_CALCULATION", "算术表达式不合法", 400) from exc
        if isinstance(value, float) and not (-1e100 < value < 1e100):
            raise AppError("CALCULATION_OUT_OF_RANGE", "计算结果超出范围", 400)
        return {"expression": expression, "value": value}

    def _evaluate(self, node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._unary:
            return self._unary[type(node.op)](self._evaluate(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in self._binary:
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("exponent is too large")
            return self._binary[type(node.op)](left, right)
        raise ValueError("unsupported expression")


class KnowledgeBaseTool:
    spec = ToolSpec(
        name="knowledge_base.search",
        version="1.0.0",
        description="在允许的知识库中执行混合检索",
        permission="kb:read",
    )

    def __init__(self, qa: QAService):
        self.qa = qa

    def execute(self, query: str, knowledge_base_ids: list[str],
                limit_per_base: int = 6) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for knowledge_base_id in knowledge_base_ids:
            _, hits, index_version = self.qa.search(
                knowledge_base_id, query, limit=limit_per_base
            )
            for hit in hits:
                if hit["id"] in seen:
                    continue
                seen.add(hit["id"])
                merged.append({
                    "evidence_id": f"ev_{len(merged) + 1:03d}",
                    "source_type": "knowledge_base",
                    "knowledge_base_id": knowledge_base_id,
                    "index_version": index_version,
                    "chunk_id": hit["id"],
                    "document_id": hit["document_id"],
                    "filename": hit["filename"],
                    "page_number": hit["page_number"],
                    "quote": hit["text"][:1000],
                    "text": hit["text"],
                    "score": hit["rerank_score"],
                    "rrf_score": hit["rrf_score"],
                })
        return sorted(merged, key=lambda item: (-item["score"], item["evidence_id"]))


class ToolRegistry:
    def __init__(self, qa: QAService, agent_repository: AgentRepository):
        self.kb = KnowledgeBaseTool(qa)
        self.calculator = CalculatorTool()
        self.repository = agent_repository
        self.web_spec = ToolSpec(
            name="web.search", version="1.0.0",
            description="检索公开网页；开发配置中未连接 Provider",
            permission="web:read", available=False,
        )

    def specs(self) -> list[ToolSpec]:
        return [self.kb.spec, self.calculator.spec, self.web_spec]

    def calculate(self, run_id: str, step_id: str, expression: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = self.calculator.execute(expression)
            self.repository.record_tool_call(
                run_id=run_id, step_id=step_id, tool_name=self.calculator.spec.name,
                arguments={"expression": expression}, status="SUCCEEDED", result=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        except AppError as exc:
            self.repository.record_tool_call(
                run_id=run_id, step_id=step_id, tool_name=self.calculator.spec.name,
                arguments={"expression": expression}, status="FAILED", error_code=exc.code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
