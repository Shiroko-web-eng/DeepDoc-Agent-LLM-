CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    active_index_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    filename TEXT NOT NULL, media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, storage_path TEXT NOT NULL, status TEXT NOT NULL,
    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, sha256)
);
CREATE TABLE IF NOT EXISTS pages (
    id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL, text TEXT NOT NULL, UNIQUE(document_id, page_number)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL,
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_fts
    ON chunks USING gin (to_tsvector('simple', text));
CREATE TABLE IF NOT EXISTS knowledge_base_documents (
    tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, PRIMARY KEY(knowledge_base_id, document_id)
);
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL,
    embedding vector(256), content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS index_jobs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    status TEXT NOT NULL, index_version INTEGER, error_code TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    knowledge_base_id TEXT, question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, model TEXT NOT NULL, input_chars INTEGER NOT NULL DEFAULT 0,
    output_chars INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER, completion_tokens INTEGER, retrieval_trace TEXT NOT NULL DEFAULT '{}',
    error_code TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS citations (
    id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES qa_runs(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL, page_number INTEGER NOT NULL, quote TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    question TEXT NOT NULL, knowledge_base_ids TEXT NOT NULL,
    allow_web_search INTEGER NOT NULL DEFAULT 0, output_format TEXT NOT NULL,
    status TEXT NOT NULL, task_type TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '',
    citations_json TEXT NOT NULL DEFAULT '[]', plan_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]', budget_json TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}', error_code TEXT, current_node TEXT,
    state_version INTEGER NOT NULL DEFAULT 0, cancellation_requested INTEGER NOT NULL DEFAULT 0,
    execution_mode TEXT NOT NULL DEFAULT 'single', route_reason TEXT NOT NULL DEFAULT 'legacy',
    graph_version TEXT NOT NULL DEFAULT 'agent-v1',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_events (
    id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL, event_type TEXT NOT NULL, data_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(run_id, sequence_number)
);
CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id, sequence_number);
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL, node TEXT NOT NULL, state_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(run_id, state_version)
);
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE, step_id TEXT NOT NULL,
    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, status TEXT NOT NULL,
    result_json TEXT, error_code TEXT, duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tasks (
    tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL, agent_type TEXT NOT NULL, objective TEXT NOT NULL,
    status TEXT NOT NULL, knowledge_base_id TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id, task_id)
);
CREATE TABLE IF NOT EXISTS eval_datasets (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    name TEXT NOT NULL, version TEXT NOT NULL, split TEXT NOT NULL, description TEXT NOT NULL,
    content_sha256 TEXT NOT NULL, corpus_snapshot_json TEXT NOT NULL,
    cases_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tenant_id, name, version)
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    dataset_id TEXT NOT NULL REFERENCES eval_datasets(id), mode TEXT NOT NULL,
    status TEXT NOT NULL, config_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    gate_json TEXT NOT NULL DEFAULT '{}', cancellation_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS eval_case_results (
    tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL, status TEXT NOT NULL, artifact_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL, error_code TEXT, duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, PRIMARY KEY(run_id, case_id)
);
CREATE TABLE IF NOT EXISTS durable_jobs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL,
    payload_json TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL, lease_owner TEXT,
    lease_expires_at TEXT, error_code TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim
    ON durable_jobs(status, available_at, created_at);
CREATE TABLE IF NOT EXISTS request_idempotency (
    tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id'),
    route TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, route, idempotency_key)
);

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'knowledge_bases','documents','pages','chunks','knowledge_base_documents',
    'chunk_embeddings','index_jobs','qa_runs','citations','agent_runs','agent_events',
    'agent_checkpoints','agent_tool_calls','agent_tasks','eval_datasets','eval_runs',
    'eval_case_results','durable_jobs','request_idempotency'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
    IF table_name = 'durable_jobs' THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true) OR current_setting(''app.roles'', true) LIKE ''%%worker%%'') WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) OR current_setting(''app.roles'', true) LIKE ''%%worker%%'')',
        table_name
      );
    ELSE
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))',
        table_name
      );
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION deepdoc_queued_jobs(job_kinds TEXT[])
RETURNS BIGINT
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
SET app.roles = 'worker'
AS $$
  SELECT COUNT(*) FROM durable_jobs
  WHERE status = 'QUEUED' AND kind = ANY(job_kinds)
$$;
