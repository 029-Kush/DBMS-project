<#
  Runs the Phase 1 demo against the local server started by .\db.ps1 up

  Usage:
    .\db.ps1 up            # once, first
    .\demo.ps1 baseline    # load_data + build_canary + eval_recall baseline
    .\demo.ps1 faults      # inject version-skew + index-drop, eval each, restore
    .\demo.ps1 timeline    # print the health_snapshots table

  Every python call goes through 'micromamba run -n pgv' so OpenBLAS / psycopg2
  resolve correctly (calling python.exe unactivated segfaults scipy).
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('baseline', 'faults', 'timeline')]
  [string]$Action
)

$ErrorActionPreference = 'Stop'

$MM = 'C:\tools\micromamba.exe'
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
$PGBIN   = 'C:\tools\mmroot\envs\pgv\Library\bin'
$psql    = Join-Path $PGBIN 'psql.exe'
$PROJ    = Join-Path $PSScriptRoot '..\selfheal_pgvector' | Resolve-Path | Select-Object -ExpandProperty Path
$SCRIPTS = Join-Path $PROJ 'scripts'

function Py([string]$file, [string]$arg) {
  Push-Location $SCRIPTS
  try {
    if ($arg) { & $MM run -n pgv python $file $arg }
    else      { & $MM run -n pgv python $file }
    if ($LASTEXITCODE -ne 0) { throw "$file exited $LASTEXITCODE" }
  }
  finally { Pop-Location }
}

function Q([string]$sql) {
  & $psql -h 127.0.0.1 -p 5432 -U svuser -d selfheal -v ON_ERROR_STOP=1 -c $sql
}

switch ($Action) {

  'baseline' {
    Write-Host "`n===== load_data.py =====" -ForegroundColor Cyan
    Py 'load_data.py'
    Write-Host "`n===== build_canary.py =====" -ForegroundColor Cyan
    Py 'build_canary.py'
    Write-Host "`n===== eval_recall.py baseline =====" -ForegroundColor Cyan
    Py 'eval_recall.py' 'baseline'
  }

  'faults' {
    Write-Host "`n### FAULT A: half-finished model migration (1/3 of rows)" -ForegroundColor Yellow
    Q "UPDATE documents SET embedding_model_version='tfidf-svd-v0' WHERE id % 3 = 0;"
    Q "SELECT embedding_model_version, count(*) FROM documents GROUP BY 1 ORDER BY 1;"
    Py 'eval_recall.py' 'degraded_versionskew'
    Write-Host ">>> restore" -ForegroundColor DarkGray
    Q "UPDATE documents SET embedding_model_version='tfidf-svd-v1';"

    Write-Host "`n### FAULT B: ANN index dropped" -ForegroundColor Yellow
    Q "DROP INDEX documents_embedding_hnsw;"
    Write-Host "-- query plan is now a seq scan:"
    & $psql -h 127.0.0.1 -p 5432 -U svuser -d selfheal -c `
      "EXPLAIN SELECT id FROM documents ORDER BY embedding <=> (SELECT embedding FROM documents WHERE id=1) LIMIT 10;" |
      Select-String -Pattern 'Scan|Sort'
    Py 'eval_recall.py' 'degraded_noindex'
    Write-Host ">>> restore index" -ForegroundColor DarkGray
    Q "CREATE INDEX documents_embedding_hnsw ON documents USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);"
    Py 'eval_recall.py' 'recovered'

    & $PSScriptRoot\demo.ps1 timeline
  }

  'timeline' {
    Write-Host "`n===== health_snapshots =====" -ForegroundColor Cyan
    Q "SELECT id, note, recall_at_k AS recall, round(avg_latency_ms::numeric,2) AS avg_ms, round(p95_latency_ms::numeric,2) AS p95_ms, round(version_skew_pct::numeric,1) AS skew_pct, round(dead_tuple_pct::numeric,1) AS dead_pct, recorded_at::timestamp(0) FROM health_snapshots ORDER BY id;"
  }
}
