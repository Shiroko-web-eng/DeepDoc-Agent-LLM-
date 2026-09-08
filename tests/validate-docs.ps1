$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$documentPath = Join-Path $repositoryRoot 'docs/technical-solution.md'

if (-not (Test-Path -LiteralPath $documentPath -PathType Leaf)) {
    throw "Technical solution document does not exist: $documentPath"
}

$content = Get-Content -LiteralPath $documentPath -Raw -Encoding UTF8
$requiredSections = @(
    '# DeepDoc Agent 技术方案',
    '## 3. 总体架构',
    '## 5. 文档摄取与知识加工',
    '## 6. RAG 检索增强生成',
    '## 7. Agent 设计',
    '## 8. 引用与忠实度保障',
    '## 10. 自动化评测体系',
    '## 11. 可观测性设计',
    '## 12. 安全与治理',
    '## 14. 测试策略',
    '## 17. 分阶段实施路线'
)

foreach ($section in $requiredSections) {
    if (-not $content.Contains($section)) {
        throw "Missing required section: $section"
    }
}

$mermaidCount = ([regex]::Matches($content, '```mermaid')).Count
if ($mermaidCount -lt 3) {
    throw "Expected at least 3 Mermaid diagrams, found $mermaidCount"
}

if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'The technical solution contains unresolved placeholders.'
}

$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 150) {
    throw "Technical solution is unexpectedly short: $lineCount lines"
}

Write-Output "Documentation validation passed: $lineCount lines, $mermaidCount Mermaid diagrams."
