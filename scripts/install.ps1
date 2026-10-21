$ErrorActionPreference = 'Stop'
python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m agentweaver validate
exit $LASTEXITCODE
