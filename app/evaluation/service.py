from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from app.agents.service import AgentService
from app.config import Settings
from app.errors import AppError
from app.evaluation.metrics import (
    aggregate_results, evaluate_gate, paired_comparison, score_case,
)
from app.evaluation.models import EvalDatasetCreate, EvalRunCreate
from app.evaluation.repository import EvalRepository, encode
from app.repository import Repository
from app.services import QAService

logger = logging.getLogger(__name__)


class EvalService:
    def __init__(self, settings: Settings, repository: Repository,
                 eval_repository: EvalRepository, qa: QAService,
                 agent: AgentService):
        self.settings = settings
        self.repository = repository
        self.eval_repository = eval_repository
        self.qa = qa
        self.agent = agent

    def create_dataset(self, body: EvalDatasetCreate) -> dict[str, Any]:
        ids = sorted({kb for case in body.cases for kb in case.knowledge_base_ids})
        snapshot, allowed_chunks = self._corpus_snapshot(ids)
        for case in body.cases:
            scope = set().union(*(allowed_chunks[kb] for kb in case.knowledge_base_ids))
            gold = {chunk for group in case.gold_evidence_sets for chunk in group}
            citations = set(case.expected_citation_chunk_ids)
            if not gold <= scope or not citations <= scope:
                raise AppError(
                    "INVALID_GOLD_EVIDENCE", "标注证据不属于案例知识库范围", 400
                )
            if citations and not citations <= gold:
                raise AppError(
                    "INVALID_GOLD_CITATION", "预期引用必须属于 Gold Evidence", 400
                )
        cases = [case.model_dump(mode="json") for case in body.cases]
        content = {"name": body.name, "version": body.version,
                   "split": body.split, "description": body.description,
                   "cases": cases, "corpus_snapshot": snapshot}
        digest = hashlib.sha256(encode(content).encode("utf-8")).hexdigest()
        return self.eval_repository.create_dataset(
            name=body.name, version=body.version, split=body.split,
            description=body.description, content_sha256=digest,
            corpus_snapshot=snapshot, cases=cases,
        )

    def _corpus_snapshot(self, knowledge_base_ids: list[str]
                         ) -> tuple[dict[str, Any], dict[str, set[str]]]:
        snapshot: dict[str, Any] = {}
        allowed_chunks: dict[str, set[str]] = {}
        for kb_id in knowledge_base_ids:
            kb = self.repository.get_knowledge_base(kb_id)
            documents = self.repository.list_knowledge_base_documents(kb_id)
            chunks = self.repository.get_knowledge_base_chunks(kb_id)
            if not chunks:
                raise AppError("KNOWLEDGE_BASE_NOT_READY", "评测知识库没有可用 Chunk", 409)
            doc_entries = sorted(
                ({"id": doc["id"], "sha256": doc["sha256"],
                  "status": doc["status"]} for doc in documents),
                key=lambda item: item["id"],
            )
            chunk_entries = sorted(
                ({"id": chunk["id"], "document_id": chunk["document_id"],
                  "page_number": chunk["page_number"],
                  "text_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()}
                 for chunk in chunks), key=lambda item: item["id"]
            )
            snapshot[kb_id] = {
                "index_version": kb["active_index_version"],
                "documents": doc_entries,
                "chunks_sha256": hashlib.sha256(
                    encode(chunk_entries).encode("utf-8")
                ).hexdigest(),
            }
            allowed_chunks[kb_id] = {chunk["id"] for chunk in chunks}
        return snapshot, allowed_chunks

    def create_run(self, body: EvalRunCreate) -> dict[str, Any]:
        dataset = self.eval_repository.get_dataset(body.dataset_id)
        config = {
            "dataset_sha256": dataset["content_sha256"],
            "corpus_snapshot": dataset["corpus_snapshot"],
            "app_version": "0.5.0",
            "git_commit": os.getenv("DEEPDOC_BUILD_COMMIT", "UNKNOWN"),
            "llm_provider": self.settings.llm_provider,
            "llm_model": self.settings.llm_model or self.qa.llm.model_name,
            "embedding_dimensions": self.settings.embedding_dimensions,
            "retrieval": {"dense_top_k": self.settings.dense_top_k,
                          "sparse_top_k": self.settings.sparse_top_k,
                          "rerank_top_k": self.settings.rerank_top_k,
                          "rrf_k": self.settings.rrf_k},
            "graph_versions": {"single": "agent-v1", "multi": "multi-v1"},
            "gates": body.gates.model_dump(),
            "evaluator_version": "rules-v1",
            "actual_cost_available": False,
        }
        return self.eval_repository.create_run(dataset["id"], body.mode, config)

    def execute(self, run_id: str) -> None:
        run = self.eval_repository.get_run(run_id)
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return
        dataset = self.eval_repository.get_dataset(run["dataset_id"])
        try:
            current_snapshot, _ = self._corpus_snapshot(
                sorted(dataset["corpus_snapshot"])
            )
            if current_snapshot != dataset["corpus_snapshot"]:
                raise AppError("CORPUS_CHANGED", "数据集绑定的文档或索引已变化", 409)
            self.eval_repository.set_running(run_id)
            existing = {item["case_id"] for item in self.eval_repository.list_case_results(run_id)}
            for case in dataset["cases"]:
                if self.eval_repository.get_run(run_id)["cancellation_requested"]:
                    break
                if case["case_id"] in existing:
                    continue
                start = time.perf_counter()
                try:
                    artifact = self._execute_case(case, run["mode"])
                    case_status = artifact["status"]
                    error_code = artifact.get("error_code")
                except AppError as exc:
                    case_status, error_code = "FAILED", exc.code
                    artifact = self._failed_artifact(case, exc.code)
                except Exception:
                    logger.exception("evaluation_case_failed", extra={
                        "run_id": run_id, "case_id": case["case_id"]
                    })
                    case_status, error_code = "FAILED", "EVAL_CASE_FAILED"
                    artifact = self._failed_artifact(case, error_code)
                duration_ms = int((time.perf_counter() - start) * 1000)
                artifact["duration_ms"] = duration_ms
                artifact["tags"] = case["tags"]
                metrics = score_case(case, artifact)
                self.eval_repository.save_case_result(
                    run_id, case["case_id"], status=case_status,
                    artifact=artifact, metrics=metrics,
                    error_code=error_code, duration_ms=duration_ms,
                )
            results = self.eval_repository.list_case_results(run_id)
            summary = aggregate_results(results, len(dataset["cases"]))
            gate = evaluate_gate(summary, results, run["config"]["gates"])
            cancelled = self.eval_repository.get_run(run_id)["cancellation_requested"]
            self.eval_repository.complete_run(
                run_id, "CANCELLED" if cancelled else "COMPLETED", summary, gate
            )
        except AppError as exc:
            logger.warning("evaluation_run_rejected: %s", exc.code)
            self.eval_repository.complete_run(
                run_id, "FAILED", {"error_code": exc.code},
                {"status": "INSUFFICIENT_DATA", "reason": exc.code},
            )
        except Exception:
            logger.exception("evaluation_run_failed", extra={"run_id": run_id})
            self.eval_repository.complete_run(
                run_id, "FAILED", {"error_code": "EVAL_RUN_FAILED"},
                {"status": "INSUFFICIENT_DATA", "reason": "EVAL_RUN_FAILED"},
            )

    @staticmethod
    def _failed_artifact(case: dict[str, Any], code: str) -> dict[str, Any]:
        return {"status": "FAILED", "answer": "", "retrieved": [],
                "citations": [], "execution_mode": None, "tool_names": [],
                "error_code": code, "tags": case["tags"]}

    def _execute_case(self, case: dict[str, Any], mode: str) -> dict[str, Any]:
        if mode == "rag":
            return self._run_rag(case)
        return self._run_agent(case, mode)

    def _run_rag(self, case: dict[str, Any]) -> dict[str, Any]:
        if len(case["knowledge_base_ids"]) != 1:
            return {"status": "NOT_APPLICABLE", "reason": "RAG_REQUIRES_ONE_KB",
                    "answer": "", "retrieved": [], "citations": [],
                    "execution_mode": "rag", "tool_names": []}
        kb_id = case["knowledge_base_ids"][0]
        qa_run_id = None
        error_code = None
        for block in self.qa.answer_knowledge_base_events(kb_id, case["question"]):
            event = next((line[7:] for line in block.splitlines()
                          if line.startswith("event: ")), "")
            data_line = next((line[6:] for line in block.splitlines()
                              if line.startswith("data: ")), "{}")
            data = json.loads(data_line)
            if event == "metadata":
                qa_run_id = data["run_id"]
            elif event == "error":
                error_code = data["code"]
        if qa_run_id is None:
            raise AppError("QA_RUN_MISSING", "RAG 未返回运行 ID", 500)
        qa_run = self.repository.get_qa_run(qa_run_id)
        chunks = {item["id"]: item for item in
                  self.repository.get_knowledge_base_chunks(kb_id)}
        retrieved = []
        for hit in qa_run["retrieval_trace"].get("hits", []):
            chunk = chunks.get(hit["chunk_id"])
            if chunk:
                retrieved.append({"chunk_id": chunk["id"],
                                  "document_id": chunk["document_id"],
                                  "page_number": chunk["page_number"],
                                  "text": chunk["text"]})
        return {"status": qa_run["status"], "answer": qa_run["answer"],
                "retrieved": retrieved, "citations": qa_run["citations"],
                "execution_mode": "rag", "source_run_id": qa_run_id,
                "tool_names": ["knowledge_base.search"],
                "error_code": error_code or qa_run["error_code"],
                "input_chars": qa_run["input_chars"],
                "output_chars": qa_run["output_chars"],
                "prompt_tokens": qa_run["prompt_tokens"],
                "completion_tokens": qa_run["completion_tokens"]}

    def _run_agent(self, case: dict[str, Any], mode: str) -> dict[str, Any]:
        created = self.agent.create_run(
            question=case["question"],
            knowledge_base_ids=case["knowledge_base_ids"],
            allow_web_search=False, output_format="research_brief",
            budget_overrides={}, execution_mode=mode,
        )
        self.agent.execute(created["id"])
        result = self.agent.agent_repository.get_run(created["id"])
        retrieved = [{"chunk_id": item["chunk_id"],
                      "document_id": item["document_id"],
                      "page_number": item["page_number"], "text": item["text"]}
                     for item in result["evidence"]]
        with self.agent.agent_repository.database.connect() as db:
            tools = [row[0] for row in db.execute(
                "SELECT tool_name FROM agent_tool_calls WHERE run_id = ?",
                (created["id"],),
            ).fetchall()]
        return {"status": result["status"], "answer": result["answer"],
                "retrieved": retrieved, "citations": result["citations"],
                "execution_mode": result["execution_mode"],
                "source_run_id": created["id"], "tool_names": tools,
                "error_code": result["error_code"], "agent_usage": result["usage"],
                "prompt_tokens": result["usage"].get("prompt_tokens"),
                "completion_tokens": result["usage"].get("completion_tokens")}

    def compare(self, candidate_id: str, baseline_id: str) -> dict[str, Any]:
        candidate = self.eval_repository.get_run(candidate_id)
        baseline = self.eval_repository.get_run(baseline_id)
        if candidate["dataset_id"] != baseline["dataset_id"]:
            raise AppError("DATASET_MISMATCH", "只能比较同一数据集版本", 409)
        if candidate["status"] != "COMPLETED" or baseline["status"] != "COMPLETED":
            raise AppError("EVAL_RUN_NOT_COMPLETE", "评测运行尚未完成", 409)
        return {
            "candidate_id": candidate_id, "baseline_id": baseline_id,
            "dataset_id": candidate["dataset_id"],
            "candidate_mode": candidate["mode"], "baseline_mode": baseline["mode"],
            **paired_comparison(
                self.eval_repository.list_case_results(candidate_id),
                self.eval_repository.list_case_results(baseline_id),
            ),
        }
