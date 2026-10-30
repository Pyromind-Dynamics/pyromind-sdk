#!/usr/bin/env python3
"""Build and verify the rift data-plane release artifacts.

One command produces what the exec/cp rollout needs:

1. ``make build-linux`` (both architectures, version injected via ldflags,
   ``rift-client.sha256`` + ``rift-client.version`` written next to them);
2. the proto/generated-binding consistency check;
3. a wheel built from this tree, asserted to contain both binaries, the
   checksums and the version file.

Usage::

    python scripts/release_rift.py [--skip-proto] [--skip-gateway-check]
                                   [--dist DIR]

Exit code 0 means the artifacts are ready to publish.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIFT = REPO / "pyromind_sdk" / "rift"
CLIENT = RIFT / "client"
BIN = RIFT / "bin"
ARCHES = ("amd64", "arm64")


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"+ {' '.join(cmd)} (cwd={cwd})")
    subprocess.run(cmd, cwd=cwd, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_binaries() -> tuple[str, list[tuple[str, str]]]:
    version_file = BIN / "rift-client.version"
    if not version_file.is_file():
        raise SystemExit(f"{version_file} is missing; run `make build-linux` first")
    version = version_file.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit(f"{version_file} is empty")

    sums = {}
    sha_file = BIN / "rift-client.sha256"
    if not sha_file.is_file():
        raise SystemExit(f"{sha_file} is missing")
    for line in sha_file.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[Path(parts[-1]).name] = parts[0]

    checks = []
    for arch in ARCHES:
        binary = BIN / f"rift-client-linux-{arch}"
        if not binary.is_file():
            raise SystemExit(f"missing {binary}")
        actual = _sha256(binary)
        if sums.get(binary.name) != actual:
            raise SystemExit(
                f"{binary.name}: sha256 mismatch (file {actual[:12]}, "
                f"manifest {str(sums.get(binary.name))[:12]})"
            )
        checks.append((binary.name, actual))
    return version, checks


def _check_proto(skip_gateway: bool) -> None:
    cmd = [sys.executable, str(RIFT / "check_proto.py")]
    if skip_gateway:
        cmd.append("--skip-gateway")
    _run(cmd, REPO)


def _build_artifacts(dist: Path) -> tuple[Path, Path]:
    dist.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(dist),
        ],
        REPO,
    )
    wheels = sorted(dist.glob("*.whl"), key=lambda path: path.stat().st_mtime)
    sdists = sorted(dist.glob("*.tar.gz"), key=lambda path: path.stat().st_mtime)
    if not wheels or not sdists:
        raise SystemExit(f"expected a wheel and an sdist in {dist}")
    return sdists[-1], wheels[-1]


def _verify_wheel(wheel: Path, version: str) -> None:
    expected_tail = [
        f"bin/rift-client-linux-{arch}" for arch in ARCHES
    ] + ["bin/rift-client.sha256", "bin/rift-client.version"]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for tail in expected_tail:
            matches = [name for name in names if name.endswith(tail)]
            if not matches:
                raise SystemExit(f"{wheel.name} is missing {tail}")
        version_name = next(
            name for name in names if name.endswith("bin/rift-client.version")
        )
        packaged = archive.read(version_name).decode("utf-8").strip()
        if packaged != version:
            raise SystemExit(
                f"{wheel.name}: packaged version {packaged!r} != {version!r}"
            )
        # Runtime package: Go sources must not be shipped.
        leaked = [name for name in names if name.endswith((".go", "go.mod", "go.sum"))]
        if leaked:
            raise SystemExit(
                f"{wheel.name}: Go sources leaked into the wheel: {leaked[:3]}"
            )


def _verify_sdist(sdist: Path, version: str) -> None:
    """The sdist must still carry the binaries and the Go sources."""
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        for arch in ARCHES:
            tail = f"bin/rift-client-linux-{arch}"
            if not any(name.endswith(tail) for name in names):
                raise SystemExit(f"{sdist.name} is missing {tail}")
        go_files = [name for name in names if name.endswith(".go")]
        if len(go_files) < 6:
            raise SystemExit(
                f"{sdist.name}: expected the Go sources for rebuilds, found "
                f"{len(go_files)} .go files"
            )
        for tail in (
            "rift/client/main.go",
            "rift/client/server.go",
            "rift/client/go.mod",
            "rift/client/Makefile",
            "rift/proto/rift.proto",
        ):
            if not any(name.endswith(tail) for name in names):
                raise SystemExit(f"{sdist.name} is missing {tail}")
        version_name = next(
            (name for name in names if name.endswith("bin/rift-client.version")),
            None,
        )
        if version_name is None:
            raise SystemExit(f"{sdist.name} is missing bin/rift-client.version")
        packaged = archive.extractfile(version_name).read().decode("utf-8").strip()
        if packaged != version:
            raise SystemExit(
                f"{sdist.name}: packaged version {packaged!r} != {version!r}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-proto", action="store_true")
    parser.add_argument(
        "--skip-gateway-check",
        action="store_true",
        help="do not diff against the sibling rift-gateway checkout",
    )
    parser.add_argument("--dist", default=str(REPO / "dist"))
    args = parser.parse_args(argv)

    _run(["make", "build-linux"], CLIENT)
    version, checks = _check_binaries()
    # make writes the source fingerprint; assert it so a hand-edited bin/ cannot
    # slip past the wheel guard.
    _run([sys.executable, str(RIFT / "source_fingerprint.py")], REPO)

    if not args.skip_proto:
        _check_proto(skip_gateway=args.skip_gateway_check)

    sdist, wheel = _build_artifacts(Path(args.dist))
    _verify_sdist(sdist, version)
    _verify_wheel(wheel, version)

    print()
    print(f"record:   {version}")
    for name, digest in checks:
        print(f"binary:   {digest}  {name}")
    print(f"sdist:    {_sha256(sdist)}  {sdist.name}")
    print(f"wheel:    {_sha256(wheel)}  {wheel.name}")
    print("release artifacts OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
