<#
  Local PostgreSQL + pgvector control for the Phase 1 demo.

  Installed by setup (no admin, no compiler):
    micromamba : C:\tools\micromamba.exe
    env 'pgv'  : C:\tools\mmroot\envs\pgv   (postgres 16, pgvector 0.8.6, python 3.12)
    data dir   : C:\tools\pgdata            (listens on 127.0.0.1:5432)

  Usage:
    .\db.ps1 up       # initdb if needed, start server, ensure 'selfheal' db exists
    .\db.ps1 status   # is it running? show version + row counts
    .\db.ps1 down     # stop the server
    .\db.ps1 reset    # (re)apply sql/schema.sql  -- WIPES all demo tables
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('up', 'down', 'status', 'reset')]
  [string]$Action
)

$ErrorActionPreference = 'Stop'

$MM      = 'C:\tools\micromamba.exe'
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
$PGBIN   = 'C:\tools\mmroot\envs\pgv\Library\bin'
$PGDATA  = 'C:\tools\pgdata'
$PORT    = 5432
$DBNAME  = 'selfheal'
$DBUSER  = 'svuser'
$PROJ    = Join-Path $PSScriptRoot '..\selfheal_pgvector' | Resolve-Path | Select-Object -ExpandProperty Path
$SCHEMA  = Join-Path $PROJ 'sql\schema.sql'

$initdb  = Join-Path $PGBIN 'initdb.exe'
$pg_ctl  = Join-Path $PGBIN 'pg_ctl.exe'
$psql    = Join-Path $PGBIN 'psql.exe'
$createdb= Join-Path $PGBIN 'createdb.exe'

function Test-Running {
  & $pg_ctl status -D $PGDATA *> $null
  return ($LASTEXITCODE -eq 0)
}

function Psql-Q([string]$sql) {
  & $psql -h 127.0.0.1 -p $PORT -U $DBUSER -d $DBNAME -v ON_ERROR_STOP=1 -c $sql
}

switch ($Action) {

  'up' {
    if (-not (Test-Path (Join-Path $PGDATA 'PG_VERSION'))) {
      Write-Host "initdb -> $PGDATA"
      & $initdb -D $PGDATA -U $DBUSER --auth-host=trust --auth-local=trust -E UTF8 | Out-Null
    }
    if (Test-Running) {
      Write-Host "server already running on 127.0.0.1:$PORT"
    }
    else {
      Write-Host "starting server on 127.0.0.1:$PORT"
      & $pg_ctl -D $PGDATA -l (Join-Path $PGDATA 'server.log') `
                -o "-p $PORT -c listen_addresses=127.0.0.1" -w start
    }
    $exists = & $psql -h 127.0.0.1 -p $PORT -U $DBUSER -d postgres -tAc `
      "SELECT 1 FROM pg_database WHERE datname='$DBNAME'"
    if ($exists -ne '1') {
      Write-Host "creating database '$DBNAME'"
      & $createdb -h 127.0.0.1 -p $PORT -U $DBUSER $DBNAME
      Write-Host "applying schema"
      & $psql -h 127.0.0.1 -p $PORT -U $DBUSER -d $DBNAME -v ON_ERROR_STOP=1 -f $SCHEMA | Out-Null
    }
    Write-Host "ready. DSN: dbname=$DBNAME user=$DBUSER host=127.0.0.1 port=$PORT"
  }

  'down' {
    if (Test-Running) { & $pg_ctl -D $PGDATA -m fast stop }
    else { Write-Host "not running" }
  }

  'status' {
    if (Test-Running) {
      Write-Host "RUNNING on 127.0.0.1:$PORT"
      Psql-Q "SELECT version();"
      Psql-Q "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
      Psql-Q "SELECT 'documents' t, count(*) FROM documents UNION ALL SELECT 'canary_set', count(*) FROM canary_set UNION ALL SELECT 'health_snapshots', count(*) FROM health_snapshots;"
    }
    else {
      Write-Host "STOPPED  (run: .\db.ps1 up)"
    }
  }

  'reset' {
    if (-not (Test-Running)) { throw "server not running -- .\db.ps1 up first" }
    Write-Host "re-applying $SCHEMA (drops + recreates the 4 demo tables)"
    & $psql -h 127.0.0.1 -p $PORT -U $DBUSER -d $DBNAME -v ON_ERROR_STOP=1 -f $SCHEMA
  }
}
