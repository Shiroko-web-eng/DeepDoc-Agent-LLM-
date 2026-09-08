# DeepDoc Agent MVP

一个引用优先的单文档问答 MVP，支持 PDF、TXT、Markdown 上传、异步解析、关键词片段选择、SSE 问答和原文引用。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 使用 OpenAPI 页面。默认 `extractive` 模型不需要外部 API，适合开发和测试。

使用 OpenAI-compatible 服务时设置：

```text
DEEPDOC_LLM_PROVIDER=openai-compatible
DEEPDOC_LLM_MODEL=your-model
DEEPDOC_LLM_BASE_URL=https://provider.example/v1
DEEPDOC_LLM_API_KEY=...
```

## Docker

```powershell
docker compose up --build
```

## 测试

```powershell
pytest
powershell -File tests/validate-docs.ps1
powershell -File tests/validate-mvp-doc.ps1
```

详细边界和设计见 [MVP 设计方案](docs/mvp-design.md)。
