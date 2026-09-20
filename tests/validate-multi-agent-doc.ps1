$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/multi-agent-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Multi-Agent design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent Multi-Agent 版本设计方案',
    '## 1. 阶段定位与现状',
    '## 3. 架构选择',
    '## 4. 工作流与委派协议',
    '## 5. 状态、Evidence 与引用',
    '## 6. 持久化、队列与故障恢复',
    '## 7. 预算、权限与安全',
    '## 8. API 与用户体验',
    '## 9. 可观测性与评测',
    '## 10. 实施顺序与交付门槛',
    '## 11. 版本治理与回滚'
)

foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required Multi-Agent section: $section"
    }
}

$terms = @(
    'Supervisor', 'Retrieval Agent', 'Analysis Agent', 'Verifier Agent',
    'TaskSpec', 'TaskResult', 'DAG', 'Send', 'Checkpointer', 'InMemorySaver',
    'Citation Accuracy', 'Feature Flag', 'Outbox', '幂等', '预算'
)
foreach ($term in $terms) {
    if (-not $content.Contains($term)) {
        throw "Missing required Multi-Agent design term: $term"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 3) {
    throw "Expected at least 3 Mermaid diagrams, found $diagramCount"
}

$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 200) {
    throw "Multi-Agent design is unexpectedly short: $lineCount lines"
}

if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'Multi-Agent design contains unresolved placeholders.'
}

Write-Output "Multi-Agent documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
