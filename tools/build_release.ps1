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
$ReleaseEnvironment = Join-Path $OutputRoot ".venv"
$RuntimeEnvironment = Join-Path $OutputRoot "runtime-venv"
foreach ($ArtifactDirectory in @($PythonArtifacts, $NuitkaArtifacts)) {
    if (Test-Path -LiteralPath $ArtifactDirectory) {
        Remove-Item -LiteralPath $ArtifactDirectory -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $PythonArtifacts, $NuitkaArtifacts, $NuitkaCache | Out-Null

Push-Location $RepoRoot
try {
    $env:NUITKA_CACHE_DIR = $NuitkaCache
    $env:UV_CACHE_DIR = Join-Path $OutputRoot "uv-cache"
    $env:UV_PROJECT_ENVIRONMENT = $ReleaseEnvironment
    $Uv = (Get-Command uv -ErrorAction Stop).Source
    $BuildPython = (Get-Command python -ErrorAction Stop).Source
    & $Uv sync --locked --no-editable --extra release --extra onshape --python $BuildPython
    if ($LASTEXITCODE -ne 0) { throw "Locked release-environment sync failed with exit code $LASTEXITCODE" }
    $ReleasePython = Join-Path $ReleaseEnvironment "Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $ReleasePython -PathType Leaf)) {
        throw "Locked release Python was not created at expected path: $ReleasePython"
    }

    & $ReleasePython tools/verify_autocad_artifact.py
    if ($LASTEXITCODE -ne 0) { throw "AutoCAD artifact verification failed with exit code $LASTEXITCODE" }

    & $ReleasePython -m trackball_daemon --release-smoke
    if ($LASTEXITCODE -ne 0) { throw "Source release smoke failed with exit code $LASTEXITCODE" }

    & $Uv build --out-dir $PythonArtifacts
    if ($LASTEXITCODE -ne 0) { throw "uv build failed with exit code $LASTEXITCODE" }

    & $ReleasePython -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --enable-plugin=tk-inter `
        --windows-console-mode=attach `
        --include-package=trackball_daemon `
        --include-package=winrt `
        --include-package-data=trackball_daemon `
        --include-data-files=trackball_daemon/plugins/autocad/TrackballNavAcad.dll=trackball_daemon/plugins/autocad/TrackballNavAcad.dll `
        --output-dir=$NuitkaArtifacts `
        --output-filename=Astrolabe.exe `
        tools/release_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Nuitka failed with exit code $LASTEXITCODE" }

    $Executable = Join-Path $NuitkaArtifacts "release_entry.dist/Astrolabe.exe"
    $PackagedAutoCAD = Join-Path $NuitkaArtifacts (
        "release_entry.dist/trackball_daemon/plugins/autocad/TrackballNavAcad.dll")
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Nuitka executable not found at expected path: $Executable"
    }
    if (-not (Test-Path -LiteralPath $PackagedAutoCAD -PathType Leaf)) {
        throw "Nuitka AutoCAD plugin not found at expected path: $PackagedAutoCAD"
    }
    & $Executable --release-smoke
    if ($LASTEXITCODE -ne 0) { throw "Packaged release smoke failed with exit code $LASTEXITCODE" }

    $Version = (& $ReleasePython -c "from trackball_daemon import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Version)) {
        throw "Could not resolve the package version for the release archive."
    }
    $ReleaseDirectory = Split-Path -Parent $Executable
    $Archive = Join-Path $OutputRoot "Astrolabe-$Version-windows-x64.zip"
    & $ReleasePython tools/archive_release.py --source $ReleaseDirectory --output $Archive
    if ($LASTEXITCODE -ne 0) { throw "Release archive creation failed with exit code $LASTEXITCODE" }

    $Sbom = Join-Path $OutputRoot "Astrolabe-$Version-windows-x64.cdx.json"
    $env:UV_PROJECT_ENVIRONMENT = $RuntimeEnvironment
    & $Uv sync --locked --no-editable --extra onshape --python $BuildPython
    if ($LASTEXITCODE -ne 0) { throw "Locked runtime-environment sync failed with exit code $LASTEXITCODE" }
    $RuntimePython = Join-Path $RuntimeEnvironment "Scripts/python.exe"
    $SbomTool = Join-Path $ReleaseEnvironment "Scripts/cyclonedx-py.exe"
    if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) {
        throw "Locked runtime Python was not created at expected path: $RuntimePython"
    }
    if (-not (Test-Path -LiteralPath $SbomTool -PathType Leaf)) {
        throw "CycloneDX tool was not installed at expected path: $SbomTool"
    }
    & $SbomTool environment $RuntimePython --pyproject (Join-Path $RepoRoot "pyproject.toml") `
        --mc-type application --spec-version 1.6 --output-format JSON `
        --output-reproducible --output-file $Sbom
    if ($LASTEXITCODE -ne 0) { throw "CycloneDX SBOM generation failed with exit code $LASTEXITCODE" }
    & $ReleasePython tools/finalize_sbom.py --sbom $Sbom `
        --name "trackball-daemon" --version $Version
    if ($LASTEXITCODE -ne 0) { throw "SBOM metadata finalization failed with exit code $LASTEXITCODE" }

    Get-FileHash -Algorithm SHA256 -LiteralPath $Executable, $PackagedAutoCAD, $Archive, $Sbom |
        Select-Object Algorithm, Hash, Path |
        Format-List
    Write-Host "Archive checksum file: $Archive.sha256"
}
finally {
    Pop-Location
}
