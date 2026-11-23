from __future__ import annotations

import os
import pathlib


def _xdg(var: str, default: str) -> pathlib.Path:
    return pathlib.Path(os.environ.get(var) or pathlib.Path.home() / default)


def state_file() -> pathlib.Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "grayjay-link" / "state.json"


def control_socket() -> pathlib.Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return pathlib.Path(runtime) / "grayjay-link" / "control.sock"


def log_file() -> pathlib.Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "grayjay-link" / "daemon.log"


def grayjay_port_file() -> pathlib.Path:
    """Written by Grayjay once its local web server is up (Program.cs, PortFile)."""
    return pathlib.Path.home() / ".var/app/app.grayjay.Grayjay/data/Grayjay/port"
