# DeepDoc Agent RAG 版本设计方案

## 1. 阶段定位

RAG 阶段在现有 MVP“上传、解析、关键词检索、受控回答、引用回查”的闭环上，升级为可评测、可版本化、可降级的检索增强生成系统。本阶段解决语义召回、跨文档检索、长文档上下文选择和检索质量量化，不提前引入任务规划、工具调用、Agent 状态图或 Multi-Agent 协作。

核心目标是：用户可在一个知识库内检索多份文档，系统通过查询改写、稠密与稀疏混合召回、RRF 融合、重排和上下文组装生成有依据的回答，并将每项事实映射到确定的文档版本、页码及原文片段。

### 1.1 建设范围

RAG 版本包含：

- 知识库与多文档问答。
- PDF、DOCX、TXT、Markdown 文档解析；PDF 和 DOCX 保留结构与页码信息。
- 结构感知、Token 感知的父子 Chunk。
- Embedding 生成、批处理、缓存、限流和索引版本管理。
- Query Normalize、Query Rewrite 和可选多查询扩展。
- BM25 稀疏召回与向量语义召回。
- Reciprocal Rank Fusion（RRF）、去重、多样性控制和 Reranker。
- 上下文预算、邻接片段扩展及父片段回填。
- 带文档版本、页码、片段和摘录的引用。
- 检索运行记录、离线 Benchmark 和质量回归门禁。
- PostgreSQL、Qdrant、Redis 的 Docker Compose 开发环境。
- 旧 MVP API 的兼容迁移和可回滚索引构建。

RAG 版本不包含：

- Planner、Tool Executor、Evaluator Agent 和循环状态图。
- Web Search、计算器及外部业务工具。
- Multi-Agent 通信、任务委派和共享记忆。
- LLM-as-Judge 为主的完整 Eval 平台；本阶段只建设 RAG 所需离线评测基线。
- OCR、图片理解、复杂表格推理和音视频内容解析。
- 多地域容灾、Kubernetes 自动扩缩容及企业级计费。

### 1.2 成功标准

| 目标 | 验收门槛 |
| --- | --- |
| 检索质量 | 标注集 Recall@5 ≥ 0.85，nDCG@10 相比 MVP 关键词基线提升 ≥ 15% |
| 引用质量 | Citation Precision ≥ 0.95，Citation Recall ≥ 0.90 |
| 忠实回答 | Faithfulness ≥ 0.90，不可回答准确率 ≥ 0.90 |
| 索引可靠性 | 验收集索引成功率 100%，重复提交不产生重复活动索引 |
| 在线性能 | 缓存未命中时检索 P95 ≤ 1.5 秒，端到端非流式首事件 P95 ≤ 3 秒 |
| 可恢复性 | 新索引失败不影响旧活动索引，支持按索引版本回滚 |
| 可维护性 | 单元、契约、集成、迁移和检索回归测试全部通过 |

以上数值是 RAG 阶段发布门槛，最终阈值应由真实业务语料的 Benchmark 校准，而不是只使用合成样本。

## 2. 关键架构决策

### 2.1 存储选型

本阶段推荐：

- PostgreSQL：知识库、文档、版本、页面、Chunk 元数据、索引任务、问答运行和引用的事实源。
- Qdrant：保存稠密向量、BM25 兼容稀疏向量及检索 Payload，执行过滤后的混合候选召回。
- Redis：Embedding 结果缓存、查询改写缓存、短期检索缓存、分布式锁和任务状态通知。
- 本地目录：开发环境保存原始文件；接口保持对象存储兼容，Production 阶段迁移 S3。

选择 Qdrant 作为本阶段默认检索引擎，是为了让稠密和稀疏候选共享过滤条件、索引版本和融合入口。系统同时定义 `VectorIndex`、`SparseIndex` 接口；若部署环境更重视单一数据库，可实现 pgvector 适配器，但一个环境只能启用一个活动后端，不做长期双写。

### 2.2 一致性原则

PostgreSQL 是元数据事实源，Qdrant 是可重建索引。任何索引记录均绑定：

```text
tenant_id + knowledge_base_id + document_version_id
+ parser_version + chunker_version + embedding_model_version
+ sparse_encoder_version + index_schema_version
```

