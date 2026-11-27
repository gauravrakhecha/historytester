"""
quantdesk.integrations.openalgo_manager — loose-coupled OpenAlgo sidecar detector & launcher.

Strictly decoupled from OpenAlgo's Python package (ZERO ``import openalgo``).
Talks to any running OpenAlgo Web/Desktop instance over HTTP via
``quantdesk.brokers.openalgo.ping_openalgo``, and optionally detects/launches
an isolated sidecar installed inside ``services/openalgo/.venv``.
"""
from __future__ import annotations

import socket
import subprocess
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from quantdesk.config import ROOT_DIR
from quantdesk.core.logging import get_logger

log = get_logger("integrations.openalgo_manager")

DEFAULT_SIDECAR_DIR: Path = ROOT_DIR / "services" / "openalgo"
_SIDECAR_PROC: subprocess.Popen | None = None
_SIDECAR_LOG_FH: Any = None


def _resolve_sidecar_dir(services_dir=None) -> Path:
    return Path(services_dir) if services_dir is not None else DEFAULT_SIDECAR_DIR


def _sidecar_venv_python(sidecar_dir: Path) -> Path | None:
    for candidate in (
        sidecar_dir / ".venv" / "Scripts" / "python.exe",
        sidecar_dir / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return None


def _is_sidecar_installed(sidecar_dir: Path) -> bool:
    return sidecar_dir.is_dir() and (sidecar_dir / ".venv").is_dir()


def _is_proc_alive() -> bool:
    global _SIDECAR_PROC
    if _SIDECAR_PROC is None:
        return False
    if _SIDECAR_PROC.poll() is not None:
        _SIDECAR_PROC = None
        return False
    return True


def _parse_host_port(host_url: str | None) -> tuple[str, str, int]:
    raw = (host_url or "http://127.0.0.1:5000").strip().rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        port = int(parsed.port)
    elif parsed.scheme == "https":
        port = 443
    elif host in ("127.0.0.1", "localhost"):
        port = 5000
    else:
        port = 80
    clean_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return clean_url, host, port


def is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    """Return True if a TCP connection to ``(host, port)`` succeeds within ``timeout``."""
    if not host or not port:
        return False
    try:
        with socket.create_connection((str(host), int(port)), timeout=float(timeout)):
            return True
    except (OSError, ValueError):
        return False


def get_openalgo_status(
    host_url: str = "http://127.0.0.1:5000",
    api_key: str = "",
    services_dir=None,
) -> dict[str, Any]:
    """Probe OpenAlgo socket reachability, API ping, and optional local sidecar status."""
    clean_url, host, port = _parse_host_port(host_url)
    sdir = _resolve_sidecar_dir(services_dir)
    sidecar_installed = _is_sidecar_installed(sdir)
    reachable = is_port_open(host, port)

    ping_ok = False
    latency_ms: float | None = None
    broker = ""
    analyzer_active = False
    mode = "Unknown"
    message = ""

    clean_key = (api_key or "").strip()
    if not reachable:
        message = f"OpenAlgo not reachable at {clean_url}"
    elif not clean_key:
        message = f"Server reachable at {clean_url}; API key required to verify session."
    else:
        try:
            from quantdesk.brokers.openalgo import ping_openalgo

            ok, info = ping_openalgo(clean_url, clean_key)
            info = info or {}
            ping_ok = bool(ok)
            latency_ms = info.get("latency_ms")
            broker = str(info.get("broker") or "")
            analyzer_active = bool(info.get("analyzer_active", False))
            if ping_ok:
                mode = str(info.get("mode") or ("Sandbox" if analyzer_active else "Live"))
                message = str(info.get("message") or "Connected")
            else:
                message = str(info.get("message") or "OpenAlgo ping failed")
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAlgo ping check failed: %s", exc)
            message = f"Ping error: {exc}"

    sidecar_running = bool(
        _is_proc_alive() or (sidecar_installed and reachable and host in ("127.0.0.1", "localhost"))
    )

    return {
        "host_url": clean_url,
        "reachable": reachable,
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "broker": broker,
        "analyzer_active": analyzer_active,
        "mode": mode,
        "message": message,
        "sidecar_dir": str(sdir),
        "sidecar_installed": sidecar_installed,
        "sidecar_running": sidecar_running,
    }


def build_openalgo_install_commands(target_dir=None) -> list[str]:
    """Return shell commands to install OpenAlgo into its OWN isolated ``.venv``."""
    sdir = _resolve_sidecar_dir(target_dir)
    venv_dir = sdir / ".venv"
    venv_py = venv_dir / "Scripts" / "python.exe"
    venv_pip = venv_dir / "Scripts" / "pip.exe"
    return [
        f'git clone https://github.com/marketcalls/openalgo.git "{sdir}"',
        f'python -m venv "{venv_dir}"',
        f'"{venv_py}" -m pip install --upgrade pip',
        f'"{venv_pip}" install -r "{sdir / "requirements.txt"}"',
        f'"{venv_py}" "{sdir / "app.py"}"',
    ]


def start_sidecar(services_dir=None) -> tuple[bool, str]:
    """Launch the isolated OpenAlgo sidecar process if installed."""
    global _SIDECAR_PROC, _SIDECAR_LOG_FH
    if _is_proc_alive():
        return True, f"OpenAlgo sidecar already running (PID {_SIDECAR_PROC.pid})."

    sdir = _resolve_sidecar_dir(services_dir)
    py_exe = _sidecar_venv_python(sdir)
    app_py = sdir / "app.py"
    if not sdir.is_dir() or py_exe is None or not app_py.is_file():
        return False, f"Isolated OpenAlgo sidecar not found at {sdir}."

    log_dir = sdir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sidecar.log"
    try:
        if _SIDECAR_LOG_FH is not None:
            try:
                _SIDECAR_LOG_FH.close()
            except Exception:  # noqa: BLE001
                pass
        _SIDECAR_LOG_FH = open(log_path, "a", encoding="utf-8")
        _SIDECAR_PROC = subprocess.Popen(
            [str(py_exe), str(app_py)],
            cwd=str(sdir),
            stdout=_SIDECAR_LOG_FH,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return True, f"Started OpenAlgo sidecar (PID {_SIDECAR_PROC.pid})."
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to start OpenAlgo sidecar: {exc}"


def stop_sidecar() -> tuple[bool, str]:
    """Terminate the running OpenAlgo sidecar process launched by QuantDesk."""
    global _SIDECAR_PROC, _SIDECAR_LOG_FH
    if not _is_proc_alive():
        _SIDECAR_PROC = None
        return False, "OpenAlgo sidecar is not running."

    proc = _SIDECAR_PROC
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        _SIDECAR_PROC = None
        if _SIDECAR_LOG_FH is not None:
            try:
                _SIDECAR_LOG_FH.close()
            except Exception:  # noqa: BLE001
                pass
            _SIDECAR_LOG_FH = None
        return True, "Stopped OpenAlgo sidecar."
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to stop OpenAlgo sidecar: {exc}"


def read_sidecar_logs(services_dir=None, tail_lines: int = 80) -> list[str]:
    """Return up to ``tail_lines`` trailing lines from the sidecar log file."""
    sdir = _resolve_sidecar_dir(services_dir)
    limit = max(1, int(tail_lines))
    for candidate in (
        sdir / "logs" / "sidecar.log",
        sdir / "sidecar.log",
        sdir / "logs" / "openalgo.log",
    ):
        if candidate.is_file():
            try:
                with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                    return [line.rstrip("\r\n") for line in deque(fh, maxlen=limit)]
            except OSError as exc:
                log.warning("Failed reading sidecar log %s: %s", candidate, exc)
                return []
    return []
