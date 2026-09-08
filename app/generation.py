from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import AppError


@dataclass
class GeneratedAnswer:
    text: str
    citation_numbers: list[int]
    model: str


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...
    def generate(self, question: str, evidence: list[dict[str, Any]]) -> GeneratedAnswer: ...


class ExtractiveLLM:
    model_name = "extractive-mvp"

    def generate(self, question: str, evidence: list[dict[str, Any]]) -> GeneratedAnswer:
        if not evidence or evidence[0].get("score", 0) <= 0:
            return GeneratedAnswer("当前文档无法支持该结论。", [], self.model_name)
        parts = []
        citations = []
        for number, chunk in enumerate(evidence[:3], 1):
            text = re.split(r"(?<=[。！？.!?])\s*", chunk["text"].strip())[0]
            if text:
                parts.append(f"{text} [C{number}]")
                citations.append(number)
        answer = "\n\n".join(parts) if parts else "当前文档无法支持该结论。"
        return GeneratedAnswer(answer, citations, self.model_name)


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings):
        if not settings.llm_base_url or not settings.llm_model:
            raise ValueError("OpenAI-compatible provider requires base URL and model")
        self.settings = settings
        self.model_name = settings.llm_model

    def generate(self, question: str, evidence: list[dict[str, Any]]) -> GeneratedAnswer:
        blocks = "\n\n".join(
            f"[C{number}] (第 {chunk['page_number']} 页)\n{chunk['text']}"
            for number, chunk in enumerate(evidence, 1)
        )
        prompt = (
            "只能依据下列证据回答。每个事实句必须用 [C数字] 引用。"
            "证据不足时只回答“当前文档无法支持该结论”。\n\n"
            f"问题：{question}\n\n证据：\n{blocks}"
        )
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"} if self.settings.llm_api_key else {}
        try:
            response = httpx.post(
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": self.settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=self.settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise AppError("MODEL_CALL_FAILED", "模型调用失败", 502, True) from exc
        numbers = [int(value) for value in re.findall(r"\[C(\d+)]", text)]
        return GeneratedAnswer(text, numbers, self.model_name)


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "extractive":
        return ExtractiveLLM()
    if settings.llm_provider == "openai-compatible":
        return OpenAICompatibleLLM(settings)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
