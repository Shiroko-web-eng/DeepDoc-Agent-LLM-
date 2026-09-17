# DeepDoc Agent

DeepDoc Agent 是一个引用优先、具有显式预算和终止条件的文档研究 Agent。0.3.0 版本在混合 RAG 基础上加入 LangGraph 工作流、Planner、知识库与计算工具、Evidence Evaluator、检查点、取消/恢复、报告生成和引用验证。

默认开发模式不依赖外部模型或向量服务：元数据、Agent Checkpoint 和向量写入 SQLite，Embedding 使用可复现的 Hashing Provider，Planner/Evaluator 使用确定性策略，回答使用抽取式模型。因此项目可直接在本地和 CI 中运行。检索、规划、工具和模型均通过独立边界接入，后续可以替换为 PostgreSQL、Qdrant、Redis、生产模型和外部工具。

## 功能

- LangGraph 单 Agent 显式状态图。
- Guard、Classifier、Planner、Retriever、Tool Executor、Evaluator、Report、Validator。
- Agent Run、计划、Evidence、事件和 Checkpoint 持久化。
- 节点、时长、检索轮次和工具次数预算。
- 无进展检测、有限重试和确定终止。
- Calculator 安全 AST 工具与知识库检索工具。
- SSE 运行事件、Last-Event-ID 续传、取消和恢复。
- PDF、DOCX、TXT、Markdown 上传和解析。
- 知识库与多文档管理。
- Query Normalize 和规则 Query Rewrite。
- BM25 稀疏检索与稠密向量检索。
- RRF Fusion、重复候选合并和轻量 Reranker。
- 检索分数、通道排名和索引版本诊断。
- 跨文档 SSE 问答和文档名、页码、原文引用。
- 可恢复文档处理、幂等上传和可重建索引。
- 保留 MVP 单文档 API 的向后兼容性。

当前 Web Search 工具已注册但默认不可用；在配置受控 Provider、域名策略和 SSRF 防护前，Agent 不会执行外部网页检索。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 使用 OpenAPI 页面。根路径 `http://127.0.0.1:8000/` 会自动跳转到 API 文档。

### PyCharm

项目内置两个共享运行配置：

- `DeepDoc Agent Server`：使用项目 `.venv` 启动 Uvicorn。
- `All Tests`：运行全部 pytest 测试。

在 PyCharm Terminal 首次执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

随后在右上角选择 `DeepDoc Agent Server` 并运行。

## 快速体验 Agent

先按下方 RAG 步骤创建知识库并上传至少一份文档，然后创建 Agent Run：

```powershell
$body = @{
  question = "比较差旅报销和采购报销的期限差异，并计算期限相差多少天"
  knowledge_base_ids = @($kb.id)
  allow_web_search = $false
  output_format = "comparison_report"
  budget = @{
    max_duration_seconds = 45
    max_nodes = 20
    max_retrieval_rounds = 3
    max_tool_calls = 8
  }
} | ConvertTo-Json -Depth 4

$run = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/agent/runs `
  -ContentType application/json `
  -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/agent/runs/$($run.id)"
```

也可以通过以下地址读取 SSE 事件：

```text
GET /v1/agent/runs/{run_id}/events
```

## 快速体验 RAG

### 1. 创建知识库

```powershell
$kb = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/knowledge-bases `
  -ContentType application/json `
  -Body '{"name":"产品文档","description":"RAG 演示知识库"}'
```

### 2. 上传多份文档

推荐通过 Swagger 页面调用 `POST /v1/knowledge-bases/{knowledge_base_id}/documents`。上传完成后可调用：

- `GET /v1/knowledge-bases/{id}` 查看文档数和活动索引版本。
- `GET /v1/knowledge-bases/{id}/documents` 查看解析状态。

### 3. 检索诊断

调用 `POST /v1/knowledge-bases/{id}/search`：

```json
{
  "question": "报销期限有什么规定？",
  "limit": 8
}
```

响应包含 Dense、Sparse 排名与分数、RRF 分数、Reranker 分数、文档名、页码和文本片段。

### 4. 跨文档问答

调用 `POST /v1/knowledge-bases/{id}/questions`：

