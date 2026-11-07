param([ValidateSet('Dashboard', 'Simulate', 'Tests')][string]$Mode = 'Dashboard')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $basePython = $bundledPython }
    else { $basePython = 'python' }
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
if ($Mode -eq 'Dashboard') {
    & $taskPython -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('streamlit') else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $taskPython -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard dependency installation failed.' }
    }
    & $taskPython -m streamlit run app.py --server.address 127.0.0.1 --server.headless false
} elseif ($Mode -eq 'Simulate') {
    & $taskPython simulate.py --all
    if ($LASTEXITCODE -eq 0) { Start-Process -FilePath (Join-Path $PSScriptRoot 'outputs\report.html') }
} else {
    & $taskPython -m unittest discover -s tests -v
}
exit $LASTEXITCODE
