param([string]$Python = "")

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
}
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$environmentNames = @('PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'OMP_NUM_THREADS',
    'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
$previousEnvironment = @{}
foreach ($environmentName in $environmentNames) {
    $previousEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, 'Process')
    [Environment]::SetEnvironmentVariable($environmentName, '1', 'Process')
}
$validationExitCode = 1
Push-Location $repositoryRoot
try {
    & $pythonCommand -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw 'Dependency validation failed.'
    }
    & $pythonCommand -m pytest -q -m 'not integration and not corpus'
    $validationExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    foreach ($environmentName in $environmentNames) {
        [Environment]::SetEnvironmentVariable($environmentName, $previousEnvironment[$environmentName], 'Process')
    }
}
exit $validationExitCode
