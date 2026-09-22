$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/production-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Production design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent Production 版本设计方案',
    '## 1. 阶段定位与当前差距',
    '## 3. 目标架构',
    '## 4. 请求、任务与一致性模型',
    '## 5. 数据平台与迁移',
    '## 6. 身份、授权与租户隔离',
    '## 8. 可观测性、审计与成本',
    '## 9. 可靠性、降级与灾难恢复',
    '## 11. CI/CD、供应链与渐进发布',
    '## 15. 分阶段迁移路线',
    '## 17. Production 完成定义'
)
foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required Production section: $section"
    }
}

$terms = @(
    'SLO', '错误预算', 'PostgreSQL', 'pgvector', 'RLS', 'Outbox',
    '幂等', 'lease', 'DLQ', 'OIDC', 'OpenTelemetry', 'RPO', 'RTO',
    'PITR', 'Kubernetes', 'HPA', 'KEDA', 'SBOM', 'canary', 'GameDay'
)
foreach ($term in $terms) {
    if (-not $content.Contains($term)) {
        throw "Missing required Production design term: $term"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 4) {
    throw "Expected at least 4 Mermaid diagrams, found $diagramCount"
}
$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 260) {
    throw "Production design is unexpectedly short: $lineCount lines"
}
if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'Production design contains unresolved placeholders.'
}

$readme = Get-Content -LiteralPath (Join-Path $root 'README.md') -Raw -Encoding UTF8
if (-not $readme.Contains('[Production 版本设计方案](docs/production-design.md)')) {
    throw 'README does not link to the Production design document.'
}

Write-Output "Production documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
