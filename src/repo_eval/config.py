"""YAML config loader for repo-eval checklist."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULTS_DIR = Path(__file__).parent.parent.parent / "defaults"


@dataclass
class StepConfig:
    id: str
    name: str
    module: str
    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class EvalConfig:
    package_name: Optional[str]
    python_version: str
    steps: list[StepConfig]
    report_title: Optional[str] = None
    accent_color: str = "#7652a2"
    logo_url: Optional[str] = None


def load_config(path: Optional[str] = None) -> EvalConfig:
    if path is None:
        path = str(DEFAULTS_DIR / "checklist.yaml")

    with open(path) as f:
        raw = yaml.safe_load(f)

    steps = []
    for s in raw.get("steps", []):
        steps.append(StepConfig(
            id=s["id"],
            name=s["name"],
            module=s["module"],
            enabled=s.get("enabled", True),
            params=s.get("params", {}),
        ))

    report = raw.get("report", {})
    return EvalConfig(
        package_name=raw.get("package_name"),
        python_version=raw.get("python_version", "3.12"),
        steps=steps,
        report_title=report.get("title"),
        accent_color=report.get("accent_color", "#7652a2"),
        logo_url=report.get("logo_url"),
    )
