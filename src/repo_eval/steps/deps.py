"""Step 4: Dependency analysis."""

from __future__ import annotations

import subprocess
from pathlib import Path

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "deps.sh"
        # Real execution: actually inspects the installed package
        script.write_text(f"""#!/bin/bash
export PATH="{ctx.venv_path}/bin:$PATH"

echo "\\$ python -c \\"# list direct dependencies\\""
{ctx.python_bin} -c "
import importlib.metadata
deps = importlib.metadata.requires('{ctx.pkg}')
if deps:
    core = [d for d in deps if '; extra' not in d]
    print(f'Direct dependencies: {{len(core)}}')
    for d in sorted(core):
        print(f'  {{d}}')
else:
    print('No dependency metadata found')
" 2>&1

echo ""
echo "\\$ python -c \\"# count total packages\\""
{ctx.python_bin} -c "
import importlib.metadata
pkgs = list(importlib.metadata.distributions())
print(f'Total packages in environment: {{len(pkgs)}}')
" 2>&1

echo ""
echo "\\$ python -c \\"# measure import time\\""
{ctx.python_bin} -c "
import time
start = time.time()
import {ctx.pkg}
elapsed = time.time() - start
print(f'Import time: {{elapsed:.3f}}s')
" 2>&1
""")
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []

        result = subprocess.run(
            [str(ctx.python_bin), "-c", f"""
import importlib.metadata
deps = importlib.metadata.requires('{ctx.pkg}')
if deps:
    core = [d for d in deps if '; extra' not in d]
    print(len(core))
    for d in sorted(core):
        print(d)
else:
    print(0)
"""],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            direct_count = int(lines[0])
            dep_list = lines[1:] if len(lines) > 1 else []

            max_direct = ctx.params.get("max_direct_deps", 15)
            if direct_count > max_direct:
                annotations.append(Annotation(
                    Severity.WARNING, f"{direct_count} direct dependencies",
                    f"Heavy for a Python package. Threshold: {max_direct}.",
                    Category.UX,
                ))
            else:
                annotations.append(Annotation(
                    Severity.PASS, f"{direct_count} direct dependencies",
                    "Reasonable dependency count.",
                ))

            pinned = [d for d in dep_list if "==" in d]
            if pinned:
                annotations.append(Annotation(
                    Severity.WARNING, f"{len(pinned)} pinned dependencies",
                    f"Exact pins may cause conflicts: {', '.join(d.split('==')[0].strip() for d in pinned[:5])}",
                    Category.UX,
                ))

        result = subprocess.run(
            [str(ctx.python_bin), "-c",
             "import importlib.metadata; print(len(list(importlib.metadata.distributions())))"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            total = int(result.stdout.strip())
            max_total = ctx.params.get("max_total_packages", 80)
            sev = Severity.WARNING if total > max_total else Severity.PASS
            annotations.append(Annotation(
                sev, f"{total} total packages in venv",
                f"Transitive dependency count. Threshold: {max_total}.",
            ))

        return annotations
