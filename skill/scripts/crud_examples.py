#!/usr/bin/env python3
"""Create / update / delete examples for Jupyter, Inference, and Sandbox."""

from __future__ import annotations

import argparse
import os
import time
from typing import Optional, Union

from pyromind_sdk import PyroMindAPIClient
from pyromind_sdk.client.models import (
    InferenceJobRequest,
    JupyterRequest,
    ResourceConfig,
    SandboxRequest,
    SandboxType,
)


def _build_client(api_key: Optional[str], base_url: Optional[str]) -> PyroMindAPIClient:
    return PyroMindAPIClient(
        api_key=api_key or os.getenv("PYROMIND_API_KEY"),
        base_url=base_url or os.getenv("PYROMIND_BASE_URL"),
    )


def _ensure_no_duplicate(mode: str, client: PyroMindAPIClient, name: str) -> None:
    if mode == "jupyter":
        duplicates = [x for x in client.instance.list() if x.name == name]
    elif mode == "inference":
        duplicates = [x for x in client.inference.list() if x.name == name]
    else:
        duplicates = [x for x in client.sandboxes.list() if x.name == name]

    if duplicates:
        duplicate_ids = ", ".join(str(x.id) for x in duplicates)
        raise ValueError(
            f"Found existing {mode} resource(s) with name '{name}': {duplicate_ids}. "
            "Use a different --name or pass --allow-duplicate."
        )


def jupyter_crud(
    client: PyroMindAPIClient,
    name: str,
    updated_name: str,
    cpu: Union[int, str],
    memory: Union[int, str],
    updated_cpu: Union[int, str],
    updated_memory: Union[int, str],
    keep: bool,
) -> None:
    created = client.instance.create(
        JupyterRequest(
            name=name,
            resources=ResourceConfig(cpu=cpu, memory=memory),
        )
    )
    created_verified = client.instance.get_instance(created.id)
    print(f"[create] jupyter id={created_verified.id} status={created_verified.status} url={created_verified.url}")
    print("[check] jupyter creation verified by get_instance")

    updated = client.instance.update(
        created.id,
        JupyterRequest(
            name=updated_name,
            resources=ResourceConfig(cpu=updated_cpu, memory=updated_memory),
        ),
    )
    updated_verified = client.instance.get_instance(updated.id)
    print(f"[update] jupyter id={updated_verified.id} name={updated_verified.name} status={updated_verified.status}")
    print("[check] jupyter update verified by get_instance")

    if not keep:
        client.instance.pause(created.id)
        print(f"[pause]  jupyter id={created.id} (waiting for pause)")
        time.sleep(10)
        client.instance.delete(created.id)
        print(f"[delete] jupyter id={created.id}")


def inference_crud(
    client: PyroMindAPIClient,
    name: str,
    updated_name: str,
    model_path: str,
    framework: str,
    cpu: Union[int, str],
    memory: Union[int, str],
    updated_cpu: Union[int, str],
    updated_memory: Union[int, str],
    gpu: Union[int, str],
    gpu_card: Optional[str],
    keep: bool,
) -> None:
    created_id = client.inference.create(
        InferenceJobRequest(
            name=name,
            model_path=model_path,
            inference_framework=framework,
            resources=ResourceConfig(cpu=cpu, memory=memory, gpu=gpu, gpu_card=gpu_card),
        )
    )
    created = client.inference.get_job(created_id)
    print(f"[create] inference id={created.id} status={created.status} endpoint={created.endpoint_url}")
    print("[check] inference creation verified by get_job")

    client.inference.update(
        created_id,
        InferenceJobRequest(
            name=updated_name,
            model_path=model_path,
            inference_framework=framework,
            resources=ResourceConfig(cpu=updated_cpu, memory=updated_memory, gpu=gpu, gpu_card=gpu_card),
        ),
    )
    updated_verified = client.inference.get_job(created_id)
    print(f"[update] inference id={updated_verified.id} name={updated_verified.name} status={updated_verified.status}")
    print("[check] inference update verified by get_job")

    if not keep:
        client.inference.pause(created_id)
        print(f"[pause]  inference id={created_id} (waiting for pause)")
        time.sleep(10)
        client.inference.delete(created_id)
        print(f"[delete] inference id={created_id}")