```json
{
  "question": "两份制度的报销期限有什么区别？",
  "document_ids": null
}
```

响应为 SSE，事件包括 `metadata`、`answer_delta`、`citation`、`usage`、`error` 和 `done`。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/agent/runs` | 创建并执行 Agent Run |
| `GET` | `/v1/agent/runs/{id}` | 查询状态、计划、Evidence、报告和用量 |
| `GET` | `/v1/agent/runs/{id}/events` | 读取可续传 SSE 事件 |
| `POST` | `/v1/agent/runs/{id}/cancel` | 幂等请求取消 |
| `POST` | `/v1/agent/runs/{id}/resume` | 从检查点恢复失败或取消任务 |
| `GET` | `/v1/agent/runs/{id}/steps` | 查询计划步骤 |
| `GET` | `/v1/agent/runs/{id}/evidence` | 查询引用证据 |
| `GET` | `/v1/agent/tools` | 查询工具和可用状态 |
| `POST` | `/v1/knowledge-bases` | 创建知识库 |
| `GET` | `/v1/knowledge-bases` | 查询知识库 |
| `POST` | `/v1/knowledge-bases/{id}/documents` | 上传知识库文档 |
| `GET` | `/v1/knowledge-bases/{id}/documents` | 查询知识库文档 |
| `POST` | `/v1/knowledge-bases/{id}/search` | 混合检索诊断 |
| `POST` | `/v1/knowledge-bases/{id}/questions` | 多文档 RAG 问答 |
| `POST` | `/v1/knowledge-bases/{id}/reindex` | 重建活动索引 |
| `GET` | `/v1/index-jobs/{id}` | 查询索引任务 |
| `GET` | `/v1/qa-runs/{id}` | 查询回答与引用 |
| `GET` | `/health/live` | 存活检查 |
| `GET` | `/health/ready` | 就绪检查 |

原有 `/v1/documents` 和 `/v1/documents/{id}/questions` 继续映射到默认知识库。

## 模型配置

默认配置：

```text
DEEPDOC_LLM_PROVIDER=extractive
DEEPDOC_EMBEDDING_DIMENSIONS=256
DEEPDOC_DENSE_TOP_K=40
DEEPDOC_SPARSE_TOP_K=40
DEEPDOC_RERANK_TOP_K=12
DEEPDOC_FINAL_CONTEXT_CHUNKS=8
DEEPDOC_RRF_K=60
DEEPDOC_AGENT_MAX_DURATION_SECONDS=45
DEEPDOC_AGENT_MAX_NODES=20
DEEPDOC_AGENT_MAX_RETRIEVAL_ROUNDS=3
DEEPDOC_AGENT_MAX_TOOL_CALLS=8
```

使用 OpenAI-compatible 生成服务时设置：

```text
DEEPDOC_LLM_PROVIDER=openai-compatible
DEEPDOC_LLM_MODEL=your-model
DEEPDOC_LLM_BASE_URL=https://provider.example/v1
DEEPDOC_LLM_API_KEY=...
```

当前 Hashing Embedding、确定性 Planner/Evaluator 和抽取式生成器用于开发、测试和工作流验证，不等同于生产级模型。上线前应依据 [Agent 设计方案](docs/agent-design.md) 接入生产模型、持久化 Checkpointer、Worker 队列、PostgreSQL/Qdrant/Redis 和受控 Web Search Provider。

## Docker

```powershell
docker compose up --build
```

数据保存在 `deepdoc-data` Volume。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest

$testFiles = Get-ChildItem -LiteralPath .\tests -Filter 'validate-*.ps1'
foreach ($testFile in $testFiles) { & $testFile.FullName }
```

测试覆盖 LangGraph 路由、Planner、预算终止、Calculator 安全、Checkpoint、SSE 续传、取消恢复、解析、DOCX、Embedding、BM25、RRF、知识库隔离、跨文档引用和旧 API 兼容性。

## 设计文档

- [Agent 版本设计方案](docs/agent-design.md)
- [RAG 版本设计方案](docs/rag-design.md)
- [MVP 设计方案](docs/mvp-design.md)
- [总体技术方案](docs/technical-solution.md)
