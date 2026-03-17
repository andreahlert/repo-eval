<p align="center">
  <img src="assets/logo.svg" width="48" height="48" alt="repo-eval" />
</p>

<h2 align="center">Roadmap</h2>

<p align="center">
Where repo-eval is going. Updated as priorities shift.
</p>

---

### Legend

| Status | Meaning |
|---|---|
| :white_check_mark: | Shipped |
| :construction: | In progress |
| :bulb: | Planned |
| :telescope: | Exploring |

---

### v0.1 &mdash; Foundation (shipped)

> Prove the concept: can automated tests catch real adoption bugs?

:white_check_mark: Core engine with step protocol (`generate_script` + `evaluate`)

:white_check_mark: 6 built-in steps: install, README, CLI, introspection, examples, deps

:white_check_mark: GitHub URL resolution (reads `pyproject.toml` to find PyPI name)

:white_check_mark: JSON output to stdout, pipe-friendly

:white_check_mark: HTML report with terminal recordings (asciinema-player)

:white_check_mark: Live browser dashboard with real-time terminal streaming

:white_check_mark: Assisted mode (Claude enriches findings with root cause analysis)

:white_check_mark: Podman/Docker container isolation

:white_check_mark: Custom checklist YAML config

---

### v0.2 &mdash; Depth

> Go deeper on each test. Catch more subtle bugs.

:bulb: **Type system step** &mdash; Test Pydantic models, NamedTuples, dataclasses as function I/O. Detect positional-arg-only constructors, broken serialization round-trips.

:bulb: **Composition step** &mdash; Test async/sync boundaries. Detect event loop conflicts, tasks that can't call other tasks, footgun patterns.

:bulb: **Migration step** &mdash; If a package has a predecessor (flytekit -> flyte, flask -> quart), test the documented migration path. Check if the old and new package can coexist.

:bulb: **Security step** &mdash; Run `pip audit` on the installed environment. Flag known CVEs in dependencies.

:bulb: **Python version matrix** &mdash; Test against 3.10, 3.11, 3.12, 3.13, 3.14. Report which versions pass and which break.

:bulb: **Doctest runner** &mdash; Execute every `>>>` block in every module's docstrings, not just top-level exports.

---

### v0.3 &mdash; Integration

> Make it part of the development workflow.

:bulb: **GitHub Action** &mdash; `andreahlert/repo-eval-action@v1` that runs on push/PR, posts the report as a PR comment, fails the check if score drops below threshold.

:bulb: **Pre-release gate** &mdash; Run against a local build (`repo-eval .`) before `twine upload`. Catch broken README examples before they hit PyPI.

:bulb: **Version diff** &mdash; `repo-eval flyte==2.0.6 --diff flyte==2.0.7`. Show what improved, what regressed, new findings.

:bulb: **Badge** &mdash; `![repo-eval score](https://repo-eval.dev/badge/flyte)` for READMEs. SVG badge generated from latest analysis.

:bulb: **Webhook notifications** &mdash; Post findings to Slack/Discord when a new version is published on PyPI.

---

### v0.4 &mdash; Scale

> Analyze the ecosystem, not just one package.

:telescope: **Public registry** &mdash; Web service at `repo-eval.dev` that continuously analyzes top PyPI packages and publishes scores. Searchable, sortable, comparable.

:telescope: **API** &mdash; `GET /api/v1/score/flyte` returns the latest findings JSON. Free tier for open source.

:telescope: **Leaderboard** &mdash; Top packages by score, worst offenders, most improved. Weekly digest email for maintainers.

:telescope: **Package manager integration** &mdash; Show the repo-eval score in `uv add` / `pip install` output. "Installing flyte (repo-eval: 60/100, 2 broken README examples)".

---

### v0.5 &mdash; Intelligence

> Let the tool understand what it's testing, not just whether it crashes.

:telescope: **Semantic analysis** &mdash; Use the assisted mode to generate step scripts dynamically based on the package's documentation. Instead of generic checks, test the specific workflows the package advertises.

:telescope: **Regression tracking** &mdash; Store historical scores per package. Detect trends. Alert when a package's score drops across releases.

:telescope: **Community contributions** &mdash; Users submit custom step modules as plugins. Curated registry of steps for specific domains (ML, web, data, CLI).

---

### Non-goals

Things repo-eval intentionally does not do:

- **Code quality / linting** &mdash; Use ruff, mypy, pylint. repo-eval tests the user experience, not the source code.
- **Test coverage** &mdash; Use pytest + coverage. repo-eval tests what's documented, not what's tested internally.
- **Supply chain security** &mdash; Use Socket, Snyk, pip-audit. repo-eval may call pip-audit as a step, but supply chain analysis is not the core mission.
- **Performance benchmarking** &mdash; repo-eval measures import time, not runtime performance.

The mission is narrow: **does this package work the way its documentation says it does?**

---

<p align="center">
  <sub>Want to contribute a step or suggest a feature? <a href="https://github.com/andreahlert/repo-eval/issues">Open an issue</a>.</sub>
</p>
