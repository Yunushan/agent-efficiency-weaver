$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$manifest = Join-Path $projectRoot 'pyproject.toml'
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Cannot locate the project manifest at $manifest"
}
$pythonName = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$python = Get-Command $pythonName -ErrorAction Stop
& $python.Source -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)"
$inVenv = $LASTEXITCODE -eq 0
$pipArgs = @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '--disable-pip-version-check')
if (-not $inVenv) {
    $pipArgs += '--user'
}
$pipArgs += $projectRoot
& $python.Source @pipArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $python.Source -m agentweaver validate
exit $LASTEXITCODE
