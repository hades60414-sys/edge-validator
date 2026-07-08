"""Edge Validator engine — 純 numpy/pandas 誠實裁判引擎(Pyodide-ready)。

公開入口:
    from engine import analyze
    result = analyze(payload)
"""
from . import statshim
from .judge_web import (
    analyze,
    compute_metrics,
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
    pbo_cscv,
    run_fwer_gates,
    permutation_null,
    cost_stress,
)

__all__ = [
    "analyze", "compute_metrics", "deflated_sharpe", "expected_max_sharpe",
    "probabilistic_sharpe", "pbo_cscv", "run_fwer_gates", "permutation_null",
    "cost_stress", "statshim",
]
