# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Insert the SPDX header into every source file that `licensing.json` requires it in.

Idempotent: a file that already carries the header is left byte-for-byte alone. Run it after
adding source files, or with ``--check`` to list what is missing without writing.
"""

import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def load_policy():
    return json.loads((ROOT / "licensing.json").read_text(encoding="utf-8"))


# Never write into a dependency tree. .gitignore already excludes these, but this tool stamps a
# copyright line into files, so it does not rely on one list being complete: putting a first-party
# notice on third-party source would be a licensing defect, not an inconvenience.
FOREIGN_SEGMENTS = frozenset({
    ".venv", "venv", "env", "site-packages", "node_modules", "build", "dist"})


def candidate_paths():
    """Tracked files plus new files that are not ignored, so a header lands before the commit.

    The automated suite deliberately checks tracked files only: CI enforces what the repository
    actually contains, and a developer's scratch file does not turn the suite red.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return sorted({line for line in listing.splitlines()
                   if line and not FOREIGN_SEGMENTS.intersection(PurePosixPath(line).parts)})


def matching_rule(policy, path):
    for rule in policy["rules"]:
        for pattern in rule["match"]:
            if pattern == "**":
                return rule
            if pattern.endswith("/**"):
                if path == pattern[:-3] or path.startswith(pattern[:-2]):
                    return rule
            elif fnmatch.fnmatchcase(path, pattern):
                return rule
    return None


def required_header(policy, path):
    """``(comment_prefix, spdx_id)`` this path must carry, or None if it needs no inline header."""
    rule = matching_rule(policy, path)
    if rule is None or rule["headers"] != "auto":
        return None
    prefix = policy["comment_prefixes"].get(PurePosixPath(path).suffix)
    return None if prefix is None else (prefix, rule["license"])


def header_lines(policy, prefix, spdx_id):
    return [f"{prefix} SPDX-FileCopyrightText: {policy['copyright']}",
            f"{prefix} SPDX-License-Identifier: {spdx_id}"]


def has_header(text, prefix):
    expected = f"{prefix} SPDX-License-Identifier:"
    return any(line.startswith(expected) for line in text.splitlines()[:10])


def detect_eol(text):
    index = text.find("\n")
    return "\r\n" if index > 0 and text[index - 1] == "\r" else "\n"


def insert_header(text, policy, prefix, spdx_id):
    """Insert the header after any language-required prologue, keeping the file's line endings."""
    eol = detect_eol(text)
    lines = text.splitlines(keepends=True)
    cut = 0
    while cut < len(lines) and any(
            lines[cut].startswith(start) for start in policy["prologue_prefixes"]):
        cut += 1
    block = [line + eol for line in header_lines(policy, prefix, spdx_id)]
    if cut < len(lines) and lines[cut].strip():
        block.append(eol)
    return "".join(lines[:cut] + block + lines[cut:])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report files missing the header without modifying them")
    args = parser.parse_args(argv)

    policy = load_policy()
    missing = []
    for relative in candidate_paths():
        required = required_header(policy, relative)
        if required is None:
            continue
        prefix, spdx_id = required
        path = ROOT / relative
        # newline="" everywhere: read and write the file's own line endings untranslated, so a
        # header insertion never rewrites CRLF as LF or the reverse. Path.read_text gained the
        # keyword only in 3.13, and this repository supports older interpreters.
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        if has_header(text, prefix):
            continue
        missing.append(relative)
        if not args.check:
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(insert_header(text, policy, prefix, spdx_id))

    verb = "missing the SPDX header" if args.check else "updated"
    for relative in missing:
        print(f"{verb}: {relative}")
    print(f"{len(missing)} file(s) {verb}.")
    return 1 if (args.check and missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
