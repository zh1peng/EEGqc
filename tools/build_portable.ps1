param(
    [string]$OutputDirectory = "output/portable",
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$eegqcRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $eegqcRoot
try {
    if (-not $SkipSync) {
        uv sync --locked --extra iclabel
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    }
    uv run --no-sync pyinstaller --noconfirm --distpath $OutputDirectory --workpath output/portable-build packaging/EEGqc.spec
    if ($LASTEXITCODE -ne 0) { throw "Portable build failed" }
    $eegqcVersion = (Get-Content -LiteralPath src/eegqc/__init__.py | Select-String '__version__').ToString().Split('"')[1]
    Copy-Item -LiteralPath README.md, THIRD_PARTY_NOTICES.md -Destination (Join-Path $OutputDirectory 'EEGqc')
    Copy-Item -LiteralPath docs/PORTABLE.md -Destination (Join-Path $OutputDirectory 'EEGqc')
    Compress-Archive -Path (Join-Path $OutputDirectory 'EEGqc') -DestinationPath (Join-Path $OutputDirectory "EEGqc-$eegqcVersion-windows-x64.zip") -Force
    Write-Output "Portable package: $OutputDirectory/EEGqc-$eegqcVersion-windows-x64.zip"
} finally {
    Pop-Location
}
