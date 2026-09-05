# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [string]$OutputDirectory = "build/release",
    # One patch version is an input to the artifact, not a property of the operator's PATH.
    [string]$PythonVersion = "3.13.14",
    [string]$BuildPython,
    # A release pipeline must not produce an artifact whose recorded revision does not describe it.
    # Local builds deliberately allow it and the manifest records the tree as dirty instead.
    [switch]$RequireCleanRevision,
    [switch]$BuildInstaller,
    [string]$InnoCompilerPath,
    # Signing is opt-in for development builds and mandatory for the public channel.
    [switch]$Sign,
    [string]$SigningThumbprint = $env:ASTROLABE_SIGNING_THUMBPRINT,
    [string]$SignToolPath,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$OutputRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDirectory))
$RepoPrefix = $RepoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $OutputRoot.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must resolve inside the repository: $OutputRoot"
}

function Resolve-OptionalExecutable {
    param(
        [string]$ExplicitPath,
        [string]$CommandName,
        [string[]]$FallbackPaths
    )
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $resolved = [System.IO.Path]::GetFullPath($ExplicitPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "$CommandName was not found at the supplied path: $resolved"
        }
        return $resolved
    }
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    foreach ($candidate in $FallbackPaths) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Resolve-WindowsSignTool {
    param([string]$ExplicitPath)

    $resolved = Resolve-OptionalExecutable -ExplicitPath $ExplicitPath `
        -CommandName "signtool.exe" -FallbackPaths @(
            "${env:ProgramFiles(x86)}\Windows Kits\10\bin\x64\signtool.exe",
            "$env:ProgramFiles\Windows Kits\10\bin\x64\signtool.exe")
    if ($resolved) {
        return $resolved
    }

    # Current Windows SDKs put signtool under bin\<SDK version>\x64. GitHub's hosted runner does
    # not guarantee that directory is on PATH, so discover the newest installed x64 SDK explicitly.
    foreach ($kitsBin in @(
            "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
            "$env:ProgramFiles\Windows Kits\10\bin")) {
        if (-not (Test-Path -LiteralPath $kitsBin -PathType Container)) {
            continue
        }
        $candidate = Get-ChildItem -LiteralPath $kitsBin -Directory |
            Where-Object { $_.Name -match "^\d+\.\d+\.\d+\.\d+$" } |
            Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "x64/signtool.exe" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }
    return $null
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
New-Item -ItemType Directory -Force -Path $PythonArtifacts, $NuitkaArtifacts, $NuitkaCache |
    Out-Null

Push-Location $RepoRoot
try {
    $env:NUITKA_CACHE_DIR = $NuitkaCache
    $env:UV_CACHE_DIR = Join-Path $OutputRoot "uv-cache"
    $env:UV_PROJECT_ENVIRONMENT = $ReleaseEnvironment
    $Uv = (Get-Command uv -ErrorAction Stop).Source
    if ([string]::IsNullOrWhiteSpace($BuildPython)) {
        $BuildPython = (Get-Command python -ErrorAction Stop).Source
    }
    $BuildPython = [System.IO.Path]::GetFullPath($BuildPython)
    $ActualPythonVersion = (& $BuildPython -c (
        "import platform; print(platform.python_version())")).Trim()
    if ($LASTEXITCODE -ne 0 -or $ActualPythonVersion -ne $PythonVersion) {
        throw (
            "Release Python must be exactly $PythonVersion; resolved $BuildPython " +
            "(reported $ActualPythonVersion). Pass -BuildPython explicitly after installing " +
            "the pinned interpreter.")
    }

    # Interpreter distributions use different valid OpenSSL DLL basenames. Resolve every semantic
    # runtime dependency before dependency sync and C compilation so a bad host layout fails fast.
    & $BuildPython tools/stage_python_runtime.py --python $BuildPython
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned interpreter runtime preflight failed with exit code $LASTEXITCODE"
    }

    & $Uv sync --locked --no-editable --extra release --python $BuildPython
    if ($LASTEXITCODE -ne 0) {
        throw "Locked release-environment sync failed with exit code $LASTEXITCODE"
    }
    $ReleasePython = Join-Path $ReleaseEnvironment "Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $ReleasePython -PathType Leaf)) {
        throw "Locked release Python was not created at expected path: $ReleasePython"
    }

    & $ReleasePython tools/verify_autocad_artifact.py
    if ($LASTEXITCODE -ne 0) {
        throw "AutoCAD artifact verification failed with exit code $LASTEXITCODE"
    }
    & $ReleasePython -m trackball_daemon --release-smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Source release smoke failed with exit code $LASTEXITCODE"
    }
    & $Uv build --out-dir $PythonArtifacts
    if ($LASTEXITCODE -ne 0) {
        throw "uv build failed with exit code $LASTEXITCODE"
    }

    $Identity = (& $ReleasePython tools/release_identity.py | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or -not $Identity.version -or -not $Identity.channel) {
        throw "Could not resolve release identity from the package authorities."
    }
    if ($Identity.channel -eq "public" -and -not $BuildInstaller) {
        throw "A public release requires -BuildInstaller."
    }
    if ($Identity.channel -eq "public" -and -not $Sign) {
        throw "A public release requires -Sign; unsigned public artifacts are refused."
    }
    if ($Sign -and [string]::IsNullOrWhiteSpace($SigningThumbprint)) {
        throw "-Sign requires -SigningThumbprint or ASTROLABE_SIGNING_THUMBPRINT."
    }

    & $ReleasePython -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --enable-plugin=tk-inter `
        --windows-console-mode=attach `
        --include-package=trackball_daemon `
        --include-package=winrt `
        --include-package-data=trackball_daemon `
        --include-data-files=trackball_daemon/plugins/autocad/TrackballNavAcad.dll=trackball_daemon/plugins/autocad/TrackballNavAcad.dll `
        --include-data-files=LICENSE=LICENSE `
        --include-data-files=NOTICE=NOTICE `
        --include-data-files=THIRD_PARTY_NOTICES.md=THIRD_PARTY_NOTICES.md `
        --include-data-files=LICENSING.md=LICENSING.md `
        --include-data-dir=LICENSES=LICENSES `
        --output-dir=$NuitkaArtifacts `
        --output-filename=$($Identity.executable_name) `
        tools/release_entry.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka failed with exit code $LASTEXITCODE"
    }

    # Nuitka names a standalone directory after the entry script. Normalize that implementation
    # detail before signing or archiving so the user-visible ZIP root is always Astrolabe.
    $RawReleaseDirectory = Join-Path $NuitkaArtifacts "release_entry.dist"
    $ReleaseDirectory = Join-Path $NuitkaArtifacts $Identity.product_name
    if (-not (Test-Path -LiteralPath $RawReleaseDirectory -PathType Container)) {
        throw "Nuitka onedir was not found at expected path: $RawReleaseDirectory"
    }
    if (Test-Path -LiteralPath $ReleaseDirectory) {
        Remove-Item -LiteralPath $ReleaseDirectory -Recurse -Force
    }
    Move-Item -LiteralPath $RawReleaseDirectory -Destination $ReleaseDirectory

    $Executable = Join-Path $ReleaseDirectory $Identity.executable_name
    $PackagedAutoCAD = Join-Path $ReleaseDirectory (
        "trackball_daemon/plugins/autocad/TrackballNavAcad.dll")
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Nuitka executable not found at expected path: $Executable"
    }
    if (-not (Test-Path -LiteralPath $PackagedAutoCAD -PathType Leaf)) {
        throw "Nuitka AutoCAD plugin not found at expected path: $PackagedAutoCAD"
    }
    if (Get-ChildItem -LiteralPath $ReleaseDirectory -Recurse -Force |
            Where-Object { $_.FullName -match "[\\/]ui_demo([\\/]|$)" }) {
        throw "The release tree unexpectedly contains non-shipping ui_demo content."
    }

    # Nuitka normally follows these dependencies, but not every CPython distribution flavor is
    # recognized by its scanner. Stage the exact files accepted by the early preflight.
    & $BuildPython tools/stage_python_runtime.py --python $BuildPython `
        --destination $ReleaseDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned interpreter runtime staging failed with exit code $LASTEXITCODE"
    }

    & $ReleasePython tools/audit_native_binaries.py --root $ReleaseDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Native binary attribution audit failed with exit code $LASTEXITCODE"
    }

    $SignatureReport = Join-Path $OutputRoot "signature-report.json"
    if (Test-Path -LiteralPath $SignatureReport) {
        Remove-Item -LiteralPath $SignatureReport -Force
    }
    if ($Sign) {
        $SignTool = Resolve-WindowsSignTool -ExplicitPath $SignToolPath
        if (-not $SignTool) {
            throw "signtool.exe was not found; pass -SignToolPath."
        }
        foreach ($item in @(
                @("executable", $Executable),
                @("autocad_plugin", $PackagedAutoCAD))) {
            & "$PSScriptRoot/sign_windows_artifact.ps1" `
                -ArtifactKey $item[0] -Path $item[1] -OutputReport $SignatureReport `
                -SigningThumbprint $SigningThumbprint -SignToolPath $SignTool `
                -TimestampUrl $TimestampUrl
            if ($LASTEXITCODE -ne 0) {
                throw "Signing $($item[0]) failed with exit code $LASTEXITCODE"
            }
        }
    }

    # A Windows GUI-subsystem executable returns control to PowerShell immediately when invoked
    # with `&`, so $LASTEXITCODE can describe an earlier command while the packaged process crashes
    # in the background. Start-Process -Wait makes this a real release gate.
    $SmokeProcess = Start-Process -FilePath $Executable -ArgumentList "--release-smoke" `
        -Wait -PassThru -NoNewWindow
    if ($SmokeProcess.ExitCode -ne 0) {
        throw "Packaged release smoke failed with exit code $($SmokeProcess.ExitCode)"
    }

    $Archive = Join-Path $OutputRoot $Identity.archive_name
    & $ReleasePython tools/archive_release.py --source $ReleaseDirectory --output $Archive
    if ($LASTEXITCODE -ne 0) {
        throw "Release archive creation failed with exit code $LASTEXITCODE"
    }

    $Installer = $null
    if ($BuildInstaller) {
        $InnoCompiler = Resolve-OptionalExecutable -ExplicitPath $InnoCompilerPath `
            -CommandName "ISCC.exe" -FallbackPaths @(
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 6\ISCC.exe")
        if (-not $InnoCompiler) {
            throw "ISCC.exe was not found; install pinned Inno Setup or pass -InnoCompilerPath."
        }
        $InstallerBaseName = [System.IO.Path]::GetFileNameWithoutExtension(
            $Identity.installer_name)
        $InstallerArguments = @(
            "/Qp",
            "/DProductName=$($Identity.product_name)",
            "/DPublisher=$($Identity.publisher)",
            "/DVersion=$($Identity.version)",
            "/DNumericVersion=$($Identity.windows_file_version)",
            "/DAppId=$($Identity.inno_installer_app_id)",
            "/DInstallDirectory=$($Identity.install_directory)",
            "/DStartMenuFolder=$($Identity.start_menu_folder)",
            "/DExecutableName=$($Identity.executable_name)",
            "/DInstallerBaseName=$InstallerBaseName",
            "/DSourceDirectory=$ReleaseDirectory",
            "/DOutputDirectory=$OutputRoot",
            "/DConfigDirectory=$($Identity.config_directory)",
            "/DCurrentMutex=$($Identity.single_instance_mutex)",
            "/DLegacyMutex=$($Identity.legacy_single_instance_mutex)",
            "/DLicenseFile=$(Join-Path $RepoRoot 'LICENSE')",
            (Join-Path $RepoRoot "installer/Astrolabe.iss"))
        & $InnoCompiler @InstallerArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
        }
        $Installer = Join-Path $OutputRoot $Identity.installer_name
        if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
            throw "Installer was not found at expected path: $Installer"
        }
        if ($Sign) {
            & "$PSScriptRoot/sign_windows_artifact.ps1" `
                -ArtifactKey "installer" -Path $Installer -OutputReport $SignatureReport `
                -SigningThumbprint $SigningThumbprint -SignToolPath $SignTool `
                -TimestampUrl $TimestampUrl
            if ($LASTEXITCODE -ne 0) {
                throw "Signing the installer failed with exit code $LASTEXITCODE"
            }
        }
    }

    $Sbom = Join-Path $OutputRoot (
        [System.IO.Path]::ChangeExtension($Identity.archive_name, $null) + "cdx.json")
    $env:UV_PROJECT_ENVIRONMENT = $RuntimeEnvironment
    & $Uv sync --locked --no-editable --python $BuildPython
    if ($LASTEXITCODE -ne 0) {
        throw "Locked runtime-environment sync failed with exit code $LASTEXITCODE"
    }
    $RuntimePython = Join-Path $RuntimeEnvironment "Scripts/python.exe"
    $SbomTool = Join-Path $ReleaseEnvironment "Scripts/cyclonedx-py.exe"
    if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) {
        throw "Locked runtime Python was not created at expected path: $RuntimePython"
    }
    if (-not (Test-Path -LiteralPath $SbomTool -PathType Leaf)) {
        throw "CycloneDX tool was not installed at expected path: $SbomTool"
    }

    & $ReleasePython tools/audit_notices.py --environment $RuntimePython
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled-component notice audit failed with exit code $LASTEXITCODE"
    }
    & $SbomTool environment $RuntimePython --pyproject (Join-Path $RepoRoot "pyproject.toml") `
        --mc-type application --spec-version 1.6 --output-format JSON `
        --output-reproducible --output-file $Sbom
    if ($LASTEXITCODE -ne 0) {
        throw "CycloneDX SBOM generation failed with exit code $LASTEXITCODE"
    }
    & $ReleasePython tools/finalize_sbom.py --sbom $Sbom `
        --name $Identity.distribution_name --version $Identity.version
    if ($LASTEXITCODE -ne 0) {
        throw "SBOM metadata finalization failed with exit code $LASTEXITCODE"
    }

    # Last, because it records the final hashes after staging, signing, archive creation, installer
    # creation/signing, and SBOM finalization.
    $Manifest = Join-Path $OutputRoot (
        [System.IO.Path]::ChangeExtension($Identity.archive_name, $null) + "manifest.json")
    $ManifestArguments = @(
        "tools/build_release_manifest.py",
        "--output", $Manifest,
        "--root", $OutputRoot,
        "--executable", $Executable,
        "--autocad-plugin", $PackagedAutoCAD,
        "--archive", $Archive,
        "--sbom", $Sbom)
    if ($Installer) {
        $ManifestArguments += @("--installer", $Installer)
    }
    if ($Sign) {
        $ManifestArguments += @("--signature-report", $SignatureReport)
    }
    if ($RequireCleanRevision) {
        $ManifestArguments += "--require-clean"
    }
    & $ReleasePython @ManifestArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Release manifest generation failed with exit code $LASTEXITCODE"
    }
    & $ReleasePython tools/verify_release_manifest.py --manifest $Manifest --directory $OutputRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Release manifest verification failed with exit code $LASTEXITCODE"
    }

    $HashTargets = @($Executable, $PackagedAutoCAD, $Archive, $Sbom)
    if ($Installer) {
        $HashTargets += $Installer
    }
    Get-FileHash -Algorithm SHA256 -LiteralPath $HashTargets |
        Select-Object Algorithm, Hash, Path |
        Format-List
    Write-Host "Archive checksum file: $Archive.sha256"
    Write-Host "Release manifest: $Manifest"
}
finally {
    Pop-Location
}
