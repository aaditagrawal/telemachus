"""Verify the installed launchers use the receiver's isolated runtime."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_install_and_reinstall_produce_working_runtime(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to exercise installation")
    source = Path(__file__).resolve().parents[1]
    env = os.environ | {
        "UV_BIN": uv,
        "XDG_DATA_HOME": str(tmp_path / "share"),
        "XDG_BIN_HOME": str(tmp_path / "bin"),
    }
    for _ in range(2):
        subprocess.run(
            ["bash", str(source / "scripts/install-linux.sh")],
            env=env, capture_output=True, text=True, check=True, timeout=120,
        )
    installed = tmp_path / "share/telemachus"
    result = subprocess.run(
        [str(tmp_path / "bin/telemachus-linux"), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        check=True, timeout=30,
    )
    assert "low-latency Mac display receiver" in result.stdout
    subprocess.run(
        [str(installed / ".venv/bin/python"), "-c", "import sdl2, telemachus_client"],
        cwd=installed, capture_output=True, text=True, check=True, timeout=30,
    )
