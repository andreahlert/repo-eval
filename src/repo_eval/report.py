"""HTML report generator from findings."""

from __future__ import annotations

import json
from pathlib import Path

import jinja2

from repo_eval.models import Findings

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


class ReportGenerator:
    def __init__(self, template_dir: Path | None = None):
        tpl_dir = template_dir or TEMPLATES_DIR
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(tpl_dir)),
            autoescape=True,
        )

    def generate(self, findings: Findings, output_path: Path) -> None:
        template = self.env.get_template("report.html.j2")

        # Load raw .cast content for inline embedding
        # asciinema-player accepts the raw asciicast v2 string directly
        cast_data = {}
        output_dir = output_path.parent
        for step in findings.steps:
            if step.recording_cast:
                cast_path = output_dir / step.recording_cast
                if cast_path.exists():
                    # Escape for embedding in a JS string literal
                    raw = cast_path.read_text().strip()
                    cast_data[step.step_id] = json.dumps(raw)

        html = template.render(findings=findings, cast_data=cast_data)
        output_path.write_text(html)
        print(f"Report generated: {output_path}")
