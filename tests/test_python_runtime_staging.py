# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from tools import stage_python_runtime as runtime


def _interpreter_layout(root, *, crypto_name="libcrypto-3-x64.dll"):
    dlls = root / "DLLs"
    dlls.mkdir(parents=True)
    for path in (
        root / "python3.dll",
        root / "vcruntime140_1.dll",
        dlls / "tcl86t.dll",
        dlls / "tk86t.dll",
        dlls / crypto_name,
        dlls / crypto_name.replace("libcrypto", "libssl"),
        dlls / "libffi-8.dll",
    ):
        path.write_bytes(path.name.encode())
    return root


@pytest.mark.parametrize("crypto_name", ["libcrypto-3-x64.dll", "libcrypto-3.dll"])
def test_runtime_discovery_accepts_supported_cpython_openssl_names(tmp_path, crypto_name):
    base = _interpreter_layout(tmp_path / "python", crypto_name=crypto_name)

    found = runtime.discover_runtime_files(base)

    assert {path.name for path in found} == {
        "python3.dll", "vcruntime140_1.dll", "tcl86t.dll", "tk86t.dll",
        crypto_name, crypto_name.replace("libcrypto", "libssl"), "libffi-8.dll",
    }


def test_runtime_preflight_identifies_the_missing_semantic_family(tmp_path):
    base = _interpreter_layout(tmp_path / "python")
    (base / "DLLs" / "libcrypto-3-x64.dll").unlink()

    with pytest.raises(ValueError, match="OpenSSL crypto runtime"):
        runtime.discover_runtime_files(base)


def test_runtime_staging_flattens_the_interpreter_files_into_onedir(tmp_path):
    files = runtime.discover_runtime_files(_interpreter_layout(tmp_path / "python"))
    destination = tmp_path / "onedir"
    destination.mkdir()

    runtime.stage_runtime_files(files, destination)

    assert {path.name for path in destination.iterdir()} == {path.name for path in files}
