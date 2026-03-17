"""Step registry for repo-eval."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_eval.steps.base import EvalStep


def load_step(module_path: str) -> EvalStep:
    mod = importlib.import_module(module_path)
    step_cls = getattr(mod, "Step", None)
    if step_cls is None:
        raise ImportError(f"Module {module_path} has no 'Step' class")
    return step_cls()
