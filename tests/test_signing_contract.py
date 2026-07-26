# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNER = (ROOT / "tools" / "sign_windows_artifact.ps1").read_text(encoding="utf-8")


def test_signer_uses_sha256_rfc3161_timestamping_and_platform_verification():
    assert "sign /sha1 $NormalizedThumbprint /fd SHA256 /td SHA256 /tr $TimestampUrl" in SIGNER
    assert "verify /pa /all /v" in SIGNER
    assert "Get-AuthenticodeSignature" in SIGNER
    assert "SignatureStatus]::Valid" in SIGNER


def test_signer_refuses_a_different_identity_or_missing_timestamp():
    assert "SignerCertificate.Thumbprint" in SIGNER
    assert "does not match the requested certificate" in SIGNER
    assert "No timestamp certificate" in SIGNER


def test_signature_report_carries_every_manifest_field():
    for field in (
            "subject", "issuer", "thumbprint", "timestamp_authority", "verified"):
        assert f"{field} =" in SIGNER
