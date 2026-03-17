"""Claude-assisted analysis: reads findings.json, enriches with deep analysis."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from repo_eval.models import Annotation, Category, Findings, Severity, StepResult

ANALYZE_PROMPT_TEMPLATE = """\
You are analyzing a Python package for adoption barriers. Below is the automated \
findings.json from repo-eval. Your job is to enrich it with deeper analysis.

## Package: {package_name} {package_version}

## Automated findings:
```json
{findings_json}
```

## Raw test outputs:
{raw_outputs}

## Instructions:

For each step in the findings, you MUST:
1. Read the automated annotations
2. Investigate deeper: look at the actual error messages, understand root causes
3. Add NEW annotations with richer detail (root cause, workarounds, who introduced it, links)
4. Keep existing annotations but improve their detail text if shallow
5. Add annotations for issues the automated tool missed

Output ONLY a valid JSON object with this exact structure:
{{
  "steps": [
    {{
      "step_id": "<same as input>",
      "annotations": [
        {{
          "severity": "pass|warning|fail",
          "title": "short title",
          "detail": "rich detail with root cause, workaround, links",
          "category": "bug|ux|design|docs|null"
        }}
      ]
    }}
  ],
  "overall_assessment": "1-3 sentence summary",
  "overall_score": <0-100 integer>
}}

Be specific. Reference exact error messages, attribute names, class names. \
If you can identify the root cause in the package source, mention it. \
Do NOT be generic. Do NOT repeat the automated annotations without adding value.
"""


def _collect_raw_outputs(output_dir: Path) -> str:
    """Read all .sh scripts and their recording outputs for context."""
    parts = []
    recordings_dir = output_dir / "recordings"
    if not recordings_dir.exists():
        return "(no raw outputs available)"

    for f in sorted(recordings_dir.iterdir()):
        if f.suffix in (".sh", ".py"):
            parts.append(f"### {f.name}\n```\n{f.read_text()}\n```")

    return "\n\n".join(parts) if parts else "(no raw outputs available)"


def run_claude_analysis(findings_path: Path, output_dir: Path) -> Findings:
    """Call claude CLI to analyze findings and return enriched Findings."""
    if not shutil.which("claude"):
        print("Error: 'claude' CLI not found in PATH.", file=sys.stderr)
        print("Install: https://docs.anthropic.com/en/docs/claude-code", file=sys.stderr)
        sys.exit(1)

    findings = Findings.load(findings_path)
    raw_outputs = _collect_raw_outputs(output_dir)

    prompt = ANALYZE_PROMPT_TEMPLATE.format(
        package_name=findings.package_name,
        package_version=findings.package_version or "unknown",
        findings_json=json.dumps(json.loads(findings_path.read_text()), indent=2),
        raw_outputs=raw_outputs,
    )

    print("Calling Claude Code for deep analysis...")
    result = subprocess.run(
        [
            "claude", "-p",
            "--output-format", "text",
            "--permission-mode", "default",
            "--max-budget-usd", "1.0",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        print(f"Claude CLI failed: {result.stderr}", file=sys.stderr)
        return findings

    # Parse the JSON from Claude's response
    response = result.stdout.strip()
    enriched = _parse_claude_response(response)

    if enriched is None:
        print("Warning: Could not parse Claude's response. Using original findings.")
        return findings

    # Merge enriched annotations into findings
    return _merge_enrichments(findings, enriched)


def _parse_claude_response(response: str) -> dict | None:
    """Extract JSON from Claude's response (may have markdown wrapping)."""
    # Try direct parse
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` block
    import re
    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(response[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _merge_enrichments(findings: Findings, enriched: dict) -> Findings:
    """Merge Claude's enriched annotations into the findings."""
    step_map = {s["step_id"]: s for s in enriched.get("steps", [])}

    for step in findings.steps:
        if step.step_id in step_map:
            enriched_step = step_map[step.step_id]
            new_annotations = []
            for ann_data in enriched_step.get("annotations", []):
                sev = ann_data.get("severity", "warning")
                cat = ann_data.get("category")
                new_annotations.append(Annotation(
                    severity=Severity(sev),
                    title=ann_data.get("title", ""),
                    detail=ann_data.get("detail", ""),
                    category=Category(cat) if cat and cat != "null" else None,
                ))
            step.annotations = new_annotations
            step.status = StepResult.worst_severity(new_annotations)

    if "overall_score" in enriched:
        findings.overall_score = enriched["overall_score"]

    return findings
