"""Create a reproducible ZIP and checksum for a complete release directory."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _archive_name(root: Path, path: Path) -> str:
    return (Path(root.name) / path.relative_to(root)).as_posix()


def _zip_info(name: str, *, directory: bool = False) -> zipfile.ZipInfo:
    if directory and not name.endswith("/"):
        name += "/"
    info = zipfile.ZipInfo(name, _ZIP_EPOCH)
    info.create_system = 3
    mode = 0o755 if directory or name.lower().endswith(".exe") else 0o644
    kind = 0o040000 if directory else 0o100000
    info.external_attr = (kind | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_archive(source: Path, output: Path) -> str:
    """Archive ``source`` with stable ordering and metadata, then write ``.sha256``."""
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"release directory does not exist: {source}")
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive output must not be inside the archived directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (path for path in source.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    directories = sorted(
        (path for path in source.rglob("*") if path.is_dir()),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    if not files:
        raise ValueError(f"release directory contains no files: {source}")

    fd, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.writestr(_zip_info(f"{source.name}/", directory=True), b"")
            for directory in directories:
                archive.writestr(
                    _zip_info(_archive_name(source, directory), directory=True), b""
                )
            for path in files:
                archive.writestr(_zip_info(_archive_name(source, path)), path.read_bytes())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

    checksum = _sha256(output)
    checksum_path = output.with_name(output.name + ".sha256")
    checksum_path.write_text(f"{checksum}  {output.name}\n", encoding="ascii", newline="\n")
    return checksum


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a deterministic ZIP of the complete release directory."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    checksum = build_archive(args.source, args.output)
    print(f"{checksum}  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
