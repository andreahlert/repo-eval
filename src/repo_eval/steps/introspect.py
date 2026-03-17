"""Step 5: Introspect public API - test docstring examples and constructor signatures."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from repo_eval.models import Annotation, Category, Severity
from repo_eval.steps.base import StepContext


# Python script that runs inside the target venv and dumps API info as JSON
INTROSPECT_SCRIPT = '''\
import importlib
import inspect
import json
import sys
import re

pkg_name = sys.argv[1]
mod = importlib.import_module(pkg_name)

results = {"classes": [], "functions": [], "doctest_blocks": []}

for name in sorted(dir(mod)):
    if name.startswith("_"):
        continue
    obj = getattr(mod, name)

    # Classes
    if inspect.isclass(obj):
        sig = None
        try:
            sig = str(inspect.signature(obj))
        except (ValueError, TypeError):
            pass
        doc = inspect.getdoc(obj) or ""
        # Extract code examples from docstring
        examples = []
        for m in re.finditer(r">>>\\s+(.+)", doc):
            examples.append(m.group(1))
        for m in re.finditer(r"```python\\n(.*?)```", doc, re.DOTALL):
            examples.append(m.group(1).strip())

        results["classes"].append({
            "name": name,
            "signature": sig,
            "has_doc": bool(doc.strip()),
            "doc_examples": examples,
            "module": getattr(obj, "__module__", ""),
        })

    # Functions
    elif callable(obj) and not isinstance(obj, type):
        sig = None
        try:
            sig = str(inspect.signature(obj))
        except (ValueError, TypeError):
            pass
        doc = inspect.getdoc(obj) or ""
        examples = []
        for m in re.finditer(r">>>\\s+(.+)", doc):
            examples.append(m.group(1))
        for m in re.finditer(r"```python\\n(.*?)```", doc, re.DOTALL):
            examples.append(m.group(1).strip())

        results["functions"].append({
            "name": name,
            "signature": sig,
            "has_doc": bool(doc.strip()),
            "doc_examples": examples,
        })

json.dump(results, sys.stdout)
'''

# Script to test constructors with no args / default args
CONSTRUCTOR_TEST_SCRIPT = '''\
import importlib
import inspect
import json
import sys

pkg_name = sys.argv[1]
mod = importlib.import_module(pkg_name)

results = []
for name in sorted(dir(mod)):
    if name.startswith("_"):
        continue
    obj = getattr(mod, name)
    if not inspect.isclass(obj):
        continue

    # Try instantiating with no args
    try:
        sig = inspect.signature(obj)
        # Check if all params have defaults
        required = [p for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.name != "self"
                    and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        if not required:
            instance = obj()
            results.append({"class": name, "status": "ok", "error": None})
        else:
            # Try with positional string arg (common pattern: File("path"), Name("x"))
            try:
                instance = obj("test")
                results.append({"class": name, "status": "ok_positional", "error": None})
            except Exception as e:
                # Try keyword-only
                first_param = required[0].name if required else None
                if first_param:
                    try:
                        instance = obj(**{first_param: "test"})
                        results.append({"class": name, "status": "ok_keyword_only", "error": None,
                                       "note": f"Positional arg fails, must use {first_param}='test'"})
                    except Exception as e2:
                        results.append({"class": name, "status": "skip", "error": None,
                                       "note": f"Requires specific args: {sig}"})
                else:
                    results.append({"class": name, "status": "skip", "error": str(e)[:200]})
    except Exception as e:
        results.append({"class": name, "status": "error", "error": str(e)[:200]})

json.dump(results, sys.stdout)
'''


class Step:
    def generate_script(self, ctx: StepContext) -> Path:
        script = ctx.output_dir / "introspect.sh"

        intro_py = ctx.output_dir / "_introspect.py"
        intro_py.write_text(INTROSPECT_SCRIPT)

        ctor_py = ctx.output_dir / "_constructor_test.py"
        ctor_py.write_text(CONSTRUCTOR_TEST_SCRIPT)

        # Write a display script that formats the JSON output nicely
        display_py = ctx.output_dir / "_display_api.py"
        display_py.write_text(textwrap.dedent("""\
            import json, sys
            data = json.load(open(sys.argv[1]))
            classes = data.get("classes", [])
            functions = data.get("functions", [])
            print(f"Classes: {len(classes)}")
            for c in classes:
                doc = " (documented)" if c["has_doc"] else " (NO docstring)"
                ex = f" [{len(c['doc_examples'])} examples]" if c.get("doc_examples") else ""
                sig = c.get("signature") or "(?)"
                print(f"  {c['name']}{sig}{doc}{ex}")
            print(f"Functions: {len(functions)}")
            for f in functions:
                doc = " (documented)" if f["has_doc"] else " (NO docstring)"
                ex = f" [{len(f['doc_examples'])} examples]" if f.get("doc_examples") else ""
                sig = f.get("signature") or "(?)"
                print(f"  {f['name']}{sig}{doc}{ex}")
        """))

        display_ctor_py = ctx.output_dir / "_display_ctor.py"
        display_ctor_py.write_text(textwrap.dedent("""\
            import json, sys
            data = json.load(open(sys.argv[1]))
            for r in data:
                status = r["status"]
                note = f" -- {r['note']}" if r.get("note") else ""
                err = f" -- {r['error']}" if r.get("error") else ""
                print(f"  {r['class']}: {status}{note}{err}")
        """))

        script.write_text(f"""#!/bin/bash
