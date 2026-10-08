param(
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $PSScriptRoot 'backend'
$frontend = Join-Path $PSScriptRoot 'frontend'
$mamba = 'C:\tools\micromamba.exe'
$node = 'C:\Program Files\nodejs\node.exe'
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
$runtime = Join-Path $env:TEMP 'vectra-local'
$url = 'http://127.0.0.1:5173/'

if (-not (Test-Path -LiteralPath $mamba)) { throw "Missing $mamba" }
if (-not (Test-Path -LiteralPath $node)) { throw "Missing $node" }
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules\vite\bin\vite.js'))) {
  throw 'Frontend dependencies are missing. Run npm install in web\frontend.'
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null

function Test-LocalPort([int]$port) {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $task = $client.ConnectAsync('127.0.0.1', $port)
    return ($task.Wait(1000) -and $client.Connected)
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Wait-LocalUrl([string]$target, [string]$label) {
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
      $response = Invoke-WebRequest -Uri $target -UseBasicParsing -TimeoutSec 3
      if ($response.StatusCode -eq 200) {
        Write-Host "$label ready: $target"
        return
      }
    } catch { }
    Start-Sleep -Seconds 1
  }
  throw "$label did not respond at $target. Check logs in $runtime."
}

function Wait-LocalPort([int]$port, [string]$label) {
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if (Test-LocalPort $port) {
      Write-Host "$label ready on 127.0.0.1:$port"
      return
    }
    Start-Sleep -Seconds 1
  }
  throw "$label did not start on port $port."
}

if (-not (Test-LocalPort 5432)) {
  Write-Host 'Starting PostgreSQL...'
  & (Join-Path $repoRoot 'demo\db.ps1') up
}
Wait-LocalPort 5432 'PostgreSQL'

if (-not (Test-LocalPort 8000)) {
  Write-Host 'Starting Django API...'
  Start-Process -FilePath $mamba `
    -ArgumentList @('run', '-n', 'pgv', 'python', 'manage.py', 'runserver', '127.0.0.1:8000', '--noreload') `
    -WorkingDirectory $backend -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtime 'api.out.log') `
    -RedirectStandardError (Join-Path $runtime 'api.err.log') | Out-Null
}
Wait-LocalUrl 'http://127.0.0.1:8000/api/health/' 'Django API'

if (-not (Test-LocalPort 5173)) {
  Write-Host 'Starting React UI...'
  Start-Process -FilePath $node `
    -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1') `
    -WorkingDirectory $frontend -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtime 'ui.out.log') `
    -RedirectStandardError (Join-Path $runtime 'ui.err.log') | Out-Null
}
Wait-LocalUrl 'http://127.0.0.1:5173/api/graph/?limit=1' 'Vectra UI and API proxy'

Write-Host "Open $url"
if (-not $NoBrowser) { Start-Process -FilePath $url }
