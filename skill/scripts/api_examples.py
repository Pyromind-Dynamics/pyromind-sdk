#!/usr/bin/env python3
"""Run simple API examples for Jupyter, Inference, and Sandbox."""

from __future__ import annotations

import argparse
import os
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
    if api_key is None:
        api_key = os.getenv("PYROMIND_API_KEY")
    if base_url is None:
        base_url = os.getenv("PYROMIND_BASE_URL")
    return PyroMindAPIClient(api_key=api_key, base_url=base_url)


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


def run_jupyter(client: PyroMindAPIClient, name: str, cpu: Union[int, str], memory: Union[int, str]) -> None:
    instance = client.instance.create(
        JupyterRequest(
            name=name,
            resources=ResourceConfig(cpu=cpu, memory=memory),
        )
    )
    verified = client.instance.get_instance(instance.id)

    print("Created Jupyter instance")
    print(f"- id: {verified.id}")
    print(f"- status: {verified.status}")
    print(f"- url: {verified.url}")
    print("- verification: get_instance succeeded")


def run_inference(
    client: PyroMindAPIClient,
    name: str,
    model_path: str,
    framework: str,
    cpu: Union[int, str],
    memory: Union[int, str],
    gpu: Union[int, str],
    gpu_card: Optional[str],
) -> None:
    job_id = client.inference.create(
        InferenceJobRequest(
            name=name,
            model_path=model_path,
            inference_framework=framework,
            resources=ResourceConfig(
                cpu=cpu,
                memory=memory,
                gpu=gpu,
                gpu_card=gpu_card,
            ),
        )
    )
    job = client.inference.get_job(job_id)
    print("Created inference job")
    print(f"- id: {job.id}")
    print(f"- status: {job.status}")
    print(f"- endpoint: {job.endpoint_url}")
    print("- verification: get_job succeeded")


def run_sandbox(client: PyroMindAPIClient, name: str, cpu: Union[int, str], memory: Union[int, str]) -> None:
    sandbox = client.sandboxes.create(
        SandboxRequest(
            sandbox_type=SandboxType.LINUX,
            resources=ResourceConfig(cpu=cpu, memory=memory),
            name=name,
        )
    )
    verified = client.sandboxes.get_sandbox(sandbox.id)
    print("Created sandbox")
    print(f"- id: {verified.id}")
    print(f"- status: {verified.status}")
    print("- verification: get_sandbox succeeded")

    vnc = client.sandboxes.get_vnc(sandbox.id)
    print(f"- web_vnc_url: {vnc.get('web_vnc_url')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PyroMind API examples.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["jupyter", "inference", "sandbox"],
        help="Which example to run.",
    )
    parser.add_argument("--api-key", default=None, help="PyroMind API key.")
    parser.add_argument("--base-url", default=None, help="PyroMind API base URL.")
    parser.add_argument("--name", default="skill-demo", help="Resource name.")
    parser.add_argument("--cpu", type=int, default=2, help="CPU cores.")
    parser.add_argument("--memory", type=int, default=4, help="Memory (Gi).")
    parser.add_argument(
        "--model-path",
        default="/workspace/models/qwen",
        help="Model path for inference mode.",
    )
    parser.add_argument(
        "--framework",
        default="vllm",
        help="Inference framework for inference mode.",
    )
    parser.add_argument("--gpu", type=int, default=1, help="GPU count (inference mode).")
    parser.add_argument("--gpu-card", default=None, help="GPU card (e.g., L40S).")
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Allow creating resource even if same-name resource already exists.",
    )
    args = parser.parse_args()

    client = _build_client(api_key=args.api_key, base_url=args.base_url)
    try:
        if not args.allow_duplicate:
            _ensure_no_duplicate(args.mode, client, args.name)

        if args.mode == "jupyter":
            run_jupyter(client, args.name, args.cpu, args.memory)
        elif args.mode == "inference":
            run_inference(
                client,
                args.name,
                args.model_path,
                args.framework,
                args.cpu,
                args.memory,
                args.gpu,
                args.gpu_card,
            )
        else:
            run_sandbox(client, args.name, args.cpu, args.memory)
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
