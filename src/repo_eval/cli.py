"""CLI entry point for repo-eval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from repo_eval.config import load_config
from repo_eval.models import Findings
from repo_eval.report import ReportGenerator
from repo_eval.resolve import resolve_target
from repo_eval.runner import EvalRunner


def _print_results(findings: Findings) -> None:
    """Print a human-readable summary to stderr."""
    pkg = findings.package_name
    ver = findings.package_version or "?"
    score = findings.overall_score

    click.echo(f"\n{'=' * 60}", err=True)
    click.echo(f"  {pkg} {ver}", err=True)
    click.echo(f"  Score: {score}/100", err=True)
    click.echo(f"{'=' * 60}\n", err=True)

    for step in findings.steps:
        status = step.status.value.upper()
        color = {"PASS": "green", "WARNING": "yellow", "FAIL": "red"}.get(status, "white")
        click.echo(click.style(f"  [{status}]", fg=color) + f" {step.step_name}", err=True)

        for ann in step.annotations:
            icon = {"pass": "+", "warning": "!", "fail": "x"}[ann.severity.value]
            ann_color = {"pass": "green", "warning": "yellow", "fail": "red"}[ann.severity.value]
            click.echo(click.style(f"    {icon} ", fg=ann_color) + ann.title, err=True)
            # Wrap detail at 80 chars
            detail = ann.detail
            if len(detail) > 100:
                detail = detail[:100] + "..."
            click.echo(f"      {detail}", err=True)

        click.echo("", err=True)


@click.command()
@click.argument("target", default="_")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              help="Custom checklist YAML config.")
@click.option("--output", "-o", "output_dir", default=None,
              help="Output directory.")
@click.option("--python-version", default=None,
              help="Python version for test venv.")
@click.option("--skip-recording", is_flag=True, default=True,
              help="Skip terminal recordings (default: true for CLI).")
@click.option("--steps", default=None,
              help="Comma-separated step IDs to run.")
@click.option("--mode", type=click.Choice(["auto", "assisted"]), default="auto",
              help="auto or assisted (with Claude).")
@click.option("--json-output", "json_flag", is_flag=True,
              help="Print findings JSON to stdout.")
@click.option("--html", is_flag=True,
              help="Also generate HTML report.")
@click.option("--live", is_flag=True,
              help="Open browser with live dashboard.")
@click.option("--port", default=8778, type=int,
              help="Port for live dashboard.")
@click.option("--container", is_flag=True,
              help="Run inside podman/docker container.")
@click.option("--rebuild-image", is_flag=True,
              help="Force rebuild container image.")
@click.option("--report-only", is_flag=True,
              help="Regenerate report from existing findings.")
@click.option("--analyze-only", is_flag=True,
              help="Re-run Claude analysis on existing findings.")
def main(
    target: str,
    config_path: str | None,
    output_dir: str | None,
    python_version: str | None,
    skip_recording: bool,
    steps: str | None,
    mode: str,
    json_flag: bool,
    html: bool,
    live: bool,
    port: int,
    container: bool,
    rebuild_image: bool,
    report_only: bool,
    analyze_only: bool,
) -> None:
    """Analyze a Python package or GitHub repo for adoption barriers.

    \b
    TARGET can be:
      - PyPI package name:  repo-eval flyte
      - GitHub URL:         repo-eval https://github.com/flyteorg/flyte-sdk
      - Local path:         repo-eval ./my-project

    \b
    Examples:
      repo-eval flyte                           # run all tests, print results
      repo-eval flyte --json-output             # print JSON to stdout
      repo-eval flyte --json-output > out.json  # save JSON to file
      repo-eval flyte --mode assisted           # enrich with Claude
      repo-eval flyte --live                    # browser dashboard
      repo-eval https://github.com/flyteorg/flyte-sdk --json-output
    """

    # Live mode: browser dashboard
    if live:
        from repo_eval.server import AppServer
        out = Path(output_dir).resolve() if output_dir else Path("/tmp/repo-eval-runs")
        app = AppServer(port=port, output_base=out)
        app.run(
            initial_target=target if target != "_" else None,
            initial_mode=mode,
            config_path=config_path,
        )
        return

    # Need a target for all other modes
    if target == "_":
        raise click.ClickException("TARGET is required. Pass a package name or GitHub URL.")

    # Resolve target: GitHub URL -> PyPI name, find repo_url, readme_url
    click.echo(f"Resolving {target}...", err=True)
    resolved = resolve_target(target)
    pkg_name = resolved["package_name"]
    repo_url = resolved["repo_url"]
    readme_url = resolved["readme_url"]

    if pkg_name != target:
        click.echo(f"  Resolved to PyPI package: {pkg_name}", err=True)
    if repo_url:
        click.echo(f"  Repo: {repo_url}", err=True)

    # Output dir
    if output_dir is None:
        safe = pkg_name.replace("/", "_").replace(".", "_")
        output_dir = f"/tmp/repo-eval-{safe}"

    output = Path(output_dir).resolve()
    findings_path = output / "findings.json"
    report_path = output / "report.html"

    # Report-only
    if report_only:
        if not findings_path.exists():
            raise click.ClickException(f"No findings.json at {findings_path}")
        findings = Findings.load(findings_path)
        ReportGenerator().generate(findings, report_path)
        return

    # Analyze-only
    if analyze_only:
        if not findings_path.exists():
            raise click.ClickException(f"No findings.json at {findings_path}")
        from repo_eval.analyze import run_claude_analysis
        findings = run_claude_analysis(findings_path, output)
        findings.save(findings_path)
        _print_results(findings)
        if json_flag:
            print(json.dumps(json.loads(findings_path.read_text()), indent=2))
        return

    # Load config and inject resolved URLs
    config = load_config(config_path)
    if python_version:
        config.python_version = python_version
    config.package_name = pkg_name

    for sc in config.steps:
        if sc.id == "first_contact" and readme_url:
            sc.params["readme_url"] = readme_url
        if sc.id == "examples" and repo_url:
            sc.params["repo_url"] = repo_url

    step_filter = steps.split(",") if steps else None

    # Container mode
    if container:
        from repo_eval.container import build_image, run_in_container
        if rebuild_image:
            build_image(force=True)
        run_in_container(
            target=pkg_name, config_path=config_path, output_dir=output_dir,
            python_version=python_version, skip_recording=skip_recording, steps=steps,
        )
        if mode == "assisted" and findings_path.exists():
            from repo_eval.analyze import run_claude_analysis
            findings = run_claude_analysis(findings_path, output)
            findings.save(findings_path)
        if findings_path.exists():
            findings = Findings.load(findings_path)
            _print_results(findings)
            if json_flag:
                print(json.dumps(json.loads(findings_path.read_text()), indent=2))
            if html:
                ReportGenerator().generate(findings, report_path)
        return

    # Local mode: run
    runner = EvalRunner(
        target=pkg_name,
        config=config,
        output_dir=output,
        skip_recording=skip_recording,
        step_filter=step_filter,
    )

    findings = runner.run_all()
    findings.save(findings_path)

    # Assisted mode
    if mode == "assisted":
        from repo_eval.analyze import run_claude_analysis
        findings = run_claude_analysis(findings_path, output)
        findings.save(findings_path)

    # Output results
    _print_results(findings)

    if json_flag:
        print(json.dumps(json.loads(findings_path.read_text()), indent=2))

    if html:
        ReportGenerator().generate(findings, report_path)
        click.echo(f"Report: {report_path}", err=True)

    click.echo(f"JSON: {findings_path}", err=True)


if __name__ == "__main__":
    main()
