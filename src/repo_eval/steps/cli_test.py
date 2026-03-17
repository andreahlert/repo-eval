"""Step 3: CLI usage tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "cli_test.sh"
        pkg = ctx.pkg
        cli_bin = ctx.venv_path / "bin" / pkg

        commands = ctx.params.get("commands", [
            f"{pkg} --version",
            f"{pkg} --help",
        ])

        lines = ["#!/bin/bash", f'export PATH="{ctx.venv_path}/bin:$PATH"', ""]
        for cmd in commands:
            resolved = cmd.replace("{package}", pkg).replace("{bin}", str(cli_bin))
            lines.append(f'echo "\\$ {resolved}"')
            lines.append(f"{resolved} 2>&1")
            lines.append('echo ""')

        script.write_text("\n".join(lines))
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []
        pkg = ctx.pkg
        cli_bin = ctx.venv_path / "bin" / pkg

        if not cli_bin.exists():
            found = shutil.which(pkg, path=str(ctx.venv_path / "bin"))
            if not found:
                annotations.append(Annotation(
                    Severity.WARNING, "No CLI binary",
                    f"Package does not install a `{pkg}` CLI command.",
                    Category.UX,
                ))
                return annotations
            cli_bin = Path(found)

        env = {"PATH": str(ctx.venv_path / "bin"), "HOME": str(Path.home())}

        result = subprocess.run(
            [str(cli_bin), "--version"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode == 0:
            annotations.append(Annotation(
                Severity.PASS, "CLI --version works",
                f"`{pkg} --version` returns: {result.stdout.strip()[:100]}",
            ))
        else:
            annotations.append(Annotation(
                Severity.WARNING, "CLI --version fails",
                f"`{pkg} --version` exits with code {result.returncode}.",
                Category.UX,
            ))

        result = subprocess.run(
            [str(cli_bin), "--help"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode == 0:
            annotations.append(Annotation(
                Severity.PASS, "CLI --help works",
                f"`{pkg} --help` outputs documentation.",
            ))
        else:
            annotations.append(Annotation(
                Severity.WARNING, "CLI --help fails",
                f"`{pkg} --help` exits with code {result.returncode}.",
                Category.UX,
            ))

        return annotations
