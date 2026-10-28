"""Fingerprint the rift-client Go sources so stale binaries cannot ship.

``bin/rift-client-linux-*`` is built by hand (``make build-linux``) and packaged
into the wheel. Nothing about the binaries themselves says which Go revision
they came from, so a release built after editing ``client/*.go`` would silently
ship the previous build. ``make build-linux`` records a fingerprint of the
sources here; the wheel build refuses to package binaries that no longer match.

Stdlib only: ``setup.py`` loads this module by path before the package exists.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RIFT_DIR = Path(__file__).resolve().parent
CLIENT_DIR = RIFT_DIR / "client"
BIN_DIR = RIFT_DIR / "bin"
FINGERPRINT_FILE = BIN_DIR / "rift-client.source.sha256"
BINARIES = tuple(BIN_DIR / f"rift-client-linux-{arch}" for arch in ("amd64", "arm64"))


def source_files() -> list[Path]:
    """Everything that changes the produced binary (tests excluded)."""
    files = [
        CLIENT_DIR / "go.mod",
        CLIENT_DIR / "go.sum",
        CLIENT_DIR / "Makefile",
    ]
    files += sorted(
        path
        for path in CLIENT_DIR.glob("*.go")
        if not path.name.endswith("_test.go")
    )
    files += sorted((CLIENT_DIR / "pb").glob("*.go"))
    files.append(RIFT_DIR / "proto" / "rift.proto")
    return [path for path in files if path.is_file()]


def fingerprint() -> str:
    digest = hashlib.sha256()
    for path in source_files():
        digest.update(path.relative_to(RIFT_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def write() -> str:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    value = fingerprint()
    FINGERPRINT_FILE.write_text(value + "\n", encoding="utf-8")
    return value


def problems() -> list[str]:
    issues = [
        f"missing {binary.name}"
        for binary in BINARIES
        if not binary.is_file()
    ]
    if not FINGERPRINT_FILE.is_file():
        issues.append(f"missing {FINGERPRINT_FILE.name}")
        return issues
    recorded = FINGERPRINT_FILE.read_text(encoding="utf-8").strip()
    current = fingerprint()
    if recorded != current:
        issues.append(
            "Go sources changed after the last build "
            f"(recorded {recorded[:12]}, now {current[:12]})"
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--write" in args:
        print(write())
        return 0
    issues = problems()
    if issues:
        print("rift-client binaries are stale:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        print(f"Run `make build-linux` in {CLIENT_DIR}.", file=sys.stderr)
        return 1
    print("rift-client binaries match the Go sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
