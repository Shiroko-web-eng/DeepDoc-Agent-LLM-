from dataclasses import replace

import httpx

from app.generation import OpenAICompatibleLLM
from tests.integration.test_agent_api import make_settings


def test_provider_reports_actual_tokens_without_estimating_missing_usage(tmp_path, monkeypatch):
    settings = replace(make_settings(tmp_path), llm_provider="openai-compatible",
                       llm_model="test-model", llm_base_url="https://model.example/v1")
    client = OpenAICompatibleLLM(settings)
    payloads = [
        {"choices": [{"message": {"content": "期限为30天 [C1]"}}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
        {"choices": [{"message": {"content": "期限为30天 [C1]"}}],
         "usage": {"prompt_tokens": "12", "completion_tokens": -1}},
        {"choices": [{"message": {"content": "期限为30天 [C1]"}}],
         "usage": None},
    ]

    def fake_post(url, **kwargs):
        return httpx.Response(200, json=payloads.pop(0), request=httpx.Request("POST", url))

    monkeypatch.setattr("app.generation.httpx.post", fake_post)
    evidence = [{"page_number": 1, "text": "期限为30天。"}]
    first = client.generate("期限？", evidence)
    assert (first.prompt_tokens, first.completion_tokens) == (12, 7)
    second = client.generate("期限？", evidence)
    assert (second.prompt_tokens, second.completion_tokens) == (None, None)
    third = client.generate("期限？", evidence)
    assert (third.prompt_tokens, third.completion_tokens) == (None, None)
