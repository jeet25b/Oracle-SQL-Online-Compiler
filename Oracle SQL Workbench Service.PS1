param(
  [ValidateSet('EnsureService', 'Status', 'RestartAndOpen', 'StartAndOpen', 'RestartOnly', 'StartOnly', 'Stop', 'Open', 'Logs')]
  [string]$Action = 'StartAndOpen'
)

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
$djangoAllowedHosts = Get-WorkbenchSetting 'DJANGO_ALLOWED_HOSTS' '127.0.0.1,localhost,JEET-PC'
$scheme = if ($tlsKeyFile -and $tlsCertFile) { 'https' } else { 'http' }
$publicHost = if ($listenAddress -in @('0.0.0.0', '::', '')) { $env:COMPUTERNAME } else { $listenAddress }
if ([string]::IsNullOrWhiteSpace($publicHost)) { $publicHost = 'localhost' }
$healthUrl = "${scheme}://${publicHost}:${portNumber}/api/status"
$webUrl = "${scheme}://${publicHost}:${portNumber}/online-compiler"
$probeAddress = if ($listenAddress -in @('0.0.0.0', '::', '')) { '127.0.0.1' } else { $listenAddress }
$probeUrl = "${scheme}://${probeAddress}:${portNumber}/api/status"

function Test-WorkbenchResponding {
  try {
    # Probe locally without DNS/proxy discovery or a CIM network inventory.
    $response = & curl.exe --noproxy '*' --insecure --silent --connect-timeout 1 --max-time 2 --header "Host: ${publicHost}:${portNumber}" --output - --write-out "`n%{http_code}" $probeUrl 2>$null
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
function Get-WorkbenchListeners {
  # Read the TCP table without initializing the Windows CIM provider.
  @(Get-WorkbenchListenerProcessIds)
}
function Get-WorkbenchListenerProcessIds {
  # Process ownership is only needed when explicitly stopping/restarting.
  foreach ($line in (& netstat.exe -ano -p tcp)) {
    if ($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$' -and [int]$Matches[1] -eq $portNumber) {
      [int]$Matches[2]
    }
  }
}
function Stop-Workbench {
  $ownedProcessIds = @()
  foreach ($listenerProcessId in (Get-WorkbenchListenerProcessIds | Select-Object -Unique)) {
    $process = $null
    try {
      $process = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerProcessId" -OperationTimeoutSec 3 -ErrorAction Stop
    } catch {}
    if ($process -and $process.CommandLine) {
      if ($process.CommandLine.IndexOf($serverPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $ownedProcessIds += [int]$listenerProcessId
      }
      continue
    }
    # CIM can be disabled or time out. Fall back to Get-Process, but only
    # consider our configured Python executable to avoid stopping an unrelated listener.
    try {
      $process = Get-Process -Id $listenerProcessId -ErrorAction Stop
      if ($process.Path -and $pythonPath -and
          [string]::Equals([IO.Path]::GetFullPath($process.Path), [IO.Path]::GetFullPath($pythonPath), [StringComparison]::OrdinalIgnoreCase)) {
        $ownedProcessIds += [int]$listenerProcessId
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
  Write-Host 'Starting Oracle SQL Workbench (up to 15 seconds)...' -ForegroundColor Yellow
  $serverEnvironment = @{
    HOST = $listenAddress
    PORT = [string]$portNumber
    TLS_KEY_FILE = $tlsKeyFile
    TLS_CERT_FILE = $tlsCertFile
    DJANGO_ALLOWED_HOSTS = $djangoAllowedHosts
  }
  $previousEnvironment = @{}
  foreach ($name in $serverEnvironment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $serverEnvironment[$name], 'Process')
  }
  try {
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList ('"' + $serverPath + '"') -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
  } finally {
    foreach ($name in $previousEnvironment.Keys) {
      [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
  }
  $startupTimer = [System.Diagnostics.Stopwatch]::StartNew()
  while ($startupTimer.Elapsed.TotalSeconds -lt 15) {
    if ($serverProcess.HasExited) { throw "Server exited with code $($serverProcess.ExitCode). Check $stderrLog" }
    if (Test-WorkbenchResponding) { return $true }
    Start-Sleep -Milliseconds 300
  }
  throw "Server did not respond within the startup timeout. Check $stderrLog"
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

  # Start in browser fullscreen (F11 mode), not kiosk mode. Kiosk hides browser
  # chrome and its native fullscreen exit affordance, including the top-center X.
  $browserName = [System.IO.Path]::GetFileNameWithoutExtension($browserPaths[0])
  $browserProfile = Join-Path $env:LOCALAPPDATA (Join-Path 'OracleSQLWorkbench\BrowserProfiles' $browserName)
  $arguments = @(
    "--user-data-dir=`"$browserProfile`"",
    '--no-first-run',
    '--no-default-browser-check',
    '--start-fullscreen'
  )
  $arguments += $webUrl
  Start-Process -FilePath $browserPaths[0] -ArgumentList $arguments -WindowStyle Maximized | Out-Null
  Write-Host 'Opened the workbench in browser fullscreen mode. Press F11 or Esc to exit; its login is saved in a dedicated browser profile.' -ForegroundColor Green
}

try {
  switch ($Action) {
    'Status' {
      if (Test-WorkbenchResponding) { Write-Host 'SERVER STATUS: RUNNING' -ForegroundColor Green }
      else { Write-Host 'SERVER STATUS: STOPPED' -ForegroundColor Red }
    }
    'EnsureService' {
      Start-Workbench
      Write-Host "SERVICE READY at $webUrl (browser not opened)" -ForegroundColor Green
    }
    'RestartAndOpen' {
      Write-Host 'Restarting Oracle SQL Workbench service...' -ForegroundColor Yellow
      if (Get-WorkbenchListeners) {
        if (-not (Stop-Workbench)) { throw 'Could not stop the service process owned by this project.' }
      }
      Start-Workbench
      Write-Host "SERVICE RESTARTED at $webUrl" -ForegroundColor Green
      Open-WorkbenchPage
    }
    'StartAndOpen' {
      Start-Workbench
      Write-Host "SERVICE RUNNING at $webUrl" -ForegroundColor Green
      Open-WorkbenchPage
    }
    'RestartOnly' {
      Write-Host 'Restarting Oracle SQL Workbench service...' -ForegroundColor Yellow
      if (Get-WorkbenchListeners) {
        if (-not (Stop-Workbench)) { throw 'Could not stop the service process owned by this project.' }
      }
      Start-Workbench
      Write-Host "SERVICE RESTARTED at $webUrl (browser not opened)" -ForegroundColor Green
    }
    'StartOnly' {
      Start-Workbench
      Write-Host "SERVICE RUNNING at $webUrl (browser not opened)" -ForegroundColor Green
    }
    'Stop' {
      if (Stop-Workbench) { Write-Host 'SERVICE STOPPED' -ForegroundColor Green }
      else { Write-Host 'Service is already stopped or is not owned by this project.' -ForegroundColor Yellow }
    }
    'Open' { Open-WorkbenchPage }
    'Logs' {
      if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 30 }
      else { Write-Host 'No error log file yet.' -ForegroundColor Yellow }
    }
  }
} catch {
  Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
  exit 1
}