索引构建采用“写入候选版本 → 完整性校验 → 原子激活”流程。问答请求只读取 `ACTIVE` 索引版本；构建失败或回滚时不修改已激活版本。

### 2.3 Agent 边界

Query Rewrite 是固定、有限、可超时的 RAG 预处理步骤，不是自主 Agent。整个在线链路是无循环 Pipeline，每一步都有确定输入输出和降级路径，从而为下一阶段接入 LangGraph 保留清晰节点边界。

## 3. 总体架构

```mermaid
flowchart TB
    UI[Next.js / API Client] --> API[FastAPI]
    API --> KB[Knowledge Base Service]
    API --> QA[RAG QA Service]
    KB --> PG[(PostgreSQL)]
    KB --> Q[Ingestion Queue]
    Q --> IW[Ingestion Worker]
    IW --> FS[(File Storage)]
    IW --> EMB[Embedding Adapter]
    IW --> SI[Sparse Encoder]
    EMB --> VC[(Qdrant)]
    SI --> VC
    IW --> PG
    QA --> RW[Query Rewriter]
    RW --> RET[Hybrid Retriever]
    RET --> VC
    RET --> RR[RRF + Reranker]
    RR --> CTX[Context Builder]
    CTX --> LLM[LLM Adapter]
    LLM --> CIT[Citation Validator]
    CIT --> API
    QA --> REDIS[(Redis)]
```

### 3.1 运行单元

| 运行单元 | 职责 | 扩展方式 |
| --- | --- | --- |
| API | 参数校验、鉴权占位、流式响应、状态查询 | 无状态水平扩展 |
| Ingestion Worker | 解析、切分、Embedding、稀疏编码、索引发布 | 按队列深度扩展 |
| PostgreSQL | 事务元数据、运行记录、引用 | 单实例开发，生产阶段高可用 |
| Qdrant | 稠密/稀疏向量、Payload 过滤、候选召回 | 分片与副本留待生产化 |
| Redis | 缓存、锁、短期状态 | 故障时允许绕过缓存 |

代码仍采用模块化单体仓库，API 与 Worker 共享 Domain 和 Application 层，不在 RAG 阶段拆成多个代码仓库。

### 3.2 推荐目录

```text
app/
├── api/
├── application/
├── domain/
├── ingestion/
│   ├── parsers/
│   ├── chunking/
│   └── pipeline.py
├── retrieval/
│   ├── query_rewrite.py
│   ├── dense.py
│   ├── sparse.py
│   ├── fusion.py
│   ├── rerank.py
│   └── context.py
├── generation/
├── citations/
├── infrastructure/
│   ├── postgres/
│   ├── qdrant/
│   ├── redis/
│   └── models/
└── observability/
tests/
├── unit/
├── contract/
├── integration/
├── migration/
├── retrieval/
└── fixtures/
```

## 4. 核心领域模型

### 4.1 聚合关系

```mermaid
erDiagram
    KNOWLEDGE_BASE ||--o{ DOCUMENT : contains
    DOCUMENT ||--o{ DOCUMENT_VERSION : versions
    DOCUMENT_VERSION ||--o{ PAGE : has
    DOCUMENT_VERSION ||--o{ CHUNK : produces
    KNOWLEDGE_BASE ||--o{ INDEX_VERSION : publishes
    INDEX_VERSION ||--o{ INDEXED_CHUNK : indexes
    CHUNK ||--o{ INDEXED_CHUNK : represented_by
    QA_RUN ||--o{ RETRIEVAL_HIT : records
    QA_RUN ||--o{ CITATION : returns
    CHUNK ||--o{ CITATION : supports
```

### 4.2 核心实体

`knowledge_bases`：

- `id`、`tenant_id`、`name`、`description`。
- `active_index_version_id`。
- 默认检索配置 ID、创建与更新时间。

`document_versions`：

- `id`、`document_id`、`content_sha256`、`version_number`。
- `parser_version`、`status`、`error_code`。
- 原文件位置、页数、语言和审计时间。

`chunks`：

- 稳定 `id` 和 `document_version_id`。
- `parent_chunk_id`、`chunk_level`、`ordinal`。
- `page_from`、`page_to`、`start_offset`、`end_offset`。
- `heading_path`、`content_type`、`text`、`token_count`。
- `content_sha256`，用于 Embedding 缓存与幂等校验。

