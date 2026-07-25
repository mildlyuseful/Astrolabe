# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [string]$AcadDir = "C:\Program Files\Autodesk\AutoCAD 2026",
    [string]$SourceRevision = "",
    [string]$ReferenceFamily = "AutoCAD 2026 managed .NET 8"
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PluginSourceRelative = "plugin_src/autocad/TrackballNavAcad"
$Project = Join-Path $RepoRoot "$PluginSourceRelative/TrackballNavAcad.csproj"
$BuiltDll = Join-Path $RepoRoot "$PluginSourceRelative/bin/Release/TrackballNavAcad.dll"
$BundleDirectory = Join-Path $RepoRoot "trackball_daemon/plugins/autocad"
$BundledDll = Join-Path $BundleDirectory "TrackballNavAcad.dll"
$ManifestPath = Join-Path $BundleDirectory "version.json"

Push-Location $RepoRoot
try {
    if ([string]::IsNullOrWhiteSpace($SourceRevision)) {
        $SourceRevision = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Could not resolve the source revision." }
    }
    if ($SourceRevision -notmatch '^[0-9a-f]{40,64}$') {
        throw "SourceRevision must be a full Git object ID."
    }

    & git cat-file -e "$SourceRevision^{commit}"
    if ($LASTEXITCODE -ne 0) { throw "Source revision is not available: $SourceRevision" }
    & git diff --quiet $SourceRevision -- $PluginSourceRelative
    if ($LASTEXITCODE -eq 1) {
        throw "AutoCAD plugin sources differ from $SourceRevision; commit them before building."
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not compare AutoCAD plugin sources." }
    $Untracked = @(& git ls-files --others --exclude-standard -- $PluginSourceRelative)
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect untracked AutoCAD plugin sources." }
    if ($Untracked.Count -gt 0) {
        throw "Untracked AutoCAD plugin sources would make provenance ambiguous: $($Untracked -join ', ')"
    }

    $SourceTree = (& git rev-parse "${SourceRevision}:$PluginSourceRelative").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve the AutoCAD source tree." }
    $DotnetSdk = (& dotnet --version).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve the .NET SDK version." }

    [xml]$ProjectXml = [System.IO.File]::ReadAllText($Project)
    $Version = $ProjectXml.SelectSingleNode("/Project/PropertyGroup/Version").InnerText
    $TargetFramework = $ProjectXml.SelectSingleNode("/Project/PropertyGroup/TargetFramework").InnerText
    $Platform = $ProjectXml.SelectSingleNode("/Project/PropertyGroup/PlatformTarget").InnerText

    $BuildArgs = @(
        "build", $Project,
        "--configuration", "Release",
        "-p:AcadDir=$AcadDir",
        "-p:ContinuousIntegrationBuild=true",
        "-p:Deterministic=true",
        "-p:SourceRevisionId=$SourceRevision"
    )
    & dotnet @BuildArgs
    if ($LASTEXITCODE -ne 0) { throw "AutoCAD plugin build failed with exit code $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath $BuiltDll -PathType Leaf)) {
        throw "Built AutoCAD plugin not found: $BuiltDll"
    }

    $ExpectedInformationalVersion = "$Version+$SourceRevision"
    $VersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($BuiltDll)
    if ($VersionInfo.ProductVersion -ne $ExpectedInformationalVersion) {
        throw "Unexpected informational version: $($VersionInfo.ProductVersion)"
    }
    if ($VersionInfo.FileVersion -ne "$Version.0") {
        throw "Unexpected file version: $($VersionInfo.FileVersion)"
    }

    $ReferenceNodes = @($ProjectXml.SelectNodes("/Project/ItemGroup/Reference[HintPath]"))
    if ($ReferenceNodes.Count -eq 0) {
        throw "The AutoCAD project does not declare any managed reference inputs."
    }
    $ReferenceRecords = foreach ($Reference in $ReferenceNodes) {
        $HintPath = [string]$Reference.HintPath
        $ResolvedHintPath = $HintPath.Replace('$(AcadDir)', $AcadDir)
        if ($ResolvedHintPath -match '\$\([^)]+\)') {
            throw "Could not resolve managed reference path: $HintPath"
        }
        if (-not [System.IO.Path]::IsPathRooted($ResolvedHintPath)) {
            $ResolvedHintPath = Join-Path (Split-Path -Parent $Project) $ResolvedHintPath
        }
        $Path = [System.IO.Path]::GetFullPath($ResolvedHintPath)
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "AutoCAD managed reference not found: $Path"
        }
        $Info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
        [ordered]@{
            name = [System.IO.Path]::GetFileName($Path)
            file_version = $Info.FileVersion
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
        }
    }

    New-Item -ItemType Directory -Force -Path $BundleDirectory | Out-Null
    Copy-Item -LiteralPath $BuiltDll -Destination $BundledDll -Force
    $DllHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $BundledDll).Hash.ToLowerInvariant()
    $Manifest = [ordered]@{
        schema = 1
        version = $Version
        assembly_version = "$Version.0"
        informational_version = $ExpectedInformationalVersion
        source_revision = $SourceRevision
        source_tree = $SourceTree
        dll_sha256 = $DllHash
        dll_size = (Get-Item -LiteralPath $BundledDll).Length
        target_framework = $TargetFramework
        platform = $Platform
        dotnet_sdk = $DotnetSdk
        autocad_reference_family = $ReferenceFamily
        autocad_references = @($ReferenceRecords)
        build_properties = [ordered]@{
            configuration = "Release"
            continuous_integration_build = $true
            deterministic = $true
        }
    }
    $Json = $Manifest | ConvertTo-Json -Depth 5
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ManifestPath, $Json + [Environment]::NewLine, $Utf8NoBom)

    Write-Host "Bundled AutoCAD plugin $Version from $SourceRevision"
    Write-Host "Source tree: $SourceTree"
    Write-Host "DLL SHA-256: $DllHash"
}
finally {
    Pop-Location
}
