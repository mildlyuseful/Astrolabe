[CmdletBinding()]
param(
    [string]$OutputDirectory = "build/release"
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$OutputRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDirectory))
$RepoPrefix = $RepoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $OutputRoot.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must resolve inside the repository: $OutputRoot"
}

$PythonArtifacts = Join-Path $OutputRoot "python"
$NuitkaArtifacts = Join-Path $OutputRoot "nuitka"
$NuitkaCache = Join-Path $OutputRoot "nuitka-cache"
foreach ($ArtifactDirectory in @($PythonArtifacts, $NuitkaArtifacts)) {
    if (Test-Path -LiteralPath $ArtifactDirectory) {
        Remove-Item -LiteralPath $ArtifactDirectory -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $PythonArtifacts, $NuitkaArtifacts, $NuitkaCache | Out-Null

Push-Location $RepoRoot
try {
    $env:NUITKA_CACHE_DIR = $NuitkaCache
    python -m build --outdir $PythonArtifacts
    if ($LASTEXITCODE -ne 0) { throw "python -m build failed with exit code $LASTEXITCODE" }

    python -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --enable-plugin=tk-inter `
        --windows-console-mode=attach `
        --include-package=trackball_daemon `
        --include-package-data=trackball_daemon `
        --output-dir=$NuitkaArtifacts `
        --output-filename=Astrolabe.exe `
        tools/release_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Nuitka failed with exit code $LASTEXITCODE" }

    $Executable = Join-Path $NuitkaArtifacts "release_entry.dist/Astrolabe.exe"
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Nuitka executable not found at expected path: $Executable"
    }
    & $Executable --release-smoke
    if ($LASTEXITCODE -ne 0) { throw "Packaged release smoke failed with exit code $LASTEXITCODE" }

    Get-FileHash -Algorithm SHA256 -LiteralPath $Executable |
        Select-Object Algorithm, Hash, Path |
        Format-List
}
finally {
    Pop-Location
}