def sandbox_crud(
    client: PyroMindAPIClient,
    name: str,
    updated_name: str,
    cpu: Union[int, str],
    memory: Union[int, str],
    updated_cpu: Union[int, str],
    updated_memory: Union[int, str],
    keep: bool,
) -> None:
    created = client.sandboxes.create(
        SandboxRequest(
            sandbox_type=SandboxType.LINUX,
            resources=ResourceConfig(cpu=cpu, memory=memory),
            name=name,
        )
    )
    created_verified = client.sandboxes.get_sandbox(created.id)
    print(f"[create] sandbox id={created_verified.id} status={created_verified.status}")
    print("[check] sandbox creation verified by get_sandbox")

    updated = client.sandboxes.update(
        created.id,
        SandboxRequest(
            sandbox_type=SandboxType.LINUX,
            resources=ResourceConfig(cpu=updated_cpu, memory=updated_memory),
            name=updated_name,
        ),
    )
    updated_verified = client.sandboxes.get_sandbox(updated.id)
    print(f"[update] sandbox id={updated_verified.id} name={updated_verified.name} status={updated_verified.status}")
    print("[check] sandbox update verified by get_sandbox")

    if not keep:
        client.sandboxes.pause(created.id)
        print(f"[pause]  sandbox id={created.id} (waiting for pause)")
        time.sleep(10)
        client.sandboxes.delete(created.id)
        print(f"[delete] sandbox id={created.id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="CRUD examples for PyroMind resources.")
    parser.add_argument("--mode", required=True, choices=["jupyter", "inference", "sandbox"])
    parser.add_argument("--api-key", default=None, help="PyroMind API key (or set PYROMIND_API_KEY).")
    parser.add_argument("--base-url", default=None, help="PyroMind base URL (or set PYROMIND_BASE_URL).")
    parser.add_argument("--name", default="crud-demo", help="Name used at create.")
    parser.add_argument("--updated-name", default="crud-demo-updated", help="Name used at update.")
    parser.add_argument("--cpu", type=int, default=2, help="CPU cores at create.")
    parser.add_argument("--memory", type=int, default=4, help="Memory in Gi at create.")
    parser.add_argument("--updated-cpu", type=int, default=4, help="CPU cores at update.")
    parser.add_argument("--updated-memory", type=int, default=8, help="Memory in Gi at update.")
    parser.add_argument("--model-path", default="/workspace/models/qwen", help="Inference model path.")
    parser.add_argument("--framework", default="vllm", help="Inference framework.")
    parser.add_argument("--gpu", type=int, default=1, help="Inference GPU count.")
    parser.add_argument("--gpu-card", default=None, help="Inference GPU card (e.g. L40S).")
    parser.add_argument("--keep", action="store_true", help="Keep resource after update (skip delete).")
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Allow creating resource even if same-name resource already exists.",
    )
    args = parser.parse_args()

    client = _build_client(args.api_key, args.base_url)
    try:
        if not args.allow_duplicate:
            _ensure_no_duplicate(args.mode, client, args.name)

        if args.mode == "jupyter":
            jupyter_crud(
                client,
                args.name,
                args.updated_name,
                args.cpu,
                args.memory,
                args.updated_cpu,
                args.updated_memory,
                args.keep,
            )
        elif args.mode == "inference":
            inference_crud(
                client,
                args.name,
                args.updated_name,
                args.model_path,
                args.framework,
                args.cpu,
                args.memory,
                args.updated_cpu,
                args.updated_memory,
                args.gpu,
                args.gpu_card,
                args.keep,
            )
        else:
            sandbox_crud(
                client,
                args.name,
                args.updated_name,
                args.cpu,
                args.memory,
                args.updated_cpu,
                args.updated_memory,
                args.keep,
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
