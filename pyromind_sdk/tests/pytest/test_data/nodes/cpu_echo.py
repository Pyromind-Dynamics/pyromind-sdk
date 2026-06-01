"""CPU echo node - outputs CPU resource info for flow test verification."""

import os
import socket


def cpu_echo(job_info: str = "") -> dict:
    cpu_count = "0"
    total_memory_gb = "N/A"
    hostname = "unknown"
    cpu_count = str(os.cpu_count() or 0)
    hostname = str(socket.gethostname())
    try:
        total_memory_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        total_memory_gb = str(int(total_memory_bytes / (1024 ** 3)))
    except Exception:
        total_memory_gb = 'N/A'
    return {
        "cpu_count": cpu_count,
        "total_memory_gb": total_memory_gb,
        "hostname": hostname,
        "output_job_info": job_info,
    }
