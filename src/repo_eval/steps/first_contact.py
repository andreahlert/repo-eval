"""Step 2: Run the README example as-is."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


def _extract_first_python_block(readme_text: str) -> Optional[str]:
    pattern = r"```python\n(.*?)```"
    match = re.search(pattern, readme_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _fetch_readme(ctx: StepContext) -> Optional[str]:
    readme_url = ctx.params.get("readme_url")
    if readme_url:
        import urllib.request
        try:
            with urllib.request.urlopen(readme_url, timeout=10) as r:
                return r.read().decode()
        except Exception:
            pass

    for name in ("README.md", "README.rst", "README.txt", "README"):
        for base in (Path(ctx.target), Path.cwd()):
            p = base / name
            if p.exists():
                return p.read_text()
    return None


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        readme = _fetch_readme(ctx)
        example = _extract_first_python_block(readme) if readme else None

        script = ctx.output_dir / "first_contact.sh"
        example_file = ctx.output_dir / "readme_example.py"

        if example:
            example_file.write_text(example)
            # Real execution: shows the code then runs it for real
            script.write_text(f"""#!/bin/bash
echo "\\$ cat readme_example.py"
cat {example_file}
echo ""
echo "\\$ python readme_example.py"
{ctx.python_bin} {example_file} 2>&1
""")
        else:
            script.write_text("""#!/bin/bash
echo "No Python example found in README"
""")

        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []

        readme = _fetch_readme(ctx)
        if readme is None:
            annotations.append(Annotation(
                Severity.WARNING, "No README found",
                "Could not find README.md in the repo or via URL.",
                Category.DOCS,
            ))
            return annotations

        example = _extract_first_python_block(readme)
        if example is None:
            annotations.append(Annotation(
                Severity.WARNING, "No Python example in README",
                "README exists but has no ```python code block.",
                Category.DOCS,
            ))
            return annotations

        example_file = ctx.output_dir / "readme_example.py"
        example_file.write_text(example)

        result = subprocess.run(
            [str(ctx.python_bin), str(example_file)],
            capture_output=True, text=True, timeout=60,
            cwd=ctx.output_dir,
        )

        if result.returncode == 0:
            annotations.append(Annotation(
                Severity.PASS, "README example runs successfully",
                "The first Python example in README executes without errors.",
            ))
        else:
            combined = (result.stderr + "\n" + result.stdout).strip()
            error_lines = combined.split("\n")
            error_line = "unknown error"
            for line in reversed(error_lines):
                stripped = line.strip()
                if "Error" in stripped or "Exception" in stripped:
                    error_line = stripped
                    break
            annotations.append(Annotation(
                Severity.FAIL, "README example is broken",
                f"The first Python example fails with: `{error_line[:300]}`",
                Category.BUG,
            ))

        return annotations
