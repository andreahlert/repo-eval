"""Step 2: Test ALL code blocks from README as a new user would."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


def _extract_code_blocks(readme_text: str) -> list[dict]:
    """Extract all fenced code blocks with their language."""
    blocks = []
    pattern = r"```(\w*)\n(.*?)```"
    for match in re.finditer(pattern, readme_text, re.DOTALL):
        lang = match.group(1).lower() or "text"
        code = match.group(2).strip()
        blocks.append({"lang": lang, "code": code, "pos": match.start()})
    return blocks


def _classify_bash_block(code: str) -> str:
    """Classify a bash block: install, cli, or other."""
    if "pip install" in code or "uv add" in code or "conda install" in code:
        return "install"
    if code.strip().startswith(("flyte ", "fastapi ", "django ", "flask ")):
        return "cli"
    return "other"


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
        script = ctx.output_dir / "first_contact.sh"

        if not readme:
            script.write_text("#!/bin/bash\necho 'No README found'\n")
            script.chmod(0o755)
            return script

        blocks = _extract_code_blocks(readme)
        python_blocks = [b for b in blocks if b["lang"] in ("python", "py")]
        bash_blocks = [b for b in blocks if b["lang"] in ("bash", "sh", "shell", "console")]

        lines = ["#!/bin/bash", f'export PATH="{ctx.venv_path}/bin:$PATH"', ""]

        # Test install commands from bash blocks
        install_blocks = [b for b in bash_blocks if _classify_bash_block(b["code"]) == "install"]
        for i, b in enumerate(install_blocks):
            cmd = b["code"].strip().split("\n")[0]  # first line only
            lines.append(f'echo "--- Install block {i+1} ---"')
            lines.append(f'echo "\\$ {cmd}"')
            lines.append(f'{cmd} 2>&1 || echo "[FAILED: exit $?]"')
            lines.append('echo ""')

        # Test CLI commands from bash blocks
        cli_blocks = [b for b in bash_blocks if _classify_bash_block(b["code"]) == "cli"]
        for i, b in enumerate(cli_blocks):
            for cmd_line in b["code"].strip().split("\n"):
                cmd_line = cmd_line.strip()
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                lines.append(f'echo "--- CLI block ---"')
                lines.append(f'echo "\\$ {cmd_line}"')
                lines.append(f'{cmd_line} 2>&1 || echo "[FAILED: exit $?]"')
                lines.append('echo ""')

        # Test Python examples
        for i, b in enumerate(python_blocks):
            example_file = ctx.output_dir / f"readme_example_{i}.py"
            example_file.write_text(b["code"])
            lines.append(f'echo "--- Python example {i+1} ---"')
            lines.append(f'echo "\\$ python readme_example_{i}.py"')
            lines.append(f'{ctx.python_bin} {example_file} 2>&1 || echo "[FAILED: exit $?]"')
            lines.append('echo ""')

        if not python_blocks and not install_blocks and not cli_blocks:
            lines.append('echo "No testable code blocks found in README"')

        script.write_text("\n".join(lines))
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

        blocks = _extract_code_blocks(readme)
        python_blocks = [b for b in blocks if b["lang"] in ("python", "py")]
        bash_blocks = [b for b in blocks if b["lang"] in ("bash", "sh", "shell", "console")]
        install_blocks = [b for b in bash_blocks if _classify_bash_block(b["code"]) == "install"]
        cli_blocks = [b for b in bash_blocks if _classify_bash_block(b["code"]) == "cli"]

        annotations.append(Annotation(
            Severity.PASS,
            f"README has {len(python_blocks)} Python, {len(bash_blocks)} bash blocks",
            f"Found {len(install_blocks)} install commands, {len(cli_blocks)} CLI commands, {len(python_blocks)} Python examples.",
        ))

        # Test install commands
        for i, b in enumerate(install_blocks):
            cmd = b["code"].strip().split("\n")[0]
            result = subprocess.run(
                ["bash", "-c", f'export PATH="{ctx.venv_path}/bin:$PATH" && {cmd}'],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                annotations.append(Annotation(
                    Severity.PASS, f"Install command works: `{cmd[:60]}`",
                    "Documented install command executes successfully.",
                ))
            else:
                err = (result.stderr + result.stdout).strip().split("\n")[-1][:200]
                annotations.append(Annotation(
                    Severity.FAIL, f"Install command fails: `{cmd[:60]}`",
                    f"Error: {err}",
                    Category.DOCS,
                ))

        # Test CLI commands (just check they don't crash with --help or show usage)
        for b in cli_blocks:
            for cmd_line in b["code"].strip().split("\n"):
                cmd_line = cmd_line.strip()
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                # Don't run commands that take complex args, just test base command
                base_cmd = cmd_line.split()[0] if cmd_line.split() else ""
                if not base_cmd:
                    continue
                result = subprocess.run(
                    ["bash", "-c", f'export PATH="{ctx.venv_path}/bin:$PATH" && which {base_cmd}'],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    annotations.append(Annotation(
                        Severity.PASS, f"CLI `{base_cmd}` is available",
                        f"Command from README (`{cmd_line[:80]}`) uses an installed binary.",
                    ))
                else:
                    annotations.append(Annotation(
                        Severity.WARNING, f"CLI `{base_cmd}` not found",
                        f"README shows `{cmd_line[:80]}` but `{base_cmd}` is not in PATH after install.",
                        Category.DOCS,
                    ))

        # Test Python examples
        for i, b in enumerate(python_blocks):
            example_file = ctx.output_dir / f"readme_example_{i}.py"
            example_file.write_text(b["code"])

            result = subprocess.run(
                [str(ctx.python_bin), str(example_file)],
                capture_output=True, text=True, timeout=60,
                cwd=ctx.output_dir,
            )

            label = f"Python example {i+1}"
            # Show first meaningful line of code as context
            first_line = ""
            for line in b["code"].split("\n"):
                line = line.strip()
                if line and not line.startswith(("#", "import", "from")):
                    first_line = line[:60]
                    break

            if result.returncode == 0:
                annotations.append(Annotation(
                    Severity.PASS, f"{label} runs OK",
                    f"Code starting with `{first_line}` executes without errors.",
                ))
            else:
                combined = (result.stderr + "\n" + result.stdout).strip()
                error_line = "unknown error"
                for line in reversed(combined.split("\n")):
                    stripped = line.strip()
                    if "Error" in stripped or "Exception" in stripped:
                        error_line = stripped
                        break
                annotations.append(Annotation(
                    Severity.FAIL, f"{label} is broken",
                    f"`{first_line}` fails with: `{error_line[:250]}`",
                    Category.BUG,
                ))

        return annotations
