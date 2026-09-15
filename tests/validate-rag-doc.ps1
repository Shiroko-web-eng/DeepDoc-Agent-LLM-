$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/rag-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "RAG design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent RAG 版本设计方案',
    '## 1. 阶段定位',
    '## 3. 总体架构',
    '## 4. 核心领域模型',
    '## 5. 文档摄取与索引 Pipeline',
    '## 6. 查询理解与改写',
    '## 7. Hybrid Search',
    '## 9. 生成与引用',
    '## 11. API 设计',
    '## 17. 测试策略',
    '## 18. RAG Benchmark',
    '## 19. MVP 迁移方案',
    '## 20. 实施里程碑',
    '## 23. 完成定义'
)

foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required RAG section: $section"
    }
}

$requiredTerms = @(
    'Query Rewrite',
    'BM25',
    'Qdrant',
    'PostgreSQL',
    'RRF',
    'Reranker',
    'Recall@5',
    'Citation Precision',
    'index_version'
)

foreach ($term in $requiredTerms) {
    if (-not $content.Contains($term)) {
        throw "Missing required RAG design term: $term"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 6) {
    throw "Expected at least 6 Mermaid diagrams, found $diagramCount"
}

$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 400) {
    throw "RAG design is unexpectedly short: $lineCount lines"
}

if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'RAG design contains unresolved placeholders.'
}

Write-Output "RAG documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
