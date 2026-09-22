# DeepDoc Agent

DeepDoc Agent 是一个引用优先、具有显式预算和终止条件的文档研究系统。1.0.0 增加 Production 运行基线：PostgreSQL/pgvector、S3 对象存储、OIDC 租户身份、RLS、数据库持久任务、独立 Worker、OpenTelemetry 接入、Kubernetes 扩缩容和供应链工作流。开发模式仍可零外部依赖运行。

默认开发模式不依赖外部模型或向量服务：元数据、Agent Checkpoint 和向量写入 SQLite，Multi-Agent LangGraph 检查点另存于同目录的 `*-langgraph.sqlite` 文件。Embedding 使用可复现的 Hashing Provider，规划、分析和核验采用确定性策略，回答使用抽取式模型。因此项目可直接在本地和 CI 中运行。Production 模式则切换到 PostgreSQL/pgvector、S3、OIDC、外部模型和独立 Worker；各边界仍可继续替换为 Qdrant、Redis 或托管消息系统。

## 功能

- `embedded|durable` 两种任务模式；生产 API 只入队，摄取、Agent 和 Eval 由带租约、重试和死信状态的 Worker 执行。
- 创建知识库、重建索引、Agent Run 和 Eval Run 支持 `Idempotency-Key`；生产环境强制提供，重复请求返回首次资源且请求体变化返回 409。
- PostgreSQL 生产 Schema、pgvector 扩展、租户列与强制 RLS；SQLite 继续作为本地开发适配器。
- 开发使用确定性 Hashing Embedding；生产强制配置 OpenAI-compatible Embedding，并把 256 维向量写入带 HNSW 索引的 pgvector 列。
- 本地/S3 对象存储适配器，OIDC JWT 验证与 Tenant Context，评测管理权限支持 `admin|eval-admin` 角色。
- PostgreSQL LangGraph Checkpointer、SSE 持久事件续传和幂等终态保护。
- OpenTelemetry OTLP Trace 接入点和 Live/Ready/Startup 三类探针。
- Docker Compose 生产拓扑与 Kubernetes API、Worker、隔离 Eval Worker、PDB、HPA、KEDA、NetworkPolicy 清单。
- CI 测试/文档/镜像构建，以及带 SBOM 与 Build Provenance 的发布工作流。
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

当前 Eval 的 Claim 覆盖只做字面匹配，不等同语义正确性或 Faithfulness。固定 LLM Judge、人工裁决和真实费用价格表仍未接入；这些字段不会伪装为已通过。Production 模式会使用独立 Eval Worker，但正式发布仍需按设计方案完成 Holdout、语义 Judge 与人工校准。

开发默认仍由 FastAPI 后台任务执行；设置 `DEEPDOC_TASK_MODE=durable` 后，任务和领域对象在同一数据库事务中提交，再由独立 Worker 领取。当前 Verifier 是确定性原文匹配，不是语义事实核验模型；生产质量门禁目标仍须用真实模型和授权数据校准。

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

当前 Hashing Embedding、确定性 Planner/Evaluator/Verifier 和抽取式生成器仅用于开发、测试和工作流验证，不等同于生产级模型。Production 配置会强制使用外部 LLM 与 Embedding，并启用 PostgreSQL、S3、OIDC 和独立 Worker；受控 Web Search Provider、Redis 缓存/限流、语义核验模型和托管队列仍属于按业务规模选配的外部集成。

## Docker

```powershell
docker compose up --build
```

数据保存在 `deepdoc-data` Volume。

## Production 运行基线

安装生产依赖：

```powershell
pip install -e ".[production]"
```

本地验证生产拓扑可使用 [compose.production.yaml](compose.production.yaml)。它启动 PostgreSQL/pgvector、MinIO、一次性迁移、API 和独立 Worker；必须先配置 `.env` 中的数据库密码、MinIO、OIDC、模型和 OTLP 地址：

```powershell
docker compose -f compose.production.yaml config
docker compose -f compose.production.yaml up --build
```

生产配置采用 fail-closed：`DEEPDOC_ENV=production` 时若未配置 PostgreSQL URL、`durable` 任务模式、S3 Bucket、OIDC issuer/audience/JWKS、外部模型或 OTLP Endpoint，进程拒绝启动。API 启动时不自动迁移，部署前单独运行：

```powershell
python -m app.migrate
python -m app.worker
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Kubernetes 清单和上线前要求位于 [deploy/kubernetes](deploy/kubernetes/README.md)。清单中的镜像必须替换成 CI 产生的不可变 digest，Secret 不得提交到仓库。迁移 Job、API、常规 Worker 和 Eval Worker 使用独立工作负载；API 至少三副本，Worker 由数据库队列深度通过 KEDA 扩缩容。

这一版本提供可运行的 Production 基线，不代表无需环境验收即可上线。正式接收流量前仍需完成真实 PostgreSQL/S3/OIDC/OTel 集成测试、外部模型 Eval、负载测试、NetworkPolicy 出口白名单、备份恢复与 RPO/RTO 演练。当前数据库队列适合中等规模；如果队列吞吐或隔离需求超过基线，应通过 ADR 迁移到托管消息系统，同时保留相同的任务幂等与租约语义。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest

$testFiles = Get-ChildItem -LiteralPath .\tests -Filter 'validate-*.ps1'
foreach ($testFile in $testFiles) { & $testFile.FullName }
```

测试覆盖 Eval 数据集校验、快照拒绝、三路径适配、规则指标、门禁、配对比较、令牌保护及既有单/Multi-Agent、RAG 与旧 API 兼容性。

## 设计文档

- [Production 版本设计方案](docs/production-design.md)
- [Eval 版本设计方案](docs/eval-design.md)
- [Multi-Agent 版本设计方案](docs/multi-agent-design.md)
- [Agent 版本设计方案](docs/agent-design.md)
- [RAG 版本设计方案](docs/rag-design.md)
- [MVP 设计方案](docs/mvp-design.md)
- [总体技术方案](docs/technical-solution.md)
