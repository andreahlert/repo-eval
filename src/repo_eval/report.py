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

        # Load .cast data for each step that has a recording
        cast_data = {}
        output_dir = output_path.parent
        for step in findings.steps:
            if step.recording_cast:
                cast_path = output_dir / step.recording_cast
                if cast_path.exists():
                    # Read raw .cast content and parse each line as JSON array
                    raw = cast_path.read_text().strip()
                    lines = raw.split("\n")
                    # First line is header, rest are events
                    cast_obj = []
                    header = None
                    for i, line in enumerate(lines):
                        parsed = json.loads(line)
                        if i == 0:
                            header = parsed
                        else:
                            cast_obj.append(parsed)

                    # Build the data structure asciinema-player expects
                    # It accepts an object with header + events
                    cast_data[step.step_id] = json.dumps({
                        "version": header.get("version", 2),
                        "width": header.get("width", 110),
                        "height": header.get("height", 30),
                        "events": cast_obj,
                    })

        html = template.render(findings=findings, cast_data=cast_data)
        output_path.write_text(html)
        print(f"Report generated: {output_path}")
