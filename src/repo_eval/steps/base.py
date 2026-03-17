"""Base protocol and context for evaluation steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from repo_eval.models import Annotation


@dataclass
class StepContext:
    target: str
    python_bin: Path
    venv_path: Path
    output_dir: Path
    params: dict = field(default_factory=dict)
    is_local_repo: bool = False
    package_name: Optional[str] = None

    @property
    def pkg(self) -> str:
        return self.package_name or self.target


class EvalStep(Protocol):
    def generate_script(self, ctx: StepContext) -> Path:
        """Generate a bash script for recording. Returns path to .sh file."""
        ...

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        """Run evaluation logic. Returns annotations."""
        ...
