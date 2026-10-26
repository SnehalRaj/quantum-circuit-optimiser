"""Reusable steps for exact Pareto moves on the public Q/T curve.

The steps generalize the frozen campaign scripts in runs/frontier-levers-20261025/code/:

- `levers`: candidate tree (parent overlay on a fixed harness, counted edits), build, emit,
  development check and paired A/B compare.
- `screen`: one trusted-evaluator screen per variant; a second screen is refused.
- `audit_slot`: slot reservation under a file lock; protocol frozen with a fresh label before the run.
- `reference`: sealed public-reference folder derived from reviewed scripts by counted substitutions.
- `submit`: gate on one operation hash, public note with disclosure counts, one send.
- `explore`: exploration-only override builds; development data only.

Read docs/pareto-moves.md for the method, the evidence and the commands.
"""
from .layout import Campaign, MoveError

__all__ = ['Campaign', 'MoveError']
