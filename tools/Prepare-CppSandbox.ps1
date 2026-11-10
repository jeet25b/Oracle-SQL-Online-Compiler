param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { $null }
if (-not $dockerExe) {
    foreach ($candidate in @("$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe", "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe")) {
        if (Test-Path -LiteralPath $candidate) { $dockerExe = $candidate; break }
    }
}
if (-not $dockerExe) {
    Write-Host 'Docker Desktop is not installed.'
    Write-Host '1. Open PowerShell as Administrator and run: wsl --install --no-distribution'
    Write-Host '2. Restart Windows if requested.'
    Write-Host '3. Install Docker Desktop from https://docs.docker.com/desktop/setup/install/windows-install/'
    Write-Host '4. Start Docker Desktop, select Linux containers / WSL 2, and wait for Engine running.'
    Write-Host '5. Run this script again from the portal project folder.'
    exit 1
}
$contextName = if ($env:C_COMPILER_DOCKER_CONTEXT) { $env:C_COMPILER_DOCKER_CONTEXT } else { 'desktop-linux' }
$imageName = if ($env:C_COMPILER_IMAGE) { $env:C_COMPILER_IMAGE } else { 'gcc:14.2.0' }
$operatingSystem = & $dockerExe --context $contextName info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $operatingSystem.Trim() -ne 'linux') { throw 'Start the local Docker Desktop Linux engine first.' }
if (-not $CheckOnly) {
    & $dockerExe --context $contextName pull $imageName
    if ($LASTEXITCODE -ne 0) { throw 'Could not download the GCC compiler image.' }
}
& $dockerExe --context $contextName image inspect $imageName --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'Compiler image is not prepared. Run without -CheckOnly.' }
Write-Host 'Local C/C++ runner is prepared. In the portal, open C or C++ and select Check runner.'
