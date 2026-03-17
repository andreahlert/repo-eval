"""CLI entry point for repo-eval."""

from __future__ import annotations

from pathlib import Path

import click

from repo_eval.config import load_config
from repo_eval.models import Findings
from repo_eval.report import ReportGenerator
from repo_eval.runner import EvalRunner


@click.command()
@click.argument("target", default="_")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              help="Custom checklist YAML config.")
@click.option("--output", "-o", "output_dir", default=None,
              help="Output directory. Default: ./repo-eval-<target>/")
@click.option("--python-version", default=None,
              help="Python version for test venv. Default from config.")
@click.option("--skip-recording", is_flag=True,
              help="Skip asciinema recording (faster, no SVGs).")
@click.option("--report-only", is_flag=True,
              help="Regenerate HTML report from existing findings.json.")
@click.option("--steps", default=None,
              help="Comma-separated step IDs to run (e.g. setup,deps).")
@click.option("--container", is_flag=True,
              help="Run inside an isolated podman/docker container. No local state used.")
@click.option("--repo-url", default=None,
              help="Git repo URL to clone inside the container (used with --container).")
@click.option("--rebuild-image", is_flag=True,
              help="Force rebuild the container image.")
@click.option("--mode", type=click.Choice(["auto", "assisted"]), default="auto",
              help="auto: collect + report. assisted: collect + Claude analysis + report.")
@click.option("--analyze-only", is_flag=True,
              help="Run Claude analysis on existing findings.json (no collection).")
@click.option("--live", is_flag=True,
              help="Open browser with live dashboard showing real-time execution.")
@click.option("--port", default=8777, type=int,
              help="Port for live dashboard server (default: 8777).")
def main(
    target: str,
    config_path: str | None,
    output_dir: str | None,
    python_version: str | None,
    skip_recording: bool,
    report_only: bool,
    steps: str | None,
    container: bool,
    repo_url: str | None,
    rebuild_image: bool,
    mode: str,
    analyze_only: bool,
    live: bool,
    port: int,
) -> None:
    """Analyze TARGET (package name or repo path) for adoption barriers.

    TARGET can be a PyPI package name (e.g. 'flyte', 'fastapi') or a local path.

    \b
    Modes:
      auto      Collect evidence + generate report (default)
      assisted  Collect evidence + Claude analysis + generate report

    \b
    Examples:
      repo-eval flyte                                  # auto mode
      repo-eval flyte --live                           # real-time browser dashboard
      repo-eval flyte --mode assisted                  # Claude enriches findings
      repo-eval flyte --container --mode assisted      # container + Claude
      repo-eval flyte --analyze-only                   # re-analyze existing findings
      repo-eval flyte --report-only                    # regenerate HTML only
    """
    if output_dir is None:
        safe_name = target.replace("/", "_").replace(".", "_")
        output_dir = f"./repo-eval-{safe_name}"

    output = Path(output_dir).resolve()
    findings_path = output / "findings.json"
    report_path = output / "report.html"

    # Live mode: real-time browser dashboard with wizard
    if live:
        from repo_eval.server import AppServer
        app = AppServer(port=port, output_base=output)
        # If target is provided, auto-start. Otherwise show config screen.
        app.run(
            initial_target=target if target != "_" else None,
            initial_mode=mode,
            config_path=config_path,
        )
        return

    # Report-only: just regenerate HTML
    if report_only:
        if not findings_path.exists():
            raise click.ClickException(f"No findings.json at {findings_path}")
        findings = Findings.load(findings_path)
        ReportGenerator().generate(findings, report_path)
        return

    # Analyze-only: run Claude on existing findings
    if analyze_only:
        if not findings_path.exists():
            raise click.ClickException(f"No findings.json at {findings_path}")
        from repo_eval.analyze import run_claude_analysis
        findings = run_claude_analysis(findings_path, output)
        findings.save(findings_path)
        print(f"Enriched findings saved: {findings_path}")
        ReportGenerator().generate(findings, report_path)
        print(f"Done. Open: {report_path}")
        return

    # Container mode: delegate to podman
    if container:
        from repo_eval.container import build_image, run_in_container

        if rebuild_image:
            build_image(force=True)

        run_in_container(
            target=target,
            config_path=config_path,
            output_dir=output_dir,
            python_version=python_version,
            skip_recording=skip_recording,
            steps=steps,
            repo_url=repo_url,
        )

        # After container, optionally run Claude analysis on host
        if mode == "assisted" and findings_path.exists():
            from repo_eval.analyze import run_claude_analysis
            findings = run_claude_analysis(findings_path, output)
            findings.save(findings_path)
            print(f"Enriched findings saved: {findings_path}")

        if findings_path.exists():
            findings = Findings.load(findings_path)
            ReportGenerator().generate(findings, report_path)
            print(f"\nDone. Open: {report_path}")
        return

    # Local mode: collect
    config = load_config(config_path)

    if python_version:
        config.python_version = python_version

    if config.package_name is None:
        config.package_name = target

    step_filter = steps.split(",") if steps else None

    runner = EvalRunner(
        target=target,
        config=config,
        output_dir=output,
        skip_recording=skip_recording,
        step_filter=step_filter,
    )

    findings = runner.run_all()
    findings.save(findings_path)
    print(f"\nFindings saved: {findings_path}")

    # Assisted mode: enrich with Claude
    if mode == "assisted":
        from repo_eval.analyze import run_claude_analysis
        findings = run_claude_analysis(findings_path, output)
        findings.save(findings_path)
        print(f"Enriched findings saved: {findings_path}")

    ReportGenerator().generate(findings, report_path)
    print(f"\nDone. Open: {report_path}")


if __name__ == "__main__":
    main()