export PATH="{ctx.venv_path}/bin:$PATH"

echo "\\$ python _introspect.py {ctx.pkg}  # discover public API"
{ctx.python_bin} {intro_py} {ctx.pkg} > /tmp/_api_out.json 2>&1
{ctx.python_bin} {display_py} /tmp/_api_out.json 2>&1

echo ""
echo "\\$ python _constructor_test.py {ctx.pkg}  # test constructors"
{ctx.python_bin} {ctor_py} {ctx.pkg} > /tmp/_ctor_out.json 2>&1
{ctx.python_bin} {display_ctor_py} /tmp/_ctor_out.json 2>&1
""")
        script.chmod(0o755)
        return script

    def evaluate(self, ctx: StepContext) -> list[Annotation]:
        annotations = []
        import json as json_mod

        # Run introspection
        intro_py = ctx.output_dir / "_introspect.py"
        intro_py.write_text(INTROSPECT_SCRIPT)

        result = subprocess.run(
            [str(ctx.python_bin), str(intro_py), ctx.pkg],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            annotations.append(Annotation(
                Severity.FAIL, "Introspection failed",
                f"Could not inspect {ctx.pkg}: {result.stderr[:200]}",
                Category.BUG,
            ))
            return annotations

        try:
            api = json_mod.loads(result.stdout)
        except Exception:
            annotations.append(Annotation(
                Severity.WARNING, "Introspection output unparseable",
                f"stdout: {result.stdout[:200]}",
                Category.BUG,
            ))
            return annotations

        classes = api.get("classes", [])
        functions = api.get("functions", [])

        total_public = len(classes) + len(functions)
        documented = sum(1 for c in classes if c["has_doc"]) + sum(1 for f in functions if f["has_doc"])
        undocumented = total_public - documented

        annotations.append(Annotation(
            Severity.PASS,
            f"Public API: {len(classes)} classes, {len(functions)} functions",
            f"{documented}/{total_public} have docstrings.",
        ))

        if undocumented > 0:
            missing = [c["name"] for c in classes if not c["has_doc"]] + \
                      [f["name"] for f in functions if not f["has_doc"]]
            annotations.append(Annotation(
                Severity.WARNING,
                f"{undocumented} public symbols without docstrings",
                f"Missing docs: {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}",
                Category.DOCS,
            ))

        # Test docstring examples
        all_examples = []
        for item in classes + functions:
            for ex in item.get("doc_examples", []):
                all_examples.append((item["name"], ex))

        if all_examples:
            passed = 0
            failed = 0
            failures = []
            for name, code in all_examples:
                test_file = ctx.output_dir / f"_doctest_{name}.py"
                # Wrap in try/except to capture error
                wrapped = f"import {ctx.pkg}\ntry:\n"
                for line in code.split("\n"):
                    wrapped += f"    {line}\n"
                wrapped += f"    print('OK')\nexcept Exception as e:\n    print(f'FAIL: {{e}}')\n"
                test_file.write_text(wrapped)

                r = subprocess.run(
                    [str(ctx.python_bin), str(test_file)],
                    capture_output=True, text=True, timeout=15,
                    cwd=ctx.output_dir,
                )
                output = (r.stdout + r.stderr).strip()
                if r.returncode == 0 and "FAIL:" not in output:
                    passed += 1
                else:
                    failed += 1
                    err = output.split("\n")[-1][:150]
                    failures.append(f"`{name}`: {err}")

            if passed > 0:
                annotations.append(Annotation(
                    Severity.PASS,
                    f"{passed} docstring examples pass",
                    "Code examples in docstrings execute without errors.",
                ))
            if failed > 0:
                annotations.append(Annotation(
                    Severity.FAIL,
                    f"{failed} docstring examples broken",
                    "; ".join(failures[:5]),
                    Category.BUG,
                ))
        else:
            annotations.append(Annotation(
                Severity.WARNING,
                "No docstring examples found",
                "Public classes and functions have no executable code examples in their docstrings.",
                Category.DOCS,
            ))

        # Run constructor tests
        ctor_py = ctx.output_dir / "_constructor_test.py"
        ctor_py.write_text(CONSTRUCTOR_TEST_SCRIPT)

        result = subprocess.run(
            [str(ctx.python_bin), str(ctor_py), ctx.pkg],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode == 0:
            try:
                ctor_results = json_mod.loads(result.stdout)
            except Exception:
                ctor_results = []

            keyword_only = [r for r in ctor_results if r["status"] == "ok_keyword_only"]
            errors = [r for r in ctor_results if r["status"] == "error"]

            if keyword_only:
                annotations.append(Annotation(
                    Severity.WARNING,
                    f"{len(keyword_only)} classes reject positional args",
                    "These classes require keyword arguments only (Pydantic models?): " +
                    ", ".join(f"`{r['class']}` ({r.get('note', '')})" for r in keyword_only[:5]),
                    Category.UX,
                ))

            if errors:
                annotations.append(Annotation(
                    Severity.WARNING,
                    f"{len(errors)} constructors raise errors",
                    "; ".join(f"`{r['class']}`: {r['error'][:80]}" for r in errors[:5]),
                    Category.BUG,
                ))

        return annotations
