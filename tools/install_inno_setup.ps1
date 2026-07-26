# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [string]$DestinationDirectory = "build/toolchain/inno-6.7.3"
)

$ErrorActionPreference = "Stop"
$Version = "6.7.3"
$Uri = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
$ExpectedSha256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
$Destination = [System.IO.Path]::GetFullPath($DestinationDirectory)
$Installer = Join-Path $Destination "innosetup-$Version.exe"
$InstallRoot = Join-Path $Destination "compiler"
$Compiler = Join-Path $InstallRoot "ISCC.exe"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    Invoke-WebRequest -Uri $Uri -OutFile $Installer
}
$ActualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) {
    throw "Inno Setup $Version hash mismatch: $ActualSha256"
}
$Signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    -not $Signature.SignerCertificate.Subject.Contains("Pyrsys B.V.")) {
    throw "Inno Setup installer Authenticode verification failed: $($Signature.Status)"
}

if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    & $Installer /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER "/DIR=$InstallRoot"
    $InstallExitCode = $LASTEXITCODE
    if ($null -ne $InstallExitCode -and $InstallExitCode -ne 0) {
        throw "Inno Setup $Version installation failed with exit code $LASTEXITCODE"
    }
}
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw "ISCC.exe was not installed at the pinned destination: $Compiler"
}

Write-Output $Compiler