`index_versions`：

- `id`、`knowledge_base_id`、`status`：`BUILDING/VALIDATING/ACTIVE/FAILED/RETIRED`。
- 解析、切分、Embedding、稀疏编码、索引 Schema 和检索配置版本。
- 预期 Chunk 数、成功数、失败数、激活与回滚时间。

`qa_runs` 新增：

- `knowledge_base_id`、原始问题、规范化问题、改写查询。
- 检索配置、模型与提示词版本。
- 各阶段耗时、Token、缓存命中和降级标记。
- 选中 Chunk、最终答案、拒答原因和状态。

### 4.3 Qdrant Point

每个可召回子 Chunk 对应一个 Point：

```json
{
  "id": "indexed_chunk_uuid",
  "dense": [0.012, -0.031],
  "sparse": {"indices": [17, 91], "values": [1.2, 0.7]},
  "payload": {
    "tenant_id": "tenant_default",
    "knowledge_base_id": "kb_uuid",
    "index_version_id": "idx_uuid",
    "document_id": "doc_uuid",
    "document_version_id": "docv_uuid",
    "chunk_id": "chunk_uuid",
    "page_from": 3,
    "page_to": 4,
    "language": "zh",
    "content_type": "paragraph"
  }
}
```

原文以 PostgreSQL 为事实源；Qdrant 可保留受限预览以便诊断，但不得成为引用摘录的唯一来源。

## 5. 文档摄取与索引 Pipeline

```mermaid
sequenceDiagram
    participant U as User
    participant A as API
    participant P as PostgreSQL
    participant W as Ingestion Worker
    participant M as Model Adapter
    participant Q as Qdrant
    U->>A: 上传或更新文档
    A->>P: 创建文档版本与索引任务
    A-->>U: 202 + document_version_id
    W->>P: 领取任务并加租约
    W->>W: 解析、规范化、结构切分
    W->>M: 批量生成 Dense/Sparse 表示
    W->>Q: Upsert 到候选 index_version
    W->>P: 写入 Chunk 与构建统计
    W->>W: 数量、维度、抽样检索校验
    W->>P: 事务激活 index_version
```

### 5.1 解析与规范化

- PDF 使用页面作为最低定位边界，保留页码和标题推断结果。
- DOCX 读取标题级别、段落、列表和表格文本；无法映射真实纸张页码时返回逻辑页并标识 `page_kind=logical`。
- Markdown 保留标题路径和代码块类型；TXT 以逻辑段落处理。
- 规范化 Unicode、换行和空白，不修正数字、实体、否定词或单位。
- 解析器输出统一 `DocumentNode`，包含类型、文本、父节点、标题路径、页面和偏移。

### 5.2 父子切分

- 子 Chunk：目标 300–500 Token，最大 700 Token，重叠 50–80 Token，用于召回。
- 父 Chunk：目标 1,000–1,500 Token，用于回填生成上下文。
- 标题、列表项和表格行优先保持语义完整；不允许跨文档版本切分。
- Chunk 参数配置化并纳入 `chunker_version`；参数变化必须生成新索引版本。
- 对超长表格或代码块使用专用策略，不能以固定字符数粗暴截断。

### 5.3 Embedding

`EmbeddingProvider` 接口提供 `embed_documents`、`embed_queries`、维度和模型版本。实现要求：

- 按 Token 和条数双重限制进行批处理。
- 以 `provider + model_version + normalization + content_sha256` 为缓存键。
- 限流、临时网络错误采用带抖动的有限指数退避。
- 单批失败拆分定位坏数据；永久失败写明错误码并终止候选索引激活。
- 向量维度、距离函数和归一化策略写入索引 Schema，禁止静默混用。

### 5.4 幂等与恢复

- 文档内容哈希和处理配置共同决定索引幂等键。
- Worker 通过租约领取任务，超时后可由其他 Worker 恢复。
- Qdrant Point ID 确定生成，重复 Upsert 不增加重复数据。
- PostgreSQL 事务只提交完整批次；激活前核对预期和实际 Point 数。
- 删除先建立 Tombstone，再发布不包含该版本的新活动索引，最后异步清理旧数据。

