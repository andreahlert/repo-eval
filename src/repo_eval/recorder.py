"""Terminal recording via asciinema + svg-term."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class RecorderError(RuntimeError):
    pass


class Recorder:
    def __init__(self) -> None:
        self._check_deps()

    @staticmethod
    def _check_deps() -> None:
        for cmd in ("asciinema", "svg-term"):
            if shutil.which(cmd) is None:
                raise RecorderError(
                    f"'{cmd}' not found in PATH. "
                    f"Install: {'pip install asciinema' if cmd == 'asciinema' else 'npm i -g svg-term-cli'}"
                )

    def record(self, script_path: Path, cast_path: Path, cols: int = 110, rows: int = 40) -> None:
        cast_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "asciinema", "rec",
                str(cast_path),
                "--command", f"bash {script_path}",
                "--overwrite",
                "--cols", str(cols),
                "--rows", str(rows),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RecorderError(f"asciinema failed: {result.stderr}")

    def to_svg(self, cast_path: Path, svg_path: Path, width: int = 110) -> None:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "svg-term",
                "--in", str(cast_path),
                "--out", str(svg_path),
                "--window",
                "--no-cursor",
                "--padding", "10",
                "--width", str(width),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RecorderError(f"svg-term failed: {result.stderr}")
