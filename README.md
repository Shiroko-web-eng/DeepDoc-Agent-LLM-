# DeepDoc Agent

DeepDoc Agent 是一个引用优先、具有显式预算和终止条件的文档研究系统。0.5.0 版本增加本地离线 Eval：不可变数据集版本、RAG/单 Agent/Multi-Agent 运行、规则指标、配对比较与门禁。此前的 Multi-Agent 研究图继续保留。

默认开发模式不依赖外部模型或向量服务：元数据、Agent Checkpoint 和向量写入 SQLite，Multi-Agent LangGraph 检查点另存于同目录的 `*-langgraph.sqlite` 文件。Embedding 使用可复现的 Hashing Provider，规划、分析和核验采用确定性策略，回答使用抽取式模型。因此项目可直接在本地和 CI 中运行。检索、规划、工具和模型均通过独立边界接入，后续可替换为 PostgreSQL、Qdrant、Redis、生产模型和外部工具。

## 功能

- 版本化 Eval 数据集与知识库索引/文档/Chunk 哈希快照；快照变化时拒绝复用旧基准。
- RAG、单 Agent、Multi-Agent 三路径离线评测；原始运行 Artifact、错误与案例指标持久化。
- Recall@5、MRR@5、nDCG@5、引用有效性、Gold 引用覆盖、字面 Claim 覆盖、拒答、路由和规则任务成功率。
- P50/P95 延迟、Provider 实际 Token 用量（有返回时）、配对 bootstrap 置信区间及可配置门禁。
- 评测接口以独立管理令牌保护；未配置令牌时默认关闭。
- `auto|single|multi` 执行模式；跨知识库复杂研究自动走 Multi-Agent，简单任务保留单 Agent 快速路径。
- Supervisor 将知识库研究拆成隔离子任务，通过 LangGraph `Send` 并行执行，按任务 ID 汇合去重。
- Research 子图负责检索与 Claim 提取，Verifier 子图负责 Claim/原文匹配与引用筛选。
- Multi-Agent 子任务状态、运行事件、预算与持久 SQLite 图检查点。
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

当前 Eval 的 Claim 覆盖只做字面匹配，不等同语义正确性或 Faithfulness。固定 LLM Judge、人工裁决、真实费用价格表、独立 Worker 和 CI 发布门禁尚未接入；这些字段不会伪装为已通过。评测用 SQLite 与 FastAPI 后台任务适合本地开发，不能作为多用户生产 Eval 服务。

本地 Multi-Agent 版本仍由 FastAPI 后台任务执行；持久图检查点不等于独立 Worker 队列。进程意外退出后，目前不自动领取并继续未完成 Run。生产部署前须按 [Multi-Agent 设计方案](docs/multi-agent-design.md) 补齐独立 Worker、任务租约、Outbox、跨进程幂等和 PostgreSQL Checkpointer。当前 Verifier 是确定性原文匹配，不是语义事实核验模型；Benchmark 目标尚未达成。

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

### Multi-Agent 跨知识库研究

至少创建两个已上传文档的知识库，然后调用现有 Run API：

```powershell
$body = @{
  question = "比较两套制度的审批流程和期限差异"
  knowledge_base_ids = @($travelKb.id, $purchaseKb.id)
  execution_mode = "multi"
  output_format = "comparison_report"
  budget = @{ max_subtasks = 4; max_parallel_agents = 2; max_tool_calls = 4 }
} | ConvertTo-Json -Depth 4

$run = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/agent/runs `
  -ContentType application/json -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/agent/runs/$($run.id)"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/agent/runs/$($run.id)/tasks"
```

`execution_mode` 默认 `auto`：仅跨知识库且包含比较、分析、研究等复杂意图时自动启用 Multi-Agent；也可明确指定 `single`。`DEEPDOC_MULTI_AGENT_ENABLED=false` 会禁用 Multi-Agent 路由。多任务预算不足时 Run 会以 `BUDGET_EXCEEDED` 结束，而不会悄悄跳过知识库。

## 本地离线 Eval

先准备至少一个已上传文档的知识库，并从检索结果或 `GET /v1/knowledge-bases/{id}/search` 获取真实 `chunk_id`。启动服务前设置仅供评测管理员使用的令牌；不要把它提交到 Git：

```powershell
$env:DEEPDOC_EVAL_ADMIN_TOKEN = "replace-with-a-long-random-secret"
uvicorn app.main:app --reload
```

在另一个终端创建不可变数据集版本并运行基准（将变量换成实际值）：

```powershell
$headers = @{ "X-Eval-Token" = "replace-with-a-long-random-secret" }
$datasetBody = @{
  name = "expense-regression"
  version = "1.0.0"
  split = "regression"
  cases = @(@{
    case_id = "travel-001"
    task_type = "fact"
    question = "差旅报销期限是什么？"
    knowledge_base_ids = @($kb.id)
    gold_evidence_sets = @(@($chunkId))
    expected_citation_chunk_ids = @($chunkId)
    required_claims = @("30天")
  })
} | ConvertTo-Json -Depth 8
$dataset = Invoke-RestMethod -Method Post -Headers $headers `
  -Uri http://127.0.0.1:8000/v1/evaluations/datasets `
  -ContentType application/json -Body $datasetBody