## 6. 查询理解与改写

### 6.1 处理顺序

```mermaid
flowchart LR
    Q[原始问题] --> N[规范化]
    N --> F[过滤条件提取]
    F --> RW[Query Rewrite]
    RW --> DQ[Dense Query]
    RW --> SQ[Sparse Queries]
    DQ --> HR[Hybrid Retrieval]
    SQ --> HR
```

规范化只处理无语义影响的空白、Unicode 和标点。过滤条件提取识别明确给出的文档、版本、时间或标签，但所有过滤值必须经过知识库权限和实体存在性校验。

### 6.2 Query Rewrite 策略

- 默认输出一个语义完整查询和最多三个关键词查询。
- 保留专有名词、编号、日期、金额、否定和比较关系。
- 对代词依赖问题，只允许使用显式传入且受 Token 限制的对话摘要补全。
- Rewrite 返回结构化 JSON：`semantic_query`、`lexical_queries`、`filters`、`language`。
- 无效 JSON、超时或置信度不足时回退原问题。
- Rewrite 不得回答问题，也不得基于文档外知识增加事实前提。
- 缓存键包含知识库、规范化问题、对话摘要哈希、模型和提示词版本。

为控制延迟和成本，短问题、明显编号查询或缓存命中可跳过 LLM Rewrite，由规则生成查询。

## 7. Hybrid Search

### 7.1 候选召回

对每个请求并行执行：

- Dense：使用 `semantic_query` 生成 Query Embedding，召回 Top 40。
- Sparse/BM25：针对原问题和关键词查询分别召回 Top 40。
- 所有通道在召回前过滤 `tenant_id`、`knowledge_base_id`、`index_version_id` 和用户允许的文档范围。
- 合并后按 `chunk_id` 去重，并保留各通道排名、分数和命中查询。

禁止先做全库召回再在应用层过滤权限，这会导致召回槽位污染和跨边界信息侧漏。

### 7.2 RRF Fusion

默认融合公式：

```text
rrf_score(d) = Σ channel_weight(c) / (rrf_k + rank_c(d))
```

- 初始 `rrf_k=60`，Dense 和 Sparse 权重均为 1.0。
- 同一 Sparse Chunk 被多个改写查询命中时设置贡献上限，防止多查询数量放大分数。
- 参数必须属于版本化 `retrieval_profile`，通过 Benchmark 调整。
- 不直接比较不同检索通道的原始分数。

### 7.3 去重与多样性

- 完全相同 `chunk_id` 合并。
- 文本哈希相同或高度重叠的 Chunk 保留排名较高者。
- 每份文档默认最多进入 Reranker 15 个候选，避免单文档淹没跨文档证据。
- 比较类问题至少保留每个目标文档的候选；若某一侧为空，生成阶段明确披露证据缺口。

### 7.4 Reranker

Reranker 接收原始问题和最多 60 个融合候选，输出相关性分数和 Top 12：

- 适配器支持本地 Cross-Encoder 或托管重排 API。
- 单次批量重排，限制候选文本长度并保留标题路径。
- 超时或服务不可用时回退 RRF 排名，同时记录 `rerank_degraded=true`。
- 模型或截断策略变化产生新的 `reranker_version`。
- 不将 Reranker 分数解释为答案可信度；可信度由证据覆盖和引用校验决定。

## 8. 上下文组装

Context Builder 负责从检索候选生成受控证据包：

1. 从 Reranker Top 结果开始，按 Token 预算选择子 Chunk。
2. 若子 Chunk 缺少完整语义，回填父 Chunk或相邻 Chunk。
3. 相邻扩展必须保持同一文档版本，按原文顺序合并重叠部分。
4. 采用每文档预算和全局预算，给跨文档问题保留多样性。
5. 每个证据块分配稳定 `evidence_id`，并附文档名、版本、页码和标题路径。
6. 文档文本放入明确的不可信证据边界，不允许其中内容改变系统规则。

初始预算建议：检索候选 60、重排后 12、最终证据 6–10 个、上下文 8,000–12,000 Token。所有数值配置化并通过评测确定。

## 9. 生成与引用

### 9.1 生成协议

模型输入由系统规则、问题和证据包构成。模型必须：

