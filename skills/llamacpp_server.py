"""
skills/llamacpp_server.py — Managed llama-server subprocess for Kitezh.

Starts a ``llama-server`` process, waits until its HTTP API is ready, and
cleans it up on shutdown.  Can be used as a context manager::

    with LlamaCppServer(model_path="/path/to/model.gguf") as srv:
        # llama-server is running on http://localhost:8080
        ...

Or managed manually::

    srv = LlamaCppServer(model_path="/path/to/model.gguf")
    srv.start()
    ...
    srv.stop()
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import requests

import config

if TYPE_CHECKING:
    pass

logger = logging.getLogger("kitezh.llamacpp_server")

_READY_TIMEOUT_SECONDS = 120
_POLL_INTERVAL_SECONDS = 1.0
#: Timeout in seconds for each GET request to the /health endpoint.
_HEALTH_CHECK_TIMEOUT_SECONDS = 2
#: Seconds to wait for llama-server to exit gracefully before killing it.
_TERMINATE_TIMEOUT_SECONDS = 10


class LlamaCppServer:
    """Subprocess wrapper around ``llama-server``.

    Parameters
    ----------
    model_path:
        Path to the ``.gguf`` model file to load.
    bin_path:
        Path to the ``llama-server`` executable.  Defaults to
        ``KITEZH_LLAMACPP_SERVER_BIN`` / ``config.LLAMACPP_SERVER_BIN``.
    host:
        Hostname the server should bind to.
    port:
        Port the server should listen on (extracted from
        ``config.LLAMACPP_BASE_URL`` when *None*).
    n_ctx:
        Context-window size passed via ``--ctx-size``.
    n_gpu_layers:
        Number of layers to offload to GPU via ``--n-gpu-layers``.
    """

    def __init__(
        self,
        model_path: str | None = None,
        bin_path: str | None = None,
        host: str = "127.0.0.1",
        port: int | None = None,
        n_ctx: int | None = None,
        n_gpu_layers: int | None = None,
    ) -> None:
        self.model_path = model_path or config.LLAMACPP_MODEL_PATH
        self.bin_path = bin_path or config.LLAMACPP_SERVER_BIN
        self.host = host
        # Derive port from LLAMACPP_BASE_URL when not given explicitly.
        if port is None:
            try:
                from urllib.parse import urlparse
                port = int(urlparse(config.LLAMACPP_BASE_URL).port or 8080)
            except ValueError:
                port = 8080
        self.port = port
        self.n_ctx = n_ctx or config.LLAMACPP_SERVER_N_CTX
        # Use `is not None` rather than `or` here: 0 is a valid value meaning
        # "CPU-only, no GPU offload" and must not be treated as falsy.
        self.n_gpu_layers = n_gpu_layers if n_gpu_layers is not None else config.LLAMACPP_SERVER_N_GPU_LAYERS
        self._process: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch llama-server and block until the /health endpoint responds."""
        if self._process is not None and self._process.poll() is None:
            logger.info("LlamaCppServer: already running (pid=%d).", self._process.pid)
            return

        if not self.model_path:
            raise RuntimeError(
                "No model path configured for llama-server.  "
                "Set KITEZH_LLAMACPP_MODEL_PATH or pass --llama-server-model."
            )

        resolved_bin = shutil.which(self.bin_path) or self.bin_path
        if not Path(resolved_bin).exists():
            raise RuntimeError(
                f"llama-server binary not found: '{resolved_bin}'.  "
                "Set KITEZH_LLAMACPP_SERVER_BIN to the correct path."
            )

        if not Path(self.model_path).exists():
            raise RuntimeError(
                f"Model file not found: '{self.model_path}'.  "
                "Set KITEZH_LLAMACPP_MODEL_PATH to the correct .gguf path."
            )

        cmd = [
            resolved_bin,
            "--model", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "--ctx-size", str(self.n_ctx),
            "--n-gpu-layers", str(self.n_gpu_layers),
        ]
        logger.info("LlamaCppServer: starting %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            # Inherit stderr so llama-server error messages reach the user's terminal.
            stderr=None,
        )
        logger.info("LlamaCppServer: process started (pid=%d), waiting for ready…", self._process.pid)
        self._wait_for_ready()

    def stop(self) -> None:
        """Terminate the managed llama-server process if it is running."""
        if self._process is None:
            return
        if self._process.poll() is None:
            logger.info("LlamaCppServer: terminating pid=%d…", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("LlamaCppServer: process did not exit; killing.")
                self._process.kill()
        self._process = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LlamaCppServer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_for_ready(self) -> None:
        health_url = f"http://{self.host}:{self.port}/health"
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            # Bail early if the process already died.
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"llama-server process exited unexpectedly (rc={self._process.returncode}) "
                    "before becoming ready.  Check the model path and binary."
                )
            try:
                resp = requests.get(health_url, timeout=_HEALTH_CHECK_TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    logger.info("LlamaCppServer: ready at %s:%d ✓", self.host, self.port)
                    return
            except requests.exceptions.RequestException:
                pass
            time.sleep(_POLL_INTERVAL_SECONDS)
        self.stop()
        raise RuntimeError(
            f"llama-server did not become ready within {_READY_TIMEOUT_SECONDS}s."
        )
