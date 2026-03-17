"""Data models for repo-eval findings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class Severity(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class Category(str, Enum):
    BUG = "bug"
    UX = "ux"
    DESIGN = "design"
    DOCS = "docs"


@dataclass
class Annotation:
    severity: Severity
    title: str
    detail: str
    category: Optional[Category] = None

    @property
    def icon(self) -> str:
        return {"pass": "+", "warning": "!", "fail": "x"}[self.severity.value]

    @property
    def css_class(self) -> str:
        return {"pass": "positive", "warning": "warning", "fail": "negative"}[self.severity.value]


@dataclass
class StepResult:
    step_id: str
    step_name: str
    order: int
    status: Severity
    annotations: list[Annotation] = field(default_factory=list)
    recording_svg: Optional[str] = None
    recording_cast: Optional[str] = None
    duration_seconds: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def worst_severity(annotations: list[Annotation]) -> Severity:
        if any(a.severity == Severity.FAIL for a in annotations):
            return Severity.FAIL
        if any(a.severity == Severity.WARNING for a in annotations):
            return Severity.WARNING
        return Severity.PASS


@dataclass
class ReportConfig:
    title: Optional[str] = None
    accent_color: str = "#7652a2"
    logo_url: Optional[str] = None


@dataclass
class Findings:
    package_name: str
    package_version: Optional[str] = None
    python_version: str = ""
    platform: str = ""
    date: str = ""
    steps: list[StepResult] = field(default_factory=list)
    overall_score: Optional[int] = None
    report: ReportConfig = field(default_factory=ReportConfig)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load(cls, path: Path) -> Findings:
        data = json.loads(path.read_text())
        report = ReportConfig(**data.pop("report", {}))
        steps = []
        for s in data.pop("steps", []):
            anns = [Annotation(
                severity=Severity(a["severity"]),
                title=a["title"],
                detail=a["detail"],
                category=Category(a["category"]) if a.get("category") else None,
            ) for a in s.pop("annotations", [])]
            steps.append(StepResult(
                **{k: v for k, v in s.items() if k != "status"},
                status=Severity(s["status"]),
                annotations=anns,
            ))
        return cls(**data, steps=steps, report=report)
