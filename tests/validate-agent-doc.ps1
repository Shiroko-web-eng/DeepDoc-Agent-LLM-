$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/agent-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Agent design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent Agent 版本设计方案',
    '## 1. 阶段定位',
    '## 4. 总体架构',
    '## 5. Agent Workflow',
    '## 6. Agent State 设计',
    '## 7. Planner 设计',
    '## 9. Tool Registry 与 Tool Calling',
    '## 11. Evidence Evaluator',
    '## 12. Report Generator',
    '## 14. Budget 与终止条件',
    '## 15. Checkpoint、恢复与幂等',
    '## 17. API 设计',
    '## 22. 测试策略',
    '## 23. Agent Benchmark',
    '## 24. RAG 到 Agent 的迁移',
    '## 26. 实施里程碑',
    '## 30. 完成定义'
)

foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required Agent section: $section"
    }
}

$requiredTerms = @(
    'LangGraph',
    'Planner',
    'Retriever',
    'Tool Executor',
    'Evidence Evaluator',
    'Report Generator',
    'Citation Validator',
    'Checkpointer',
    'Progress Detector',
    'Task Success Rate',
    'Multi-Agent'
)

foreach ($term in $requiredTerms) {
    if (-not $content.Contains($term)) {
        throw "Missing required Agent design term: $term"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 7) {
    throw "Expected at least 7 Mermaid diagrams, found $diagramCount"
}

$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 650) {
    throw "Agent design is unexpectedly short: $lineCount lines"
}

if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'Agent design contains unresolved placeholders.'
}

Write-Output "Agent documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
