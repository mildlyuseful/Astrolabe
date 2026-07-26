# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("executable", "autocad_plugin", "installer")]
    [string]$ArtifactKey,
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$OutputReport,
    [Parameter(Mandatory = $true)]
    [string]$SigningThumbprint,
    [Parameter(Mandatory = $true)]
    [string]$SignToolPath,
    [Parameter(Mandatory = $true)]
    [string]$TimestampUrl
)

$ErrorActionPreference = "Stop"
$ArtifactPath = [System.IO.Path]::GetFullPath($Path)
$ReportPath = [System.IO.Path]::GetFullPath($OutputReport)
$NormalizedThumbprint = ($SigningThumbprint -replace "\s", "").ToUpperInvariant()
if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
    throw "Signable artifact does not exist: $ArtifactPath"
}
if (-not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
    throw "signtool.exe does not exist: $SignToolPath"
}

& $SignToolPath sign /sha1 $NormalizedThumbprint /fd SHA256 /td SHA256 /tr $TimestampUrl `
    $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "signtool sign failed for $ArtifactPath with exit code $LASTEXITCODE"
}
& $SignToolPath verify /pa /all /v $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "signtool verify failed for $ArtifactPath with exit code $LASTEXITCODE"
}

$Signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath
if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Authenticode verification did not return Valid for ${ArtifactPath}: $($Signature.Status)"
}
if (-not $Signature.SignerCertificate) {
    throw "No signer certificate was returned for $ArtifactPath"
}
if (-not $Signature.TimeStamperCertificate) {
    throw "No timestamp certificate was returned for $ArtifactPath"
}
if ($Signature.SignerCertificate.Thumbprint.ToUpperInvariant() -ne $NormalizedThumbprint) {
    throw "The verified signer thumbprint does not match the requested certificate."
}

$Report = @{}
if (Test-Path -LiteralPath $ReportPath -PathType Leaf) {
    $Existing = Get-Content -Raw -LiteralPath $ReportPath | ConvertFrom-Json
    foreach ($Property in $Existing.PSObject.Properties) {
        $Report[$Property.Name] = $Property.Value
    }
}
$Report[$ArtifactKey] = [ordered]@{
    subject = $Signature.SignerCertificate.Subject
    issuer = $Signature.SignerCertificate.Issuer
    thumbprint = $Signature.SignerCertificate.Thumbprint.ToUpperInvariant()
    timestamp_authority = $Signature.TimeStamperCertificate.Subject
    verified = $true
}
$ReportDirectory = Split-Path -Parent $ReportPath
New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null
$Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
Write-Host "$ArtifactKey signed and verified: $ArtifactPath"
