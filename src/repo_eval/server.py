"""Unified web server: config -> build -> dashboard flow."""

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
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import urlparse, parse_qs

from repo_eval.config import load_config, EvalConfig
from repo_eval.models import (
    Annotation, Category, Findings, ReportConfig, Severity, StepResult,
)
from repo_eval.steps import load_step
from repo_eval.steps.base import StepContext

APP_HTML = Path(__file__).parent.parent.parent / "templates" / "app.html"
DEFAULTS_DIR = Path(__file__).parent.parent.parent / "defaults"
CONTAINER_DIR = Path(__file__).parent.parent.parent / "container"
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _engine() -> str:
    for cmd in ("podman", "docker"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError("Neither podman nor docker found.")


def _image_exists(engine: str, name: str) -> bool:
    r = subprocess.run([engine, "image", "exists", name], capture_output=True)
    return r.returncode == 0


class AppServer:
    def __init__(self, port: int = 8778, output_base: Path | None = None):
        self.port = port
        self.output_base = (output_base or Path("/tmp/repo-eval-runs")).resolve()
        self._clients: list[queue.Queue] = []
        self._running = False
        self._run_id = 0
        self._container_id: str | None = None
        self._engine = _engine()

    def _broadcast(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, **data}
        dead = []
        for q in self._clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            self._clients.remove(q)

    def _wait_for_clients(self, timeout: float = 10) -> None:
        for _ in range(int(timeout * 10)):
            if self._clients:
                return
            time.sleep(0.1)

    # ===== PyPI / GitHub search =====

    def _search_pypi(self, query: str) -> list[dict]:
        """Search PyPI for packages."""
        import urllib.request
        try:
            url = f"https://pypi.org/pypi/{query}/json"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read())
                info = data.get("info", {})
                urls = info.get("project_urls", {}) or {}
                repo = urls.get("Source") or urls.get("Repository") or urls.get("Homepage") or ""
                return [{
                    "name": info.get("name", query),
                    "version": info.get("version"),
                    "summary": info.get("summary", ""),
                    "repo_url": repo,
                    "pypi_url": f"https://pypi.org/project/{query}/",
                }]
        except Exception:
            return []

    @staticmethod
    def _parse_github_url(url: str) -> tuple[str, str] | None:
        """Extract (owner, repo) from a GitHub URL."""
        url = url.rstrip("/").replace(".git", "")
        # https://github.com/owner/repo or github.com/owner/repo
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part in ("github.com", "www.github.com") and i + 2 < len(parts):
                return parts[i + 1], parts[i + 2]
        return None

    def _fetch_repo_meta(self, repo_url: str) -> dict:
        """Fetch repo metadata from GitHub API."""
        import urllib.request
        parsed = self._parse_github_url(repo_url)
        if not parsed:
            return {}
        owner, repo = parsed
        try:
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                return {
                    "name": data.get("name", ""),
                    "full_name": data.get("full_name", ""),
                    "description": data.get("description", ""),
                    "avatar_url": data.get("owner", {}).get("avatar_url", ""),
                    "html_url": data.get("html_url", ""),
                    "language": data.get("language", ""),
                    "stars": data.get("stargazers_count", 0),
                }
        except Exception:
            return {}

    def _resolve_pypi_name_from_repo(self, repo_url: str) -> str | None:
        """Read pyproject.toml from GitHub repo to find the real PyPI package name."""
        import urllib.request
        parsed = self._parse_github_url(repo_url)
        if not parsed:
            return None
        owner, repo = parsed
        # Try common locations for pyproject.toml
        for path in ("pyproject.toml", "src/pyproject.toml"):
            try:
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path}"
                req = urllib.request.Request(raw_url)
                with urllib.request.urlopen(req, timeout=5) as r:
                    content = r.read().decode()
                    # Parse [project] name = "xxx"
                    import re
                    match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                    if match:
                        return match.group(1)
            except Exception:
                continue
        return None

    def _resolve_readme_url(self, repo_url: str) -> str | None:
        """Build raw README URL from GitHub repo."""
        parsed = self._parse_github_url(repo_url)
        if not parsed:
            return None
        owner, repo = parsed
        return f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"

    # ===== Container management =====

    def _build_image(self) -> None:
        image = "repo-eval:latest"
        if _image_exists(self._engine, image):
            return

        self._broadcast("build_log", {"message": "Building repo-eval image...", "pct": 5})
        subprocess.run(
            [self._engine, "build", "-t", image, "-f",
             str(CONTAINER_DIR / "Containerfile"), str(PROJECT_ROOT)],
            capture_output=True, text=True,
        )

    def _destroy_container(self) -> None:
        if self._container_id:
            subprocess.run(
                [self._engine, "rm", "-f", self._container_id],
                capture_output=True, text=True,
            )
            self._container_id = None

    def _run_in_container(self, target: str, config_path: str | None, output_dir: Path) -> None:
        """Run repo-eval inside a container, streaming build output."""
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._engine, "run", "--rm",
            "--name", f"repo-eval-{self._run_id}",
            "-v", f"{output_dir}:/output:Z",
        ]

        if config_path:
            cfg = Path(config_path).resolve()
            cmd.extend(["-v", f"{cfg}:/opt/config.yaml:ro,Z"])

        cmd.extend(["repo-eval:latest", target, "--output", "/output"])

        if config_path:
            cmd.extend(["--config", "/opt/config.yaml"])

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )

        # Capture container ID
        self._container_id = f"repo-eval-{self._run_id}"

        for line in iter(proc.stdout.readline, ""):
            self._broadcast("build_log", {"message": line.rstrip(), "pct": None})
        proc.wait()

    # ===== Pipeline =====

    def _run_pipeline(self, target: str, mode: str, config_path: str | None, readme_url: str | None = None) -> None:
        self._running = True
        my_run_id = self._run_id

        self._wait_for_clients()

        output_dir = self.output_base / f"run-{my_run_id}"

        # Phase 1: Build environment
        self._broadcast("phase", {"phase": "building", "target": target})

        # Build image
        self._broadcast("build_log", {"message": "Checking container image...", "pct": 5})
        self._build_image()
        self._broadcast("build_log", {"message": "Image ready.", "pct": 15})

        # Create venv and install inside container
        self._broadcast("build_log", {"message": f"Creating container for {target}...", "pct": 20})
        venv_path = output_dir / ".venv"
        recordings_dir = output_dir / "recordings"
        output_dir.mkdir(parents=True, exist_ok=True)
        recordings_dir.mkdir(exist_ok=True)

        # We run locally for live streaming (container mode doesn't support SSE)
        # But we use a clean venv
        self._broadcast("build_log", {"message": "Creating virtual environment...", "pct": 30})
        subprocess.run(
            ["uv", "venv", str(venv_path), "--python", "3.12"],
            capture_output=True, text=True,
        )

        self._broadcast("build_log", {"message": f"Installing {target}...", "pct": 50})
        is_path = Path(target).exists()
        python_bin = venv_path / "bin" / "python"
        if is_path:
            install_cmd = ["uv", "pip", "install", "-e", target, "--python", str(python_bin)]
        else:
            install_cmd = ["uv", "pip", "install", target, "--python", str(python_bin)]

        proc = subprocess.Popen(
            install_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in iter(proc.stdout.readline, ""):
            self._broadcast("build_log", {"message": line.rstrip(), "pct": None})
        proc.wait()

        # Install pip
        subprocess.run(
            ["uv", "pip", "install", "pip", "--python", str(python_bin)],
            capture_output=True, text=True,
        )

        self._broadcast("build_log", {"message": "Detecting version...", "pct": 80})
        pkg_import = target.replace("-", "_")
        ver_result = subprocess.run(
            [str(python_bin), "-c", f"import {pkg_import}; print({pkg_import}.__version__)"],
            capture_output=True, text=True,
        )
        version = ver_result.stdout.strip() if ver_result.returncode == 0 else None

        # Fetch repo metadata for logo/colors
        self._broadcast("build_log", {"message": "Fetching package metadata...", "pct": 90})
        pypi_info = self._search_pypi(target)
        repo_url = ""
        repo_meta = {}
        if pypi_info:
            repo_url = pypi_info[0].get("repo_url", "")
        if repo_url and "github.com" in repo_url:
            repo_meta = self._fetch_repo_meta(repo_url)
            # Auto-resolve readme_url if not provided
            if not readme_url:
                readme_url = self._resolve_readme_url(repo_url)

        self._broadcast("build_log", {"message": "Ready.", "pct": 100})
        time.sleep(0.3)

        # Abort if new run started
        if self._run_id != my_run_id:
            self._running = False
            return

        # Phase 2: Dashboard
        config = load_config(config_path) if config_path else load_config()
        config.package_name = target

        # Inject readme_url into first_contact step params
        if readme_url:
            for sc in config.steps:
                if sc.id == "first_contact":
                    sc.params["readme_url"] = readme_url

        enabled_steps = [s for s in config.steps if s.enabled]
        all_steps = [{"id": "_setup", "name": "Environment Setup"}] + \
                    [{"id": s.id, "name": s.name} for s in enabled_steps]

        self._broadcast("phase", {
            "phase": "dashboard",
            "package_name": target,
            "package_version": version,
            "python_version": "3.12",
            "repo_url": repo_url,
            "avatar_url": repo_meta.get("avatar_url", ""),
            "description": repo_meta.get("description", ""),
            "stars": repo_meta.get("stars", 0),
            "steps": all_steps,
            "total_steps": len(all_steps),
        })

        # Setup step
        self._broadcast("step_start", {"step_id": "_setup", "step_name": "Environment Setup", "order": 0})
        # Show what was already installed
        self._broadcast("terminal", {"step_id": "_setup", "data": f"$ uv venv .venv --python 3.12\nVirtual environment created.\n\n$ uv pip install {target}\nAlready installed.\n\n$ python -c \"import {pkg_import}; print({pkg_import}.__version__)\"\n{version or '(not found)'}\n"})
        self._broadcast("step_done", {
            "step_id": "_setup", "status": "pass" if version else "warning",
            "duration": 0.1,
            "annotations": [{"severity": "pass", "title": f"Installed {target} {version or ''}", "detail": "Python 3.12 virtual environment.", "category": None}],
        })

        # Run test steps
        findings = Findings(
            package_name=target, package_version=version,
            python_version="3.12", platform="Linux x86_64",
            date=str(date.today()),
            report=ReportConfig(title=f"{target} Adoption Barrier Analysis"),
        )

        for i, sc in enumerate(enabled_steps, 1):
            if self._run_id != my_run_id:
                break
            result = self._run_step_live(sc, i, python_bin, venv_path, recordings_dir, target)
            findings.steps.append(result)

        total = len(findings.steps)
        if total > 0:
            scores = [100 if s.status == Severity.PASS else 60 if s.status == Severity.WARNING else 20
                      for s in findings.steps]
            findings.overall_score = sum(scores) // total

        findings.save(output_dir / "findings.json")

        # Phase 3: Assisted mode (optional)
        if mode == "assisted":
            self._broadcast("phase", {"phase": "analyzing"})
            try:
                from repo_eval.analyze import run_claude_analysis
                findings = run_claude_analysis(output_dir / "findings.json", output_dir)
                findings.save(output_dir / "findings.json")
                # Re-send enriched annotations
                for step in findings.steps:
                    self._broadcast("enriched", {
                        "step_id": step.step_id,
                        "status": step.status.value,
                        "annotations": [
                            {"severity": a.severity.value, "title": a.title, "detail": a.detail,
                             "category": a.category.value if a.category else None}
                            for a in step.annotations
                        ],
                    })
            except Exception as e:
                self._broadcast("build_log", {"message": f"Claude analysis failed: {e}", "pct": None})

        self._broadcast("done", {"score": findings.overall_score})
        self._running = False

    def _run_step_live(self, step_config, order, python_bin, venv_path, recordings_dir, target) -> StepResult:
        step = load_step(step_config.module)
        pkg_import = target.replace("-", "_")
        ctx = StepContext(
            target=target, python_bin=python_bin, venv_path=venv_path,
            output_dir=recordings_dir, params=step_config.params,
            is_local_repo=Path(target).is_dir(), package_name=pkg_import,
        )

        self._broadcast("step_start", {"step_id": step_config.id, "step_name": step_config.name, "order": order})
        t0 = time.time()

        try:
            script_path = step.generate_script(ctx)
            if script_path.exists():
                proc = subprocess.Popen(
                    ["bash", str(script_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    self._broadcast("terminal", {"step_id": step_config.id, "data": line})
                proc.wait()
        except Exception as e:
            self._broadcast("terminal", {"step_id": step_config.id, "data": f"\n[ERROR] {e}\n"})

        try:
            annotations = step.evaluate(ctx)
        except Exception as e:
            annotations = [Annotation(Severity.FAIL, "Step crashed", f"Error: {e}", Category.BUG)]

        duration = time.time() - t0
        status = StepResult.worst_severity(annotations)

        self._broadcast("step_done", {
            "step_id": step_config.id, "status": status.value, "duration": round(duration, 2),
            "annotations": [
                {"severity": a.severity.value, "title": a.title, "detail": a.detail,
                 "category": a.category.value if a.category else None}
                for a in annotations
            ],
        })

        return StepResult(
            step_id=step_config.id, step_name=step_config.name, order=order,
            status=status, annotations=annotations, duration_seconds=round(duration, 2),
        )

    def _start_run(self, target: str, mode: str, config_path: str | None = None, readme_url: str | None = None) -> None:
        self._run_id += 1
        def _safe():
            try:
                self._run_pipeline(target, mode, config_path, readme_url=readme_url)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._broadcast("done", {"score": 0, "error": str(e)})
                self._running = False
        threading.Thread(target=_safe, daemon=True).start()

    def run(self, initial_target: str | None = None, initial_mode: str = "auto",
            config_path: str | None = None) -> None:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)

                if parsed.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(APP_HTML.read_bytes())

                elif parsed.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    q: queue.Queue = queue.Queue(maxsize=10000)
                    server_ref._clients.append(q)

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
                        if q in server_ref._clients:
                            server_ref._clients.remove(q)

                elif parsed.path == "/search":
                    params = parse_qs(parsed.query)
                    q = params.get("q", [""])[0]
                    results = []

                    if "github.com" in q:
                        # Input is a GitHub URL: resolve to PyPI package name
                        meta = server_ref._fetch_repo_meta(q)
                        pypi_name = server_ref._resolve_pypi_name_from_repo(q)
                        readme_url = server_ref._resolve_readme_url(q)

                        if pypi_name:
                            # Found package name in pyproject.toml, verify on PyPI
                            pypi_results = server_ref._search_pypi(pypi_name)
                            if pypi_results:
                                r = pypi_results[0]
                                r["repo_url"] = meta.get("html_url", q)
                                r["avatar_url"] = meta.get("avatar_url", "")
                                r["stars"] = meta.get("stars", 0)
                                r["readme_url"] = readme_url
                                results = [r]
                            else:
                                # Package exists in repo but not on PyPI
                                results = [{
                                    "name": pypi_name,
                                    "version": None,
                                    "summary": meta.get("description", ""),
                                    "repo_url": meta.get("html_url", q),
                                    "pypi_url": "",
                                    "avatar_url": meta.get("avatar_url", ""),
                                    "stars": meta.get("stars", 0),
                                    "readme_url": readme_url,
                                }]
                        elif meta:
                            # Could not read pyproject.toml, show repo name
                            results = [{
                                "name": meta.get("name", q.split("/")[-1]),
                                "version": None,
                                "summary": meta.get("description", ""),
                                "repo_url": meta.get("html_url", q),
                                "pypi_url": "",
                                "avatar_url": meta.get("avatar_url", ""),
                                "stars": meta.get("stars", 0),
                                "readme_url": readme_url,
                                "note": "Could not resolve PyPI name. The repo name may differ from the package name.",
                            }]
                    else:
                        # Input is a package name: search PyPI directly
                        results = server_ref._search_pypi(q)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(results).encode())

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)

                if parsed.path == "/start":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length)) if length else {}
                    target = body.get("target", "")
                    mode = body.get("mode", "auto")
                    cfg = body.get("config_path", config_path)
                    readme_url = body.get("readme_url")
                    if target:
                        server_ref._start_run(target, mode, cfg, readme_url=readme_url)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

                elif parsed.path == "/rerun":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length)) if length else {}
                    target = body.get("target", initial_target or "")
                    mode = body.get("mode", "auto")
                    cfg = body.get("config_path", config_path)
                    readme_url = body.get("readme_url")
                    if target:
                        server_ref._start_run(target, mode, cfg, readme_url=readme_url)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt, *args):
                pass

        class ThreadedServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        server = ThreadedServer(("127.0.0.1", self.port), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        url = f"http://127.0.0.1:{self.port}"
        print(f"repo-eval: {url}")
        webbrowser.open(url)

        # If target provided, auto-start after browser connects
        if initial_target:
            time.sleep(2)
            self._start_run(initial_target, initial_mode, config_path)

        print("Press Ctrl+C to stop.")
        try:
            t.join()
        except KeyboardInterrupt:
            server.shutdown()
