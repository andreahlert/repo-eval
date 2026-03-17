"""Run repo-eval inside an isolated podman/docker container."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

IMAGE_NAME = "repo-eval:latest"
CONTAINERFILE_DIR = Path(__file__).parent.parent.parent / "container"
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _engine() -> str:
    for cmd in ("podman", "docker"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError("Neither podman nor docker found in PATH.")


def build_image(force: bool = False) -> None:
    engine = _engine()

    # Check if image exists
    if not force:
        result = subprocess.run(
            [engine, "image", "exists", IMAGE_NAME],
            capture_output=True,
        )
        if result.returncode == 0:
            return

    print(f"Building {IMAGE_NAME} with {engine}...")
    subprocess.run(
        [
            engine, "build",
            "-t", IMAGE_NAME,
            "-f", str(CONTAINERFILE_DIR / "Containerfile"),
            str(PROJECT_ROOT),
        ],
        check=True,
    )
    print(f"Image {IMAGE_NAME} built.")


def run_in_container(
    target: str,
    config_path: str | None = None,
    output_dir: str = "./repo-eval-output",
    python_version: str | None = None,
    skip_recording: bool = False,
    steps: str | None = None,
    repo_url: str | None = None,
) -> Path:
    engine = _engine()
    build_image()

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    # Build the command to run inside the container
    cmd_args = [target, "--output", "/output"]

    if skip_recording:
        cmd_args.append("--skip-recording")
    if python_version:
        cmd_args.extend(["--python-version", python_version])
    if steps:
        cmd_args.extend(["--steps", steps])

    # Container run command
    run_cmd = [
        engine, "run",
        "--rm",
        "-v", f"{output}:/output:Z",
    ]

    # If a custom config is provided, mount it
    if config_path:
        config = Path(config_path).resolve()
        run_cmd.extend(["-v", f"{config}:/opt/config.yaml:ro,Z"])
        cmd_args.extend(["--config", "/opt/config.yaml"])

    # If a repo URL is provided, clone it inside the container
    # We do this by overriding the entrypoint with a script
    if repo_url:
        clone_and_run = (
            f"git clone --depth 1 {repo_url} /tmp/repo && "
            f"uv run repo-eval {' '.join(cmd_args)}"
        )
        run_cmd.extend([
            "--entrypoint", "bash",
            IMAGE_NAME,
            "-c", clone_and_run,
        ])
    else:
        run_cmd.append(IMAGE_NAME)
        run_cmd.extend(cmd_args)

    print(f"Running in container: {' '.join(run_cmd)}")
    result = subprocess.run(run_cmd)

    if result.returncode != 0:
        print(f"Container exited with code {result.returncode}", file=sys.stderr)

    return output
