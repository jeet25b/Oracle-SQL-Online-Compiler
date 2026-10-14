$ErrorActionPreference = 'Stop'

$projectPath = $env:ORACLE_WORKBENCH_HOME
if ([string]::IsNullOrWhiteSpace($projectPath)) { $projectPath = $PSScriptRoot }
$projectPath = [System.IO.Path]::GetFullPath($projectPath)
$nodePath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$serverPath = Join-Path $projectPath 'server.js'
$stdoutLog = Join-Path $projectPath 'server-start.log'
$stderrLog = Join-Path $projectPath 'server-error.log'
$url = 'http://127.0.0.1:3000/api/status'
$webUrl = 'http://127.0.0.1:3000'

function Test-WorkbenchResponding {
  $request = [System.Net.HttpWebRequest]::Create($url)
  $request.Proxy = $null
  $request.Timeout = 2000
  try { $response = $request.GetResponse(); $response.Close(); return $true } catch { return $false }
}
function Get-WorkbenchListeners { @(Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) }
function Stop-Workbench {
  $listeners = Get-WorkbenchListeners
  if (-not $listeners) { return $false }
  foreach ($processId in ($listeners.OwningProcess | Select-Object -Unique)) {
    try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}
  }
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 250; if (-not (Get-WorkbenchListeners)) { return $true } }
  return $false
}
function Start-Workbench {
  if (-not (Test-Path $serverPath)) { throw "Could not find server.js in $projectPath" }
  if (-not (Test-Path $nodePath)) { throw "Node.js was not found at $nodePath" }
  Start-Process -FilePath $nodePath -ArgumentList 'server.js' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 500; if (Test-WorkbenchResponding) { return $true } }
  throw "Server did not respond. Check $stderrLog"
}

# --- AUTO START FIRST TIME ---
try {
  if (Test-WorkbenchResponding) {
    Write-Host 'Already running at http://127.0.0.1:3000' -ForegroundColor Green
  } else {
    Write-Host 'Starting Oracle SQL Workbench...' -ForegroundColor Cyan
    if (Get-WorkbenchListeners) { Stop-Workbench | Out-Null }
    Start-Workbench
    Write-Host 'WEB SERVICE STARTED SUCCESSFULLY.' -ForegroundColor Green
  }
  Start-Process $webUrl | Out-Null
} catch {
  Write-Host 'FAILED TO START: ' $_.Exception.Message -ForegroundColor Red
}

# --- PERMANENT MENU LOOP ---
while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor DarkGray
  if (Test-WorkbenchResponding) { Write-Host " STATUS: RUNNING" -ForegroundColor Green } else { Write-Host " STATUS: STOPPED" -ForegroundColor Red }
  Write-Host "========================================" -ForegroundColor DarkGray
  Write-Host "[1] Open Website      [2] Restart Service"
  Write-Host "[3] Stop Service      [4] Start Service"
  Write-Host "[5] Show Logs         [Q] Keep running in background & Close this window"
  Write-Host ""
  $choice = (Read-Host "Choose option").Trim().ToUpper()

  switch ($choice) {
    "1" { Start-Process $webUrl | Out-Null; Write-Host "Opened $webUrl" -ForegroundColor Green }
    "2" { Write-Host "Restarting..." -ForegroundColor Yellow; Stop-Workbench | Out-Null; Start-Sleep 1; try { Start-Workbench; Write-Host "RESTARTED SUCCESSFULLY" -ForegroundColor Green; Start-Process $webUrl | Out-Null } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "3" { if (Stop-Workbench) { Write-Host "STOPPED SUCCESSFULLY" -ForegroundColor Green } else { Write-Host "Already stopped or failed to stop" -ForegroundColor Yellow } }
    "4" { try { Start-Workbench; Write-Host "STARTED SUCCESSFULLY" -ForegroundColor Green; Start-Process $webUrl | Out-Null } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "5" { if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 30 } else { Write-Host "No log file yet" } }
    "Q" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    "QUIT" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    default { Write-Host "Invalid choice, use 1-5 or Q" -ForegroundColor Yellow }
  }
}