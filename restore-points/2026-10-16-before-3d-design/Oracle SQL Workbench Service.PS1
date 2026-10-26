$ErrorActionPreference = 'Stop'

# Launch the code beside this service script so it cannot start an older copy
# selected by a stale ORACLE_WORKBENCH_HOME environment variable.
$projectPath = [System.IO.Path]::GetFullPath($PSScriptRoot)
$serverPath = Join-Path $projectPath 'run_server.py'
$stdoutLog = Join-Path $projectPath 'server-start.log'
$stderrLog = Join-Path $projectPath 'server-error.log'

function Get-WorkbenchSetting {
  param([string]$Name, [string]$DefaultValue)
  $value = [Environment]::GetEnvironmentVariable($Name)
  if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
  $envFile = Join-Path $projectPath '.env'
  if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
      $trimmed = $line.Trim()
      if ($trimmed -match ('^{0}\s*=\s*(.*)$' -f [regex]::Escape($Name))) {
        return $Matches[1].Trim().Trim([char[]]@([char]34, [char]39))
      }
    }
  }
  return $DefaultValue
}

$venvPython = Join-Path $projectPath '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
  $pythonPath = $venvPython
} else {
  $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
}
$listenAddress = Get-WorkbenchSetting 'HOST' '127.0.0.1'
$portNumber = [int](Get-WorkbenchSetting 'PORT' '3000')
$tlsKeyFile = Get-WorkbenchSetting 'TLS_KEY_FILE' ''
$tlsCertFile = Get-WorkbenchSetting 'TLS_CERT_FILE' ''
$scheme = if ($tlsKeyFile -and $tlsCertFile) { 'https' } else { 'http' }
$publicHost = if ($listenAddress -in @('0.0.0.0', '::', '')) { $env:COMPUTERNAME } else { $listenAddress }
if ([string]::IsNullOrWhiteSpace($publicHost)) { $publicHost = 'localhost' }
$healthUrl = "${scheme}://${publicHost}:${portNumber}/api/status"
$webUrl = "${scheme}://${publicHost}:${portNumber}/oraclesql/"

function Test-WorkbenchResponding {
  if (-not (Get-WorkbenchListeners | Select-Object -First 1)) { return $false }
  try {
    $response = & curl.exe --insecure --silent --max-time 3 --output - --write-out "`n%{http_code}" $healthUrl 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    $lines = ($response -join "`n") -split "`r?`n"
    if ($lines[-1] -ne '200') { return $false }
    $payload = ($lines | Select-Object -SkipLast 1) -join "`n"
    $status = $payload | ConvertFrom-Json -ErrorAction Stop
    return ($null -ne $status.PSObject.Properties['connected'])
  } catch {
    return $false
  }
}
function Get-WorkbenchListeners { @(Get-NetTCPConnection -LocalPort $portNumber -State Listen -ErrorAction SilentlyContinue) }
function Stop-Workbench {
  $ownedProcessIds = @()
  foreach ($listener in (Get-WorkbenchListeners)) {
    try {
      $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction Stop
      if ($process.CommandLine -and $process.CommandLine.IndexOf($serverPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $ownedProcessIds += [int]$listener.OwningProcess
      }
    } catch {}
  }
  if (-not $ownedProcessIds) { return $false }
  foreach ($processId in ($ownedProcessIds | Select-Object -Unique)) {
    try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}
  }
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 250; if (-not (Get-WorkbenchListeners)) { return $true } }
  return $false
}
function Start-Workbench {
  if (-not (Test-Path $serverPath)) { throw "Could not find run_server.py in $projectPath" }
  if ([string]::IsNullOrWhiteSpace($pythonPath)) { throw "Python was not found on PATH. Install Python and Django requirements first." }
  if (Test-WorkbenchResponding) { return $true }
  if (Get-WorkbenchListeners) { throw "Port $portNumber is occupied by an unresponsive or unrelated process. It was left running." }
  Start-Process -FilePath $pythonPath -ArgumentList ('"' + $serverPath + '"') -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
  for ($i=0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 500; if (Test-WorkbenchResponding) { return $true } }
  throw "Server did not respond. Check $stderrLog"
}
function Open-WorkbenchPage {
  $registeredBrowserPaths = @()
  foreach ($appPathKey in @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe'
  )) {
    try {
      $registeredPath = (Get-Item -LiteralPath $appPathKey -ErrorAction Stop).GetValue('')
      if ($registeredPath) { $registeredBrowserPaths += $registeredPath }
    } catch {}
  }

  $browserPaths = @(
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe'),
    (Get-Command chrome.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
    (Get-Command msedge.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
    $registeredBrowserPaths
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

  if (-not $browserPaths) {
    Start-Process -FilePath $webUrl -WindowStyle Maximized | Out-Null
    return
  }

  # Launch directly in fullscreen using the dedicated browser profile.
  $browserName = [System.IO.Path]::GetFileNameWithoutExtension($browserPaths[0])
  $browserProfile = Join-Path $env:LOCALAPPDATA (Join-Path 'OracleSQLWorkbench\BrowserProfiles' $browserName)
  $arguments = @(
    "--user-data-dir=`"$browserProfile`"",
    '--no-first-run',
    '--no-default-browser-check',
    '--kiosk'
  )
  if ($browserName -eq 'msedge') { $arguments += '--edge-kiosk-type=fullscreen' }
  $arguments += $webUrl
  Start-Process -FilePath $browserPaths[0] -ArgumentList $arguments -WindowStyle Maximized | Out-Null
  Write-Host 'Opened the workbench in fullscreen kiosk mode. Its login is saved in a dedicated browser profile.' -ForegroundColor Green
}

# --- AUTO START FIRST TIME ---
try {
  if (Test-WorkbenchResponding) {
    Write-Host "Already running at $webUrl" -ForegroundColor Green
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
  Write-Host "[5] Show Logs                   [6] Open Website (Full screen)"
  Write-Host "[Q] Keep running in background & Close this window"
  Write-Host ""
  $choice = (Read-Host "Choose option").Trim().ToUpper()

  switch ($choice) {
    "1" { Open-WorkbenchPage }
    "2" { Write-Host "Restarting..." -ForegroundColor Yellow; Stop-Workbench | Out-Null; Start-Sleep 1; try { Start-Workbench; Write-Host "RESTARTED SUCCESSFULLY" -ForegroundColor Green; Open-WorkbenchPage } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "3" { if (Stop-Workbench) { Write-Host "STOPPED SUCCESSFULLY" -ForegroundColor Green } else { Write-Host "Already stopped or failed to stop" -ForegroundColor Yellow } }
    "4" { try { Start-Workbench; Write-Host "STARTED SUCCESSFULLY" -ForegroundColor Green; Open-WorkbenchPage } catch { Write-Host $_.Exception.Message -ForegroundColor Red } }
    "5" { if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 30 } else { Write-Host "No log file yet" } }
    "6" { Open-WorkbenchPage }
    "Q" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    "QUIT" { Write-Host "Leaving service running in background..." -ForegroundColor Green; exit 0 }
    default { Write-Host "Invalid choice, use 1-6 or Q" -ForegroundColor Yellow }
  }
}