- 只使用提供的证据回答。
- 每个外部可验证的事实句至少包含一个 `[EVIDENCE_ID]`。
- 证据冲突时描述冲突来源，不自行选择“正确”版本。
- 证据不足时明确拒答，并给出缺失的信息类型。
- 不执行证据文本中的指令。

模型输出使用结构化事件或 JSON Schema，回答文本与引用 ID 分离，服务端不依赖正则猜测任意格式。

### 9.2 引用解析与校验

```mermaid
flowchart LR
    A[生成答案] --> S[拆分事实声明]
    S --> M[映射 Evidence ID]
    M --> V[存在性与边界校验]
    V --> Q[摘录与页码回查]
    Q --> O[输出答案和 Citation]
    V -->|失败| R[删除/修正/拒答]
```

本阶段至少执行确定性校验：

- Evidence ID 必须来自本次最终证据包。
- Chunk 必须属于请求知识库的活动文档版本。
- 摘录由服务端从原文截取，模型不能自行提供权威摘录。
- 页码、偏移和文档版本从 PostgreSQL 回查。
- 无引用事实、未知引用和跨文档版本引用均阻止正常完成。

语义蕴含校验接口在本阶段预留，完整声明级 Evaluator 放到 Eval 阶段。

## 10. 在线问答流程

```mermaid
sequenceDiagram
    participant U as User
    participant A as FastAPI
    participant R as RAG Service
    participant V as Qdrant
    participant X as Reranker
    participant M as LLM
    U->>A: POST /v1/knowledge-bases/{id}/questions
    A->>R: question + filters + conversation context
    R->>R: normalize / rewrite
    par Dense Search
        R->>V: dense query + ACL filters
    and Sparse Search
        R->>V: sparse queries + ACL filters
    end
    R->>R: RRF / deduplicate
    R->>X: rerank candidates
    R->>R: build evidence context
    R->>M: constrained generation
    M-->>R: answer events + evidence ids
    R->>R: validate and resolve citations
    R-->>A: metadata/delta/citation/usage/done
    A-->>U: SSE stream
```

请求预算建议：Rewrite 800 ms、混合召回 500 ms、Rerank 700 ms、首个生成事件 3 秒、总时限 20 秒。超时预算由入口统一计算并向下传递，子步骤不得各自重新获得完整超时时间。

## 11. API 设计

### 11.1 新增接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/knowledge-bases` | 创建知识库 |
| `GET` | `/v1/knowledge-bases` | 查询知识库列表 |
| `GET` | `/v1/knowledge-bases/{id}` | 查询知识库与活动索引状态 |
| `POST` | `/v1/knowledge-bases/{id}/documents` | 上传文档并创建版本 |
| `POST` | `/v1/knowledge-bases/{id}/reindex` | 使用新配置建立候选索引 |
| `GET` | `/v1/index-jobs/{id}` | 查询解析和索引进度 |
| `POST` | `/v1/knowledge-bases/{id}/search` | 返回可诊断的检索结果 |
| `POST` | `/v1/knowledge-bases/{id}/questions` | 多文档 RAG 问答 SSE |
| `GET` | `/v1/qa-runs/{id}` | 查询问题、检索轨迹、答案和引用 |

`/search` 便于检索调试和 Benchmark，不调用生成模型。生产环境响应默认不暴露内部提示词或未授权 Chunk。

### 11.2 问答请求

```json
{
  "question": "两份制度对报销期限的规定有什么区别？",
  "document_ids": ["doc_a", "doc_b"],
  "filters": {"tags": ["finance"]},
  "conversation_summary": null,
  "retrieval_profile": "hybrid-v1"
}
```

客户端不能任意指定模型、集合名、租户或索引版本。`retrieval_profile` 只允许使用服务端发布的白名单配置。

### 11.3 检索响应要素

每个 Hit 包含 `chunk_id`、文档及版本、页码范围、标题路径、文本预览、Dense 排名、Sparse 排名、RRF 分数、Reranker 分数和命中查询。普通问答响应只返回最终引用所需字段；完整检索轨迹通过受控诊断接口查看。

### 11.4 兼容策略

