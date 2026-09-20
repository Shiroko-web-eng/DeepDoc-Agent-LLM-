$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$path = Join-Path $root 'docs/eval-design.md'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Eval design document does not exist: $path"
}

$content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
$sections = @(
    '# DeepDoc Agent Eval 版本设计方案',
    '## 1. 阶段定位与现状',
    '## 3. 评测数据集设计',
    '## 4. 实验运行与可复现性',
    '## 5. 指标定义',
    '## 6. 评测器与人工复核',
    '## 7. 存储、接口与报告',
    '## 8. Benchmark 比较与发布门禁',
    '## 9. 测试策略与实施里程碑',
    '## 11. 完成定义'
)
foreach ($section in $sections) {
    if (-not $content.Contains($section)) {
        throw "Missing required Eval section: $section"
    }
}

$terms = @(
    'DatasetManifest', 'EvalCase', 'EvaluationRun', 'Recall@K',
    'MRR@K', 'nDCG@K', 'Faithfulness', 'Citation Accuracy',
    'Task Success Rate', 'Token', 'cost_per_success', 'bootstrap',
    'Judge', 'Holdout', 'NOT_APPLICABLE', 'RAG', 'Multi-Agent'
)
foreach ($term in $terms) {
    if (-not $content.Contains($term)) {
        throw "Missing required Eval design term: $term"
    }
}

$diagramCount = ([regex]::Matches($content, '```mermaid')).Count
if ($diagramCount -lt 3) {
    throw "Expected at least 3 Mermaid diagrams, found $diagramCount"
}
$lineCount = ($content -split "`r?`n").Count
if ($lineCount -lt 190) {
    throw "Eval design is unexpectedly short: $lineCount lines"
}
if ($content -match '(?im)\b(TODO|TBD|FIXME)\b') {
    throw 'Eval design contains unresolved placeholders.'
}

$readme = Get-Content -LiteralPath (Join-Path $root 'README.md') -Raw -Encoding UTF8
if (-not $readme.Contains('[Eval 版本设计方案](docs/eval-design.md)')) {
    throw 'README does not link to the Eval design document.'
}

Write-Output "Eval documentation validation passed: $lineCount lines, $diagramCount Mermaid diagrams."
