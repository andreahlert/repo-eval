"""Orchestrator: runs steps, records, collects results."""

from __future__ import annotations

import platform
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Optional

from repo_eval.config import EvalConfig, StepConfig
from repo_eval.models import Findings, ReportConfig, Severity, StepResult
from repo_eval.recorder import Recorder, RecorderError
from repo_eval.steps import load_step
from repo_eval.steps.base import StepContext


class EvalRunner:
    def __init__(
        self,
        target: str,
        config: EvalConfig,
        output_dir: Path,
        skip_recording: bool = False,
        step_filter: Optional[list[str]] = None,
    ):
        self.target = target
        self.config = config
        self.output_dir = output_dir.resolve()
        self.skip_recording = skip_recording
        self.step_filter = step_filter
        self.venv_path = self.output_dir / ".venv"
        self.python_bin = self.venv_path / "bin" / "python"
        self.recordings_dir = self.output_dir / "recordings"

        try:
            self.recorder = Recorder() if not skip_recording else None
        except RecorderError:
            self.recorder = None
            if not skip_recording:
                print("Warning: asciinema/svg-term not found. Skipping recordings.")

    def _setup_venv(self) -> None:
        if self.venv_path.exists():
            return
        print(f"Creating venv at {self.venv_path}...")
        subprocess.run(
            ["uv", "venv", str(self.venv_path), "--python", self.config.python_version],
            check=True, capture_output=True, text=True,
        )

    def _install_package(self) -> Optional[str]:
        pkg = self.config.package_name or self.target
        print(f"Installing {pkg}...")
        is_path = Path(self.target).exists()

        if is_path:
            cmd = ["uv", "pip", "install", "-e", self.target, "--python", str(self.python_bin)]
        else:
            cmd = ["uv", "pip", "install", self.target, "--python", str(self.python_bin)]

        subprocess.run(cmd, check=True, capture_output=True, text=True)

        # Also install pip inside venv so scripts can use it
        subprocess.run(
            ["uv", "pip", "install", "pip", "--python", str(self.python_bin)],
            capture_output=True, text=True,
        )

        pkg_import = pkg.replace("-", "_")
        result = subprocess.run(
            [str(self.python_bin), "-c", f"import {pkg_import}; print({pkg_import}.__version__)"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def run_all(self) -> Findings:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(exist_ok=True)

        self._setup_venv()
        version = self._install_package()

        pkg = self.config.package_name or self.target
        findings = Findings(
            package_name=pkg,
            package_version=version,
            python_version=self.config.python_version,
            platform=f"{platform.system()} {platform.machine()}",
            date=str(date.today()),
            report=ReportConfig(
                title=self.config.report_title or f"{pkg} Adoption Barrier Analysis",
                accent_color=self.config.accent_color,
                logo_url=self.config.logo_url,
            ),
        )

        enabled_steps = [s for s in self.config.steps if s.enabled]
        if self.step_filter:
            enabled_steps = [s for s in enabled_steps if s.id in self.step_filter]

        for i, step_config in enumerate(enabled_steps, 1):
            print(f"\n[{i}/{len(enabled_steps)}] {step_config.name}...")
            result = self._run_step(step_config, i)
            findings.steps.append(result)

        total = len(findings.steps)
        if total > 0:
            scores = []
            for step in findings.steps:
                if step.status == Severity.PASS:
                    scores.append(100)
                elif step.status == Severity.WARNING:
                    scores.append(60)
                else:
                    scores.append(20)
            findings.overall_score = sum(scores) // total

        return findings

    def _run_step(self, step_config: StepConfig, order: int) -> StepResult:
        step = load_step(step_config.module)

        pkg = self.config.package_name or self.target
        ctx = StepContext(
            target=self.target,
            python_bin=self.python_bin,
            venv_path=self.venv_path,
            output_dir=self.recordings_dir,
            params=step_config.params,
            is_local_repo=Path(self.target).is_dir(),
            package_name=pkg.replace("-", "_"),
        )

        recording_cast = None
        t0 = time.time()

        # Generate script (real commands) and record execution
        try:
            script_path = step.generate_script(ctx)
            if self.recorder and script_path.exists():
                cast_path = self.recordings_dir / f"{order:02d}-{step_config.id}.cast"
                self.recorder.record(script_path, cast_path)
                recording_cast = str(cast_path.relative_to(self.output_dir))
        except Exception as e:
            print(f"  Recording failed: {e}")

        # Evaluate (programmatic checks)
        try:
            annotations = step.evaluate(ctx)
        except Exception as e:
            from repo_eval.models import Annotation, Category
            annotations = [Annotation(
                Severity.FAIL, "Step evaluation crashed",
                f"Error: {e}",
                Category.BUG,
            )]

        duration = time.time() - t0
        status = StepResult.worst_severity(annotations)

        return StepResult(
            step_id=step_config.id,
            step_name=step_config.name,
            order=order,
            status=status,
            annotations=annotations,
            recording_cast=recording_cast,
            duration_seconds=round(duration, 2),
        )