- 保留 `/v1/documents/{id}/questions`，内部映射到文档所属默认知识库。
- 现有文档首次迁移后生成 `document_version=1`。
- 旧客户端仍可处理原有 SSE 事件；新增字段保持可选。
- 删除或重命名旧字段必须经过单独 API 版本升级，不在 RAG 阶段破坏兼容性。

## 12. 配置与版本治理

主要环境配置：

- PostgreSQL、Qdrant、Redis 和文件存储连接信息。
- Embedding Provider、模型、维度、批大小、超时和并发数。
- Sparse Encoder 版本和词法配置。
- Dense/Sparse Top K、RRF K、通道权重。
- Reranker Provider、模型、Top N、超时。
- 上下文 Token 预算、每文档上限、邻接扩展窗口。
- Rewrite Provider、模型、提示词版本、最大查询数。
- 索引 Worker 并发、租约、重试及死信阈值。

连接密钥只通过环境或密钥管理服务注入。检索配置保存为不可变 `retrieval_profile`；修改后生成新版本，问答运行必须记录实际使用版本。

## 13. 缓存设计

| 缓存 | Key 组成 | 失效规则 |
| --- | --- | --- |
| Embedding | 模型版本 + 规范化版本 + 内容哈希 | 模型或规范化变化 |
| Query Rewrite | KB + 问题 + 对话摘要哈希 + Prompt/模型版本 | TTL 或版本变化 |
| Retrieval | KB + 活动索引版本 + 查询集合 + 过滤器 + Profile | 激活新索引或短 TTL |
| Document Metadata | tenant + document/version | 更新或删除时主动失效 |

回答缓存默认关闭，避免把个性化上下文或权限结果错误复用。Redis 不可用时绕过缓存继续工作，不能影响正确性。

## 14. 可观测性

每次问答建立 `request_id`、`trace_id` 和 `qa_run_id`。Span 至少包含：

- `query.normalize`、`query.rewrite`。
- `embedding.query`、`retrieval.dense`、`retrieval.sparse`。
- `retrieval.fusion`、`retrieval.rerank`、`context.build`。
- `generation.stream`、`citation.validate`。

指标包括：

- 各阶段 P50/P95/P99 延迟与错误率。
- Dense/Sparse 结果交集率、空召回率、Rerank 改序率。
- 最终上下文 Chunk 数、Token 数、文档覆盖数。
- Rewrite、Embedding 和 Retrieval 缓存命中率。
- 索引吞吐、失败率、积压、重试和激活耗时。
- 引用通过率、无支持声明率、拒答率。
- Embedding、Rewrite、Reranker 和生成 Token/费用。

日志不记录完整文档、完整问题、完整上下文、Embedding 或密钥。调试采样需经过配置启用并执行脱敏。

## 15. 安全与数据隔离

- API 层解析身份和知识库权限，检索层仍必须接收不可绕过的 `tenant_id` 与 ACL Filter。
- PostgreSQL 所有业务表包含租户字段并建立组合索引；后续可启用 Row Level Security。
- Qdrant 每次召回都过滤租户、知识库和活动索引版本。
- 文档内容视为不可信输入，提示词明确隔离证据和系统指令。
- 上传沿用大小、签名、扩展名、路径穿越和解析超时防护。
- 诊断接口限制文本预览长度，避免借检索接口批量导出知识库。
- 删除文档后立即阻止新查询访问，物理索引清理允许异步完成。

## 16. 失败处理与降级

| 故障 | 行为 |
| --- | --- |
| Rewrite 超时或格式错误 | 使用原问题和规则关键词 |
| Query Embedding 暂时失败 | 降级到 Sparse Search |
| Sparse Search 失败 | 降级到 Dense Search |
| Reranker 失败 | 使用 RRF 排名并标记降级 |
| Redis 不可用 | 绕过缓存和锁缓存层；任务锁使用数据库租约 |
| 候选索引构建失败 | 保留旧活动索引，候选版本标记 FAILED |
| 引用校验失败 | 修正或拒答，不输出无证据强结论 |
| 两个召回通道均失败 | 返回稳定可重试错误，不调用生成模型 |

仅重试幂等操作。在线请求重试次数严格受限；索引任务使用指数退避、租约和死信状态。错误响应继续采用业务码、`request_id` 和 `retryable` 字段。

## 17. 测试策略

### 17.1 单元测试

