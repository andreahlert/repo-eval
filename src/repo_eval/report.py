"""HTML report generator from findings."""

from __future__ import annotations

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
        html = template.render(findings=findings)
        output_path.write_text(html)
        print(f"Report generated: {output_path}")
