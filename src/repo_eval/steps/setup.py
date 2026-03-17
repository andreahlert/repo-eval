"""Step 1: Installation in a clean venv."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "setup.sh"
        pyver = ctx.params.get("python_version", "3.12")
        # Script only demonstrates commands and runs lightweight checks.
        # The venv and install are already done by the runner.
        script.write_text(f"""#!/bin/bash
echo "# Step 1: Install {ctx.pkg}"
sleep 0.3
echo '$ uv venv .venv --python {pyver}'
echo "Using CPython {pyver}"
echo 'Creating virtual environment at: .venv'
sleep 0.3
echo ""
echo '$ uv pip install {ctx.pkg}'
{ctx.python_bin} -c "
import importlib.metadata
for d in importlib.metadata.requires('{ctx.pkg}') or []:
    if '; extra' not in d:
        print(f'  Installed {{d}}')
" 2>&1
echo "  Installed {ctx.pkg}"
sleep 0.3
echo ""
echo '$ python -c "import {ctx.pkg}; print({ctx.pkg}.__version__)"'
{ctx.python_bin} -c "import {ctx.pkg}; print({ctx.pkg}.__version__)" 2>&1
sleep 1
""")
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []

        # Test import
        result = subprocess.run(
            [str(ctx.python_bin), "-c", f"import {ctx.pkg}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            annotations.append(Annotation(
                Severity.PASS, "Clean install",
                f"`pip install {ctx.pkg}` works without errors.",
            ))
        else:
            annotations.append(Annotation(
                Severity.FAIL, "Install failed",
                f"Import error after install: {result.stderr.strip()[:200]}",
                Category.BUG,
            ))
            return annotations

        # Get version
        ver = subprocess.run(
            [str(ctx.python_bin), "-c", f"import {ctx.pkg}; print({ctx.pkg}.__version__)"],
            capture_output=True, text=True, timeout=30,
        )
        if ver.returncode != 0:
            annotations.append(Annotation(
                Severity.WARNING, "No __version__",
                f"Package has no `__version__` attribute.",
                Category.UX,
            ))

        # Count packages
        count_result = subprocess.run(
            [str(ctx.python_bin), "-c",
             "import importlib.metadata; print(len(list(importlib.metadata.distributions())))"],
            capture_output=True, text=True, timeout=30,
        )
        if count_result.returncode == 0:
            count = int(count_result.stdout.strip())
            max_pkgs = ctx.params.get("max_total_packages", 80)
            if count > max_pkgs:
                annotations.append(Annotation(
                    Severity.WARNING, f"{count} total packages installed",
                    f"Heavy dependency tree ({count} packages). May cause conflicts.",
                    Category.UX,
                ))
            else:
                annotations.append(Annotation(
                    Severity.PASS, f"{count} packages installed",
                    f"Reasonable dependency weight.",
                ))

        # Measure import time
        t0 = time.time()
        subprocess.run(
            [str(ctx.python_bin), "-c", f"import {ctx.pkg}"],
            capture_output=True, timeout=30,
        )
        import_time = time.time() - t0
        max_import = ctx.params.get("max_import_time_ms", 500) / 1000
        if import_time > max_import:
            annotations.append(Annotation(
                Severity.WARNING, f"Import time: {import_time:.2f}s",
                f"Slow import (>{max_import:.1f}s). Impacts script startup.",
                Category.UX,
            ))
        else:
            annotations.append(Annotation(
                Severity.PASS, f"Import time: {import_time:.2f}s",
                "Acceptable import time.",
            ))

        return annotations
