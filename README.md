<p align="center">
  <img src="assets/logo.svg" width="80" height="80" alt="repo-eval logo" />
</p>

<h1 align="center">repo-eval</h1>

<p align="center">
  <strong>Does this package actually work? Find out in 15 seconds.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/repo-eval/"><img src="https://img.shields.io/pypi/v/repo-eval?color=6366f1&label=version" alt="PyPI" /></a>
  <a href="https://github.com/andreahlert/repo-eval/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="License" /></a>
  <a href="https://github.com/andreahlert/repo-eval"><img src="https://img.shields.io/github/stars/andreahlert/repo-eval?style=flat&color=f5e0a0" alt="Stars" /></a>
</p>

<p align="center">
  Automated adoption barrier analysis for Python packages.<br/>
  Installs, tests every README example, introspects the API, checks dependencies.<br/>
  Returns a structured report with everything that works and everything that breaks.
</p>

---

## What it does

`repo-eval` acts like a first-time user. It installs a package from PyPI, runs every code example from the README, tests the CLI, introspects the public API for broken docstring examples, checks the repo's example files, and analyzes the dependency tree.

Everything that passes, warns, or fails gets collected into a structured JSON report.

```
$ repo-eval flyte

============================================================
  flyte 2.0.7
  Score: 60/100
============================================================

  [PASS] Installation
    + Clean install
    + 55 packages installed
    + Import time: 0.47s

  [FAIL] Hello World (README)
    + README has 2 Python, 6 bash blocks
    + Install command works: `pip install flyte`
    + Install command works: `pip install flyte[tui]`
    x Python example 1 is broken
      `run.result` -> AttributeError: '_LocalRun' has no attribute 'result'
    x Python example 2 is broken
      `FastAPI()` -> ModuleNotFoundError: No module named 'fastapi'

  [FAIL] API Introspection
    + Public API: 18 classes, 31 functions
    + 4 docstring examples pass
    x 11 docstring examples broken
    ! 4 constructors raise errors

  [WARNING] Dependencies & Weight
    ! 20 direct dependencies (threshold: 15)
    ! 2 exact-pinned dependencies
```

## Install

```bash
pip install repo-eval
```

Or run directly with [uv](https://docs.astral.sh/uv/):

```bash
uvx repo-eval flyte
```

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (for venv creation)

## Usage

### Analyze a PyPI package

```bash
repo-eval flyte
```

### Analyze a GitHub repo

Resolves the repo's `pyproject.toml` to find the real PyPI package name.

```bash
repo-eval https://github.com/flyteorg/flyte-sdk
```

### Get JSON output

Results print to `stdout`, progress to `stderr`. Pipe-friendly.

```bash
repo-eval flyte --json-output > flyte-report.json
```

### Generate an HTML report

```bash
repo-eval flyte --html
# opens: /tmp/repo-eval-flyte/report.html
```

### Enrich with Claude (assisted mode)

Runs the automated analysis first, then calls [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to add root cause analysis, workarounds, and deeper context to each finding.

```bash
repo-eval flyte --mode assisted
```

### Live browser dashboard

Real-time terminal output and annotations as tests execute.

```bash
repo-eval --live
```

Opens a browser at `localhost:8778` with:
- Search bar to find any PyPI package or paste a GitHub URL
- Progress bar during environment setup
- Split view: terminal output on the left, annotations on the right
- Re-run and New Analysis buttons

### Run specific steps only

```bash
repo-eval flyte --steps setup,first_contact,deps
```

## What it tests

| Step | What it checks |
|---|---|
| **Installation** | `pip install`, import time, package count |
| **Hello World (README)** | Every `pip install`, CLI command, and Python example from the README |
| **CLI Usage** | `--version` and `--help` flags |
| **API Introspection** | Docstring examples, constructor signatures, missing documentation |
| **Repo Examples** | Syntax and import checks on `examples/*.py` from the GitHub repo |
| **Dependencies** | Direct count, transitive count, exact pins, version floors |

Each step is a Python module in `src/repo_eval/steps/`. Adding a new step is a single file with two methods: `generate_script()` and `evaluate()`.

## Real-world results

Packages analyzed during development:

| Package | Score | Key findings |
|---|---|---|
| **flyte** 2.0.7 | 60/100 | README example uses `run.result` which doesn't exist, FastAPI example missing dep, 11 broken docstring examples |
| **provero** 0.1.1 | 45/100 | `Engine` class not importable, `provero.airflow` module missing from package, `--version` not registered |

## Configuration

Create a YAML checklist to customize thresholds, add steps, or set branding:

```yaml
# my-checklist.yaml
package_name: my-package
python_version: "3.12"

steps:
  - id: setup
    name: "Installation"
    module: repo_eval.steps.setup
    params:
      max_total_packages: 50
      max_import_time_ms: 300

  - id: first_contact
    name: "First Contact"
    module: repo_eval.steps.first_contact
    params:
      readme_url: "https://raw.githubusercontent.com/org/repo/main/README.md"

  - id: cli
    name: "CLI"
    module: repo_eval.steps.cli_test

  - id: introspect
    name: "API"
    module: repo_eval.steps.introspect

  - id: deps
    name: "Dependencies"
    module: repo_eval.steps.deps
    params:
      max_direct_deps: 10

report:
  title: "My Package Analysis"
  accent_color: "#6366f1"
```

```bash
repo-eval my-package --config my-checklist.yaml
```

## Use in CI

Add to GitHub Actions to catch README/docstring regressions before release:

```yaml
# .github/workflows/repo-eval.yml
name: repo-eval
on: [push]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uvx repo-eval . --json-output > eval.json
      - run: |
          score=$(python3 -c "import json; print(json.load(open('eval.json'))['overall_score'])")
          echo "Score: $score"
          [ "$score" -ge 70 ] || exit 1
```

## Writing custom steps

A step is a Python file in `src/repo_eval/steps/` with a `Step` class:

```python
# src/repo_eval/steps/my_check.py
from repo_eval.models import Annotation, Severity
from repo_eval.steps.base import StepContext
from pathlib import Path
import subprocess

class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "my_check.sh"
        script.write_text(f"""#!/bin/bash
export PATH="{ctx.venv_path}/bin:$PATH"
echo "\\$ python -c \\"import {ctx.pkg}\\""
{ctx.python_bin} -c "import {ctx.pkg}" 2>&1
""")
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        r = subprocess.run(
            [str(ctx.python_bin), "-c", f"import {ctx.pkg}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return [Annotation(Severity.PASS, "Import works", "OK")]
        return [Annotation(Severity.FAIL, "Import fails", r.stderr[:200])]
```

Register it in your checklist YAML:

```yaml
steps:
  - id: my_check
    name: "My Custom Check"
    module: repo_eval.steps.my_check
```

## License

Apache 2.0. See [LICENSE](LICENSE).