- 结构切分边界、父子关系、Token 上限、页码和偏移。
- Embedding 批处理、缓存键、错误拆分及维度校验。
- Query Rewrite Schema、实体保留、失败回退和缓存。
- RRF 公式、权重、缺失排名、重复命中上限。
- Chunk 去重、每文档配额、邻接扩展和 Token 预算。
- 引用存在性、文档版本、摘录和越权校验。
- 各故障的降级决策与错误码。

### 17.2 契约测试

- Embedding、Sparse Encoder、Qdrant、Reranker 和 LLM Adapter 使用固定 Fixture 验证协议。
- CI 默认使用 Fake Model，不调用收费服务。
- Qdrant 与 PostgreSQL 的真实容器版本通过独立契约套件验证字段、过滤和事务假设。

### 17.3 集成与迁移测试

- 上传 → 解析 → 双路索引 → 激活 → Search → Question → Citation 完整闭环。
- 重复上传和 Worker 重试不产生重复活动 Point。
- 候选版本失败时旧索引仍可查询。
- 新版本激活后缓存失效，旧文档版本不再召回。
- SQLite MVP 数据迁移到 PostgreSQL 后文档、Chunk、QA Run 和引用数量一致。
- 旧单文档问答接口保持响应契约。

### 17.4 安全测试

- 跨知识库、跨租户和未授权 `document_ids` 不可召回。
- 恶意文档中的指令不能改变回答规则。
- 删除状态文档不能通过缓存或指定旧版本读取。
- 日志、错误和 Trace 不包含密钥或完整正文。

## 18. RAG Benchmark

### 18.1 数据集

首个版本至少包含 20 份代表性文档和 100 个问题：

| 类型 | 最少数量 | 标注内容 |
| --- | ---: | --- |
| 直接事实 | 30 | 相关 Chunk、答案、引用 |
| 同义表达 | 15 | 语义相关 Chunk |
| 编号/术语/数字 | 15 | 精确词法 Chunk |
| 跨段落 | 15 | 多个相关 Chunk |
| 跨文档比较 | 10 | 每份文档的证据 |
| 冲突信息 | 5 | 冲突双方证据 |
| 不可回答 | 10 | 空相关集合与拒答预期 |

数据集保存问题、过滤条件、相关 Chunk ID、必要文档集合、标准答案、预期引用和不可回答标志。文档或 Chunk 策略变化时，通过稳定映射或重新标注形成新数据集版本。

### 18.2 指标

- 检索：Recall@5/10、MRR@10、nDCG@10、文档覆盖率。
- 融合：Dense-only、Sparse-only、RRF、RRF+Reranker 的消融对比。
- 生成：Answer Relevance、Faithfulness、不可回答准确率。
- 引用：Citation Precision、Citation Recall、页码准确率。
- 性能：各阶段 P50/P95、索引吞吐和峰值内存。
- 成本：每千页索引成本、每成功问答平均模型成本。

### 18.3 发布门禁

固定配置、随机种子、数据集版本和模型版本运行 Benchmark。合并涉及解析、切分、Embedding、检索、Rerank、Prompt 或引用的改动时：

- Recall@5 不得下降超过 2 个百分点。
- Citation Precision 不得下降。
- 安全与不可回答用例必须全部通过硬性规则。
- P95 延迟和平均成本不得超出预算 10%。
- 若使用外部模型造成非确定波动，至少运行三次并报告均值和区间。

## 19. MVP 迁移方案

### 19.1 数据迁移

1. 建立 PostgreSQL Schema 和版本表，不改变现有 SQLite 数据。
2. 将 MVP 文档、页面、Chunk、QA Run 和引用导入 PostgreSQL 暂存表。
3. 校验行数、哈希、外键、页码和抽样文本。
4. 为每个旧文档创建默认知识库和 `document_version=1`。
5. 生成候选 RAG 索引并运行抽样检索及 Benchmark。
6. 原子切换应用读路径到 PostgreSQL 和活动索引。
7. 保留 SQLite 只读快照至观察期结束。

### 19.2 双轨验证

灰度期可对同一问题旁路执行旧关键词 Retriever 和新 Hybrid Retriever，只返回新链路或指定主链路结果，后台比较候选与延迟。旁路结果不得产生两次用户可见回答，也不得双倍写入 QA Run。

