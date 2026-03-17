"""Live dashboard: streams step execution to browser in real-time."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
import webbrowser
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from repo_eval.config import EvalConfig, load_config
from repo_eval.models import (
    Annotation,
    Category,
    Findings,
    ReportConfig,
    Severity,
    StepResult,
)
from repo_eval.steps import load_step
from repo_eval.steps.base import StepContext

LIVE_HTML = Path(__file__).parent.parent.parent / "templates" / "live.html"


class LiveRunner:
    def __init__(
        self,
        target: str,
        config: EvalConfig,
        output_dir: Path,
        port: int = 8777,
        step_filter: Optional[list[str]] = None,
        config_path: Optional[str] = None,
    ):
        self.target = target
        self.config = config
        self.output_dir = output_dir.resolve()
        self.port = port
        self.step_filter = step_filter
        self.config_path = config_path
        self.venv_path = self.output_dir / ".venv"
        self.python_bin = self.venv_path / "bin" / "python"
        self.recordings_dir = self.output_dir / "recordings"

        self._clients: list[queue.Queue] = []
        self._events: list[dict] = []
        self._running = False

    def _broadcast(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, **data}
        self._events.append(event)
        for q in self._clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def _reset(self) -> None:
        self._events.clear()
        # Clean venv so next run starts fresh
        if self.venv_path.exists():
            shutil.rmtree(self.venv_path, ignore_errors=True)
        if self.recordings_dir.exists():
            shutil.rmtree(self.recordings_dir, ignore_errors=True)

    def _run_streamed(self, cmd: list[str], step_id: str = "_setup") -> int:
        """Run a command and stream its output to the browser."""
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in iter(proc.stdout.readline, ""):
            self._broadcast("terminal", {"step_id": step_id, "data": line})
        proc.wait()
        return proc.returncode

    def _setup_and_install(self) -> Optional[str]:
        """Create venv and install package, streaming everything to _setup terminal."""
        pkg = self.config.package_name or self.target

        self._broadcast("terminal", {"step_id": "_setup", "data": f"$ uv venv .venv --python {self.config.python_version}\n"})
        self._run_streamed(
            ["uv", "venv", str(self.venv_path), "--python", self.config.python_version],
            "_setup",
        )

        self._broadcast("terminal", {"step_id": "_setup", "data": f"\n$ uv pip install {pkg}\n"})
        is_path = Path(self.target).exists()
        if is_path:
            cmd = ["uv", "pip", "install", "-e", self.target, "--python", str(self.python_bin)]
        else:
            cmd = ["uv", "pip", "install", self.target, "--python", str(self.python_bin)]
        self._run_streamed(cmd, "_setup")

        # Install pip silently
        subprocess.run(
            ["uv", "pip", "install", "pip", "--python", str(self.python_bin)],
            capture_output=True, text=True,
        )

        # Check version
        pkg_import = pkg.replace("-", "_")
        self._broadcast("terminal", {"step_id": "_setup", "data": f"\n$ python -c \"import {pkg_import}; print({pkg_import}.__version__)\"\n"})
        result = subprocess.run(
            [str(self.python_bin), "-c", f"import {pkg_import}; print({pkg_import}.__version__)"],
            capture_output=True, text=True,
        )
        version = result.stdout.strip() if result.returncode == 0 else None
        if version:
            self._broadcast("terminal", {"step_id": "_setup", "data": f"{version}\n"})
        else:
            self._broadcast("terminal", {"step_id": "_setup", "data": "(no __version__ found)\n"})

        return version

    def _run_step_live(self, step_config, order: int) -> StepResult:
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

        self._broadcast("step_start", {
            "step_id": step_config.id,
            "step_name": step_config.name,
            "order": order,
        })

        t0 = time.time()

        try:
            script_path = step.generate_script(ctx)
            if script_path.exists():
                proc = subprocess.Popen(
                    ["bash", str(script_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    self._broadcast("terminal", {
                        "step_id": step_config.id,
                        "data": line,
                    })
                proc.wait()
        except Exception as e:
            self._broadcast("terminal", {
                "step_id": step_config.id,
                "data": f"\n[ERROR] {e}\n",
            })

        try:
            annotations = step.evaluate(ctx)
        except Exception as e:
            annotations = [Annotation(Severity.FAIL, "Step crashed", f"Error: {e}", Category.BUG)]

        duration = time.time() - t0
        status = StepResult.worst_severity(annotations)

        self._broadcast("step_done", {
            "step_id": step_config.id,
            "status": status.value,
            "duration": round(duration, 2),
            "annotations": [
                {"severity": a.severity.value, "title": a.title, "detail": a.detail,
                 "category": a.category.value if a.category else None}
                for a in annotations
            ],
        })

        return StepResult(
            step_id=step_config.id,
            step_name=step_config.name,
            order=order,
            status=status,
            annotations=annotations,
            duration_seconds=round(duration, 2),
        )

    def _run_pipeline(self) -> None:
        self._running = True
        # Wait for at least one browser client to connect
        for _ in range(50):  # up to 5 seconds
            if self._clients:
                break
            time.sleep(0.1)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(exist_ok=True)

        pkg = self.config.package_name or self.target
        enabled_steps = [s for s in self.config.steps if s.enabled]
        if self.step_filter:
            enabled_steps = [s for s in enabled_steps if s.id in self.step_filter]

        # Send init FIRST so the UI renders immediately with setup + all steps
        all_steps = [{"id": "_setup", "name": "Environment Setup"}] + \
                    [{"id": s.id, "name": s.name} for s in enabled_steps]

        self._broadcast("init", {
            "package_name": pkg,
            "package_version": None,
            "python_version": self.config.python_version,
            "accent_color": self.config.accent_color,
            "logo_url": self.config.logo_url,
            "steps": all_steps,
            "total_steps": len(all_steps),
        })

        # Setup step: streamed to browser
        self._broadcast("step_start", {"step_id": "_setup", "step_name": "Environment Setup", "order": 0})
        t0 = time.time()
        version = self._setup_and_install()
        setup_duration = round(time.time() - t0, 2)
        self._broadcast("step_done", {
            "step_id": "_setup",
            "status": "pass" if version else "warning",
            "duration": setup_duration,
            "annotations": [{"severity": "pass", "title": f"Installed {pkg} {version or '(unknown)'}", "detail": f"Virtual environment created with Python {self.config.python_version}.", "category": None}],
        })

        # Update title with version
        if version:
            self._broadcast("version", {"package_version": version})

        findings = Findings(
            package_name=pkg, package_version=version,
            python_version=self.config.python_version, platform="Linux x86_64",
            date=str(date.today()),
            report=ReportConfig(
                title=self.config.report_title or f"{pkg} Adoption Barrier Analysis",
                accent_color=self.config.accent_color, logo_url=self.config.logo_url,
            ),
        )

        for i, sc in enumerate(enabled_steps, 1):
            result = self._run_step_live(sc, i)
            findings.steps.append(result)

        total = len(findings.steps)
        if total > 0:
            scores = [100 if s.status == Severity.PASS else 60 if s.status == Severity.WARNING else 20
                      for s in findings.steps]
            findings.overall_score = sum(scores) // total

        findings.save(self.output_dir / "findings.json")
        self._broadcast("done", {"score": findings.overall_score})
        self._running = False

    def _start_pipeline_thread(self) -> None:
        t = threading.Thread(target=self._run_pipeline, daemon=True)
        t.start()

    def run(self) -> None:
        runner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)

                if parsed.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(LIVE_HTML.read_bytes())

                elif parsed.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    q: queue.Queue = queue.Queue(maxsize=5000)
                    runner._clients.append(q)

                    # Stream only new events from now on (no replay)
                    try:
                        while True:
                            try:
                                evt = q.get(timeout=2)
                                self.wfile.write(f"data: {json.dumps(evt)}\n\n".encode())
                                self.wfile.flush()
                                if evt.get("type") == "done":
                                    break
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        if q in runner._clients:
                            runner._clients.remove(q)

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)

                if parsed.path == "/rerun":
                    if not runner._running:
                        runner._reset()
                        runner._start_pipeline_thread()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

                elif parsed.path == "/new":
                    params = parse_qs(parsed.query)
                    new_target = params.get("target", [None])[0]
                    if new_target and not runner._running:
                        runner._reset()
                        runner.target = new_target
                        # Reload config, override package name
                        if runner.config_path:
                            runner.config = load_config(runner.config_path)
                        else:
                            runner.config = load_config()
                        runner.config.package_name = new_target
                        runner._start_pipeline_thread()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", self.port), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        url = f"http://127.0.0.1:{self.port}"
        print(f"Live dashboard: {url}")
        webbrowser.open(url)

        time.sleep(1)
        self._start_pipeline_thread()

        print("Dashboard running. Press Ctrl+C to stop.")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            server.shutdown()
