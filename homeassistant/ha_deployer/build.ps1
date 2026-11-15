$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $project
$python = (Get-Command python -ErrorAction Stop).Source
$deps = Join-Path $env:TEMP ("xiaomi-ha-deployer-deps-" + $PID)
New-Item -ItemType Directory -Force -Path $deps | Out-Null
$oldPythonPath = $env:PYTHONPATH

try {
    & $python -m pip install --disable-pip-version-check --target $deps -r (Join-Path $project 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw "Python packaging dependencies failed." }

$pluginSource = Join-Path $root 'xiaomi-nas-plugin'
$pluginData = $pluginSource + ';xiaomi-nas-plugin'
$dist = Join-Path $project 'dist/v1.2.0'
$work = Join-Path $project 'build'
$env:PYTHONPATH = $deps

    $pyinstallerArgs = @(
        '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--name', 'XiaomiHAInstaller',
        '--add-data', $pluginData,
        '--collect-all', 'paramiko',
        '--collect-all', 'cryptography',
        '--distpath', $dist,
        '--workpath', $work,
        '--specpath', $work,
        (Join-Path $project 'ha_deployer.py')
    )
    & $python @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    Write-Host "Built: $(Join-Path $dist 'XiaomiHAInstaller.exe')"
}
finally {
    if ($null -eq $oldPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $oldPythonPath
    }
    Remove-Item -LiteralPath $deps -Recurse -Force -ErrorAction SilentlyContinue
}