### 19.3 回滚

- 应用保留旧 Retriever 适配器和配置开关。
- 索引发布通过 `active_index_version_id` 回切，不覆盖历史版本。
- PostgreSQL 切换失败时，在未写入新业务数据前可回到 SQLite 只读快照。
- 一旦新旧系统发生双向业务写入，不允许直接文件级回滚，必须执行经测试的数据迁移脚本。

## 20. 实施里程碑

### R1：基础设施与领域迁移

- 引入 PostgreSQL、Qdrant、Redis Compose 服务。
- 建立知识库、文档版本、索引版本和迁移工具。
- 实现 Repository、Index 和 Cache 接口及契约测试。

### R2：索引 Pipeline

- 实现 DOCX、结构节点、父子切分。
- 实现 Embedding、Sparse Encoder、批处理缓存和 Worker 恢复。
- 完成候选索引校验、激活和回滚。

### R3：混合检索

- 实现 Query Rewrite、Dense/Sparse 召回、RRF、去重和 Reranker。
- 提供 `/search` 诊断接口和检索运行记录。
- 建立 Dense/Sparse/Hybrid 消融 Benchmark。

### R4：生成、引用与兼容

- 实现上下文组装、多文档受控生成和引用回查。
- 兼容旧单文档 API 与 SSE 事件。
- 完成错误降级、安全测试和端到端测试。

### R5：质量门禁与交付

- 完成标注集、基线报告、性能压测和成本报告。
- 完成 Docker 冒烟、迁移演练、回滚演练和运行文档。
- 达到验收指标后发布 `hybrid-v1` Profile。

每个里程碑拆成可独立回滚的提交；实现、迁移、配置、文档和相应测试必须在同一变更中交付。

## 21. 主要风险与治理

| 风险 | 治理措施 |
| --- | --- |
| Chunk 过小丢上下文、过大降低召回 | 父子 Chunk、邻接扩展、按数据集调参 |
| Embedding 模型变化导致向量不可比 | 索引版本绑定模型和维度，禁止原地混写 |
| 多查询扩展放大噪声 | 限制查询数、贡献上限、Reranker 和消融评测 |
| Reranker 改善有限但增加延迟 | 超时降级、缓存、按查询类型跳过、量化收益 |
| PostgreSQL 与 Qdrant 不一致 | 事实源、确定 Point ID、候选索引校验和可重建 |
| 文档注入影响回答 | 不可信证据边界、指令隔离和攻击样本回归 |
| 缓存返回旧权限或旧版本数据 | Key 包含租户和索引版本，激活/删除主动失效 |
| 供应商费用不可控 | 批处理、缓存、请求预算和成本指标 |

## 22. 待确认 ADR

实施前应形成并提交以下 Architecture Decision Record：

1. `ADR-001`：Qdrant 作为默认混合检索后端，pgvector 作为可替换适配器。
2. `ADR-002`：Embedding 模型、维度、语言覆盖、部署方式和数据合规。
3. `ADR-003`：Sparse/BM25 编码实现、分词器和中英文策略。
4. `ADR-004`：Reranker 模型、本地或托管服务及降级阈值。
5. `ADR-005`：PostgreSQL 迁移、SQLite 观察期和回滚边界。
6. `ADR-006`：Benchmark 数据来源、标注规范和版本治理。

ADR 必须记录决策、备选方案、证据、影响和撤销条件，不能只记录最终技术名称。

## 23. 完成定义

RAG 阶段仅在以下条件全部满足后完成：

- 知识库、多文档摄取、双路索引、混合检索、重排、问答和引用闭环可运行。
- 索引具备版本、校验、原子激活、失败保护和回滚能力。
- 旧 MVP API 兼容，SQLite 到 PostgreSQL 的迁移和回滚经过演练。
- 检索、生成、引用、性能和成本 Benchmark 达到发布门槛。
- 权限过滤发生在召回前，跨知识库及恶意文档测试通过。
- 关键步骤具备 Trace、Metric、结构化日志和稳定错误码。
- 代码、迁移、配置、运行文档和测试同步更新且全部通过。
- 所有变更均有对应 Git commit，可定位、复现和回滚。
