"""Entry point: ``python -m webui``.

Starts a local web server hosting the batch-download UI. Options can be set via
environment variables:

    XHS_WEBUI_HOST  (default: 127.0.0.1)
    XHS_WEBUI_PORT  (default: 5557)
"""

from os import getenv

import uvicorn


def main() -> None:
    host = getenv("XHS_WEBUI_HOST", "127.0.0.1")
    port = int(getenv("XHS_WEBUI_PORT", "5557"))
    print(f"XHS-Downloader Web UI running at http://{host}:{port}")
    uvicorn.run("webui.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