$runBody = @{ dataset_id = $dataset.id; mode = "rag"; gates = @{
  min_recall_at_5 = 0.85; min_rule_task_success = 0.85
} } | ConvertTo-Json -Depth 5
$run = Invoke-RestMethod -Method Post -Headers $headers `
  -Uri http://127.0.0.1:8000/v1/evaluations/runs `
  -ContentType application/json -Body $runBody
Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8000/v1/evaluations/runs/$($run.id)"
Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8000/v1/evaluations/runs/$($run.id)/cases"
```

`mode` 可选 `rag`、`single`、`multi`。RAG 遇到跨知识库用例记为 `NOT_APPLICABLE`，不计零分。对同一数据集分别运行两种模式后，可调用 `GET /v1/evaluations/runs/{candidate_id}/comparison?baseline={baseline_id}` 查看配对差异与 95% bootstrap 区间。`POST /v1/evaluations/runs/{id}/cancel` 可请求中断，重新执行已完成运行不会覆盖案例结果。数据集绑定当前语料快照，文档或索引变动后应发布新数据集版本。

CI 中可对已发布的数据集直接执行规则门禁；只有运行完成且门禁为 `PASS` 时命令返回退出码 0，其他状态返回 1：

```powershell
python -m app.evaluation.cli --dataset-id $dataset.id --mode rag --min-recall-at-5 0.85 --min-rule-task-success 0.85
```

Eval 接口仅用管理令牌隔离评测数据，现有普通知识库接口并没有租户级 ACL；含敏感 Gold 的数据集应只在受控本地环境使用。`gate.status` 只有 `PASS` 才表示配置的规则阈值满足；`NOT_CONFIGURED` 与 `INSUFFICIENT_DATA` 都不是放行结论。实际费用和语义 Faithfulness 当前为 `null`，要求实际费用的门禁会返回 `INSUFFICIENT_DATA`。

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
| `GET` | `/v1/agent/runs/{id}/tasks` | 查询 Multi-Agent 子任务与结果 |
| `GET` | `/v1/agent/runs/{id}/tasks/{task_id}` | 查询单个子任务 |
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
DEEPDOC_MULTI_AGENT_ENABLED=true
DEEPDOC_MULTI_AGENT_MAX_SUBTASKS=4
DEEPDOC_MULTI_AGENT_MAX_PARALLEL=2
DEEPDOC_EVAL_ADMIN_TOKEN=
```

使用 OpenAI-compatible 生成服务时设置：

```text
DEEPDOC_LLM_PROVIDER=openai-compatible
DEEPDOC_LLM_MODEL=your-model
DEEPDOC_LLM_BASE_URL=https://provider.example/v1
DEEPDOC_LLM_API_KEY=...
```

当前 Hashing Embedding、确定性 Planner/Evaluator/Verifier 和抽取式生成器用于开发、测试和工作流验证，不等同于生产级模型。上线前应依据 [Agent 设计方案](docs/agent-design.md) 与 [Multi-Agent 设计方案](docs/multi-agent-design.md) 接入生产模型、独立 Worker 队列、PostgreSQL/Qdrant/Redis 和受控 Web Search Provider。

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

测试覆盖 Eval 数据集校验、快照拒绝、三路径适配、规则指标、门禁、配对比较、令牌保护及既有单/Multi-Agent、RAG 与旧 API 兼容性。

## 设计文档

- [Eval 版本设计方案](docs/eval-design.md)
- [Multi-Agent 版本设计方案](docs/multi-agent-design.md)
- [Agent 版本设计方案](docs/agent-design.md)
- [RAG 版本设计方案](docs/rag-design.md)
- [MVP 设计方案](docs/mvp-design.md)
- [总体技术方案](docs/technical-solution.md)
