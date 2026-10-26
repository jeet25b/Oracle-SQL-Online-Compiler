$ErrorActionPreference = 'Stop'

$projectPath = $env:ORACLE_WORKBENCH_HOME
if ([string]::IsNullOrWhiteSpace($projectPath)) { $projectPath = $PSScriptRoot }
$projectPath = [System.IO.Path]::GetFullPath($projectPath)
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
$serverPath = Join-Path $projectPath 'run_server.py'
$stdoutLog = Join-Path $projectPath 'server-start.log'
$stderrLog = Join-Path $projectPath 'server-error.log'
$url = 'https://JEET-PC:3000/api/status'
$webUrl = 'https://JEET-PC:3000/oraclesql/'

function Test-WorkbenchResponding {
  if (-not (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)) { return $false }
  try {
    $response = & curl.exe --insecure --silent --max-time 3 --output - --write-out "`n%{http_code}" 'https://127.0.0.1:3000/' 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    $responseText = $response -join "`n"
    return ($responseText -match '<title>Your Oracle SQL workbench</title>' -and $responseText -match '200\s*$')
  } catch {
    return $false
  }
}
function Get-WorkbenchListeners { @(Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) }
function Stop-Workbench {
  $listeners = Get-WorkbenchListeners
  if (-not $listeners) { return $false }
  foreach ($listenerProcessId in ($listeners.OwningProcess | Select-Object -Unique)) {
    try { Stop-Process -Id $listenerProcessId -Force -ErrorAction Stop } catch {}
  }
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 250; if (-not (Get-WorkbenchListeners)) { return $true } }
  return $false
}
function Start-Workbench {
  if (-not (Test-Path $serverPath)) { throw "Could not find run_server.py in $projectPath" }
  if ([string]::IsNullOrWhiteSpace($pythonPath)) { throw "Python was not found on PATH. Install Python and Django requirements first." }
  Start-Process -FilePath $pythonPath -ArgumentList 'run_server.py' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 500; if (Test-WorkbenchResponding) { return $true } }
  throw "Server did not respond. Check $stderrLog"
}
function Open-WorkbenchPage {
  param([switch]$NormalWindow)

  $browserPaths = @(
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

  if (-not $browserPaths) {
    Write-Host 'Chrome or Edge was not found; opening the default browser in its normal mode.' -ForegroundColor Yellow
    Start-Process $webUrl | Out-Null
    return
  }

  $arguments = @('--new-window')
  if (-not $NormalWindow) { $arguments = @('--start-fullscreen', '--new-window') }
  $arguments += $webUrl
  Start-Process -FilePath $browserPaths[0] -ArgumentList $arguments | Out-Null
  if ($NormalWindow) {
    Write-Host 'Opened in a normal browser window.' -ForegroundColor Green
  } else {
    Write-Host 'Opened in browser full-screen mode. Press F11 to show the address bar and browser controls.' -ForegroundColor Green
  }
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
  Open-WorkbenchPage
} catch {
  Write-Host 'FAILED TO START: ' $_.Exception.Message -ForegroundColor Red
}

# --- PERMANENT MENU LOOP ---
while ($true) {
  Write-Host ""
  Write-Host "========================================" -ForegroundColor DarkGray
  if (Test-WorkbenchResponding) { Write-Host " STATUS: RUNNING" -ForegroundColor Green } else { Write-Host " STATUS: STOPPED" -ForegroundColor Red }
  Write-Host "========================================" -ForegroundColor DarkGray
  Write-Host "[1] Open Website (Full screen)  [2] Restart Service"
  Write-Host "[3] Stop Service                [4] Start Service"
  Write-Host "[5] Show Logs                   [6] Open Website (Normal)"
  Write-Host "[Q] Keep running in background & Close this window"
  Write-Host ""
  $choice = (Read-Host "Choose option").Trim().ToUpper()

  switch ($choice) {
    "1" { Open-WorkbenchPage }
    "2" { Write-Host "Restarting..." -ForegroundColor Yellow; Stop-Workbench | Out-Null; Start-Sleep 1; try { Start-Workbench; Write-Host "RESTARTED SUCCESSFULLY" -ForegroundColor Green; Open-WorkbenchPage } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "3" { if (Stop-Workbench) { Write-Host "STOPPED SUCCESSFULLY" -ForegroundColor Green } else { Write-Host "Already stopped or failed to stop" -ForegroundColor Yellow } }
    "4" { try { Start-Workbench; Write-Host "STARTED SUCCESSFULLY" -ForegroundColor Green; Open-WorkbenchPage } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "5" { if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 30 } else { Write-Host "No log file yet" } }
    "6" { Open-WorkbenchPage -NormalWindow }
    "Q" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    "QUIT" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    default { Write-Host "Invalid choice, use 1-6 or Q" -ForegroundColor Yellow }
  }
}
