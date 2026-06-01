"""GPU echo node - outputs GPU resource info for flow test verification."""

import socket

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


def gpu_echo(job_info: str = "") -> dict:
    gpu_count = "0"
    gpu_name = "N/A"
    hostname = "unknown"
    if TORCH_AVAILABLE and torch.cuda.is_available():
        gpu_count = str(torch.cuda.device_count())
        gpu_name = str(torch.cuda.get_device_name(0))
    hostname = str(socket.gethostname())
    return {
        "output_gpu_count": gpu_count,
        "gpu_name": gpu_name,
        "hostname": hostname,
        "output_job_info": job_info,
        "torch_available": str(TORCH_AVAILABLE),
    }
