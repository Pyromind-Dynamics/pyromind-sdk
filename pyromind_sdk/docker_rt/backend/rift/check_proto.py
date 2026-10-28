#!/usr/bin/env python3
"""Check that ``rift.proto`` and both generated bindings agree.

The canonical schema lives in ``rift-gateway/proto/rift.proto``; the SDK keeps a
copy so it can regenerate ``pb/`` without the gateway checkout. Two kinds of
drift only show up at runtime otherwise:

* the two proto copies diverge (one repo got a field the other did not);
* a proto change was not followed by ``protoc`` (Go) / ``grpc_tools`` (Python).

Usage::

    python check_proto.py [--gateway-dir DIR] [--skip-gateway]

Exit code 0 means "in sync"; 1 lists every mismatch.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROTO = _HERE / "proto" / "rift.proto"
_GO_PB = _HERE / "client" / "pb" / "rift.pb.go"
_GO_GRPC = _HERE / "client" / "pb" / "rift_grpc.pb.go"
_REPO_ROOT = _HERE.parents[3]


def _normalized_proto(text: str) -> list[str]:
    """Proto text without the per-repo ``go_package`` line and blank tails."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("option go_package"):
            continue
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _camel(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def _descriptor_label(label: int) -> str:
    return {2: "required", 3: "repeated"}.get(label, "optional")


def _python_label(field) -> str:
    """``FieldDescriptor.label`` was removed in protobuf 6; use the flags."""
    is_repeated = getattr(field, "is_repeated", None)
    if is_repeated is None:
        is_repeated = field.label == 3
    if is_repeated:
        return "repeated"
    is_required = getattr(field, "is_required", None)
    if is_required is None:
        is_required = field.label == 2
    return "required" if is_required else "optional"


def _descriptor_model(proto: Path):
    from google.protobuf import descriptor_pb2
    from grpc_tools import protoc

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rift.pb"
        rc = protoc.main(
            [
                "protoc",
                f"-I{proto.parent}",
                f"--descriptor_set_out={out}",
                "--include_imports",
                str(proto),
            ]
        )
        if rc != 0:
            raise SystemExit(f"protoc failed for {proto} (exit {rc})")
        fds = descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())

    messages: dict[str, dict] = {}
    services: dict[str, list] = {}
    for file in fds.file:
        for message in file.message_type:
            oneofs = [decl.name for decl in message.oneof_decl]
            fields = []
            for field in message.field:
                fields.append(
                    {
                        "name": field.name,
                        "number": field.number,
                        "type": field.type,
                        "label": _descriptor_label(field.label),
                        "type_name": field.type_name.lstrip("."),
                        "oneof": (
                            oneofs[field.oneof_index]
                            if field.HasField("oneof_index")
                            else None
                        ),
                    }
                )
            messages[message.name] = {"fields": fields, "oneofs": oneofs}
        for service in file.service:
            services[service.name] = [
                (
                    method.name,
                    method.input_type.lstrip("."),
                    method.output_type.lstrip("."),
                    method.client_streaming,
                    method.server_streaming,
                )
                for method in service.method
            ]
    return messages, services


def _python_model():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from pyromind_sdk.docker_rt.backend.rift.pb import rift_pb2

    messages: dict[str, dict] = {}
    for name, message in rift_pb2.DESCRIPTOR.message_types_by_name.items():
        oneofs = [oneof.name for oneof in message.oneofs]
        fields = [
            {
                "name": field.name,
                "number": field.number,
                "type": field.type,
                "label": _python_label(field),
                "type_name": (
                    field.message_type.full_name if field.message_type else ""
                ),
                "oneof": field.containing_oneof.name if field.containing_oneof else None,
            }
            for field in message.fields
        ]
        messages[name] = {"fields": fields, "oneofs": oneofs}
    services = {
        name: [
            (
                method.name,
                method.input_type.full_name,
                method.output_type.full_name,
                method.client_streaming,
                method.server_streaming,
            )
            for method in service.methods
        ]
        for name, service in rift_pb2.DESCRIPTOR.services_by_name.items()
    }
    return messages, services


def _compare(label: str, want, got, problems: list[str]) -> None:
    if want != got:
        problems.append(f"{label}:\n  proto:  {want}\n  {label}: {got}")


def _check_go(pb_path: Path, grpc_path: Path, messages, services, problems) -> None:
    source = pb_path.read_text(encoding="utf-8")
    grpc_source = grpc_path.read_text(encoding="utf-8")
    for name, spec in messages.items():
        body = re.search(
            rf"type {re.escape(name)} struct \{{(.*?)\n\}}", source, re.S
        )
        if body is None:
            problems.append(f"Go: missing message {name} (regenerate pb)")
            continue
        for field in spec["fields"]:
            tag = f"name={field['name']}"
            if field["oneof"]:
                wrapper = f"{name}_{_camel(field['name'])}"
                if f"type {wrapper} struct" not in source:
                    problems.append(f"Go: missing oneof member {wrapper}")
                continue
            if tag not in body.group(1):
                problems.append(f"Go: {name} lacks field {field['name']}")
        for oneof in spec["oneofs"]:
            if f'protobuf_oneof:"{oneof}"' not in body.group(1):
                problems.append(f"Go: {name} lacks oneof {oneof}")
    for service, methods in services.items():
        for name, *_ in methods:
            if f"func (c *riftClient) {name}(" not in grpc_source:
                problems.append(f"Go: {service} lacks method {name}")


def _check_python(sdk_messages, sdk_services, problems) -> None:
    py_messages, py_services = _python_model()
    _compare("python messages", sdk_messages, py_messages, problems)
    _compare("python services", sdk_services, py_services, problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-dir",
        default=str(_REPO_ROOT.parent / "rift-gateway"),
        help="rift-gateway checkout (default: sibling directory)",
    )
    parser.add_argument(
        "--skip-gateway",
        action="store_true",
        help="only check the SDK copy and its bindings",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    messages, services = _descriptor_model(_PROTO)

    if not args.skip_gateway:
        gateway_proto = Path(args.gateway_dir) / "proto" / "rift.proto"
        if gateway_proto.is_file():
            gateway_lines = _normalized_proto(gateway_proto.read_text(encoding="utf-8"))
            sdk_lines = _normalized_proto(_PROTO.read_text(encoding="utf-8"))
            if gateway_lines != sdk_lines:
                problems.append(
                    f"proto drift: {_PROTO} != {gateway_proto} "
                    "(ignoring option go_package)"
                )
            gateway_go = Path(args.gateway_dir) / "gen" / "riftpb" / "rift.pb.go"
            gateway_grpc = Path(args.gateway_dir) / "gen" / "riftpb" / "rift_grpc.pb.go"
            if gateway_go.is_file() and gateway_grpc.is_file():
                _check_go(gateway_go, gateway_grpc, messages, services, problems)
        else:
            print(
                f"check_proto: {gateway_proto} not found; skipped the gateway side",
                file=sys.stderr,
            )

    _check_go(_GO_PB, _GO_GRPC, messages, services, problems)
    _check_python(messages, services, problems)

    if problems:
        print("check_proto: FAILED", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("check_proto: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
