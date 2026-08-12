"""docker-rt daemon entrypoint (Unix socket or TCP).

Uses aiohttp so ``docker exec -it`` TCP Upgrade works. FastAPI app in
``app.py`` remains available for non-interactive / test use via uvicorn.
"""

from __future__ import annotations


def main() -> None:
    from .aio_server import main as aio_main

    aio_main()


if __name__ == "__main__":
    main()
