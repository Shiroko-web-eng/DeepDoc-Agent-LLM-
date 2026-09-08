$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/mvp-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "MVP design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent MVP 设计方案',
    '## 1. MVP 定位',
    '## 3. 总体架构',
    '## 5. 数据模型',
    '## 6. API 契约',
    '## 8. 安全与可靠性',
    '## 10. 测试与验收',
    '## 11. 实施拆分',
    '## 12. 向 RAG 阶段演进',
    '## 14. 完成定义'
)

foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required MVP section: $section"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 3) {
    throw "Expected at least 3 Mermaid diagrams, found $diagramCount"
}

$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 250) {
    throw "MVP design is unexpectedly short: $lineCount lines"
}

if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'MVP design contains unresolved placeholders.'
}

Write-Output "MVP documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
