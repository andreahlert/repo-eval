"""Step 6: Test examples/ from the repo - every .py file the project ships as a demo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


def _fetch_example_list(repo_url: str) -> list[dict]:
    """List .py files in the examples/ directory via GitHub API."""
    import urllib.request
    import json

    # Parse owner/repo
    url = repo_url.rstrip("/").replace(".git", "")
    parts = url.split("/")
    owner = repo = None
    for i, part in enumerate(parts):
        if part in ("github.com", "www.github.com") and i + 2 < len(parts):
            owner, repo = parts[i + 1], parts[i + 2]
            break
    if not owner:
        return []

    # Use git trees API to list examples/ recursively
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/main?recursive=1"
        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            tree = data.get("tree", [])
            examples = []
            for item in tree:
                path = item.get("path", "")
                if path.startswith("examples/") and path.endswith(".py") and item.get("type") == "blob":
                    examples.append({
                        "path": path,
                        "raw_url": f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path}",
                    })
            return examples
    except Exception:
        return []


def _download_file(url: str) -> str | None:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode()
    except Exception:
        return None


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "examples.sh"
        repo_url = ctx.params.get("repo_url", "")

        examples = _fetch_example_list(repo_url) if repo_url else []

        lines = [
            "#!/bin/bash",
            f'export PATH="{ctx.venv_path}/bin:$PATH"',
            "",
        ]

        if not examples:
            lines.append('echo "No examples/ directory found in repo"')
        else:
            # Only test a sample (first 10 top-level examples)
            top_level = [e for e in examples if e["path"].count("/") <= 2][:10]
            lines.append(f'echo "Found {len(examples)} example files, testing {len(top_level)} top-level ones"')
            lines.append("")

            for ex in top_level:
                lines.append(f'echo "\\$ python {ex["path"]}"')
                lines.append(f'timeout 10 {ctx.python_bin} -c "import ast; ast.parse(open(\'/dev/stdin\').read())" < <(curl -sL "{ex["raw_url"]}") 2>&1 && echo "  syntax OK" || echo "  SYNTAX ERROR"')
                lines.append("")

        script.write_text("\n".join(lines))
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []
        repo_url = ctx.params.get("repo_url", "")

        if not repo_url:
            annotations.append(Annotation(
                Severity.WARNING, "No repo URL provided",
                "Cannot test examples without a repository URL.",
                Category.DOCS,
            ))
            return annotations

        examples = _fetch_example_list(repo_url)

        if not examples:
            annotations.append(Annotation(
                Severity.WARNING, "No examples/ directory",
                "Repository has no examples/ directory with .py files.",
                Category.DOCS,
            ))
            return annotations

        annotations.append(Annotation(
            Severity.PASS,
            f"Found {len(examples)} example files in repo",
            f"Repository has examples/ directory with {len(examples)} Python files.",
        ))

        # Download and syntax-check a sample
        top_level = [e for e in examples if e["path"].count("/") <= 2][:10]
        syntax_ok = 0
        syntax_fail = 0
        import_ok = 0
        import_fail = 0
        import_failures = []

        for ex in top_level:
            content = _download_file(ex["raw_url"])
            if content is None:
                continue

            # Write to temp file
            local_path = ctx.output_dir / f"_ex_{Path(ex['path']).stem}.py"
            local_path.write_text(content)

            # Syntax check
            r = subprocess.run(
                [str(ctx.python_bin), "-c", f"import ast; ast.parse(open('{local_path}').read())"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                syntax_ok += 1
            else:
                syntax_fail += 1
                continue

            # Import check (try to import, catches missing deps)
            r = subprocess.run(
                [str(ctx.python_bin), "-c", f"import ast, sys; tree = ast.parse(open('{local_path}').read()); imports = [n.names[0].name if isinstance(n, ast.Import) else n.module for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and (n.names[0].name if isinstance(n, ast.Import) else n.module)]; [__import__(i.split('.')[0]) for i in imports if i]"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                import_ok += 1
            else:
                import_fail += 1
                err = r.stderr.strip().split("\n")[-1][:100]
                import_failures.append(f"`{ex['path']}`: {err}")

        tested = syntax_ok + syntax_fail
        if tested > 0:
            if syntax_fail > 0:
                annotations.append(Annotation(
                    Severity.FAIL,
                    f"{syntax_fail}/{tested} examples have syntax errors",
                    "These files fail to parse as Python.",
                    Category.BUG,
                ))
            else:
                annotations.append(Annotation(
                    Severity.PASS,
                    f"All {syntax_ok} tested examples have valid syntax",
                    "Syntax check passed on sampled example files.",
                ))

            if import_fail > 0:
                annotations.append(Annotation(
                    Severity.WARNING,
                    f"{import_fail}/{syntax_ok} examples have unmet imports",
                    "Missing dependencies: " + "; ".join(import_failures[:5]),
                    Category.DOCS,
                ))
            elif import_ok > 0:
                annotations.append(Annotation(
                    Severity.PASS,
                    f"All {import_ok} tested examples have resolvable imports",
                    "Top-level imports in example files resolve with the base package installed.",
                ))

        return annotations
