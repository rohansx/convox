"""The numeric comparison grammar shared by every threshold assertion.

Supports ``lt``, ``lte``, ``gt``, ``gte``, ``eq``, and ``between``, optionally
nested under a percentile selector (``p50``, ``p90``, ``p95``, ``p99``, ``max``,
``min``, ``mean``). A bare number is treated as ``eq``.
"""

from __future__ import annotations

from statistics import fmean
from typing import Any

SELECTORS = ("p50", "p90", "p95", "p99", "max", "min", "mean")
OPERATORS = ("lt", "lte", "gt", "gte", "eq", "between")


class ComparisonError(ValueError):
    """The comparison expression itself is malformed."""


def percentile(samples: list[float], pct: float) -> float:
    """Linear-interpolated percentile. ``samples`` need not be sorted."""
    if not samples:
        raise ComparisonError("no samples")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def select(samples: list[float], selector: str) -> float:
    match selector:
        case "max":
            return float(max(samples))
        case "min":
            return float(min(samples))
        case "mean":
            return float(fmean(samples))
        case s if s.startswith("p"):
            return percentile(samples, float(s[1:]))
    raise ComparisonError(f"unknown selector {selector!r}")


def check(actual: float, expr: Any) -> tuple[bool, str]:
    """Evaluate one comparison expression against a value.

    Returns ``(ok, description)`` where the description renders the expectation
    for a failure message.
    """
    if isinstance(expr, int | float) and not isinstance(expr, bool):
        return actual == expr, f"== {expr}"
    if not isinstance(expr, dict):
        raise ComparisonError(f"expected a number or comparison mapping, got {expr!r}")

    unknown = set(expr) - set(OPERATORS)
    if unknown:
        raise ComparisonError(f"unknown comparison operator(s): {', '.join(sorted(unknown))}")

    ok = True
    parts: list[str] = []
    for op, bound in expr.items():
        match op:
            case "lt":
                ok &= actual < bound
                parts.append(f"< {bound}")
            case "lte":
                ok &= actual <= bound
                parts.append(f"<= {bound}")
            case "gt":
                ok &= actual > bound
                parts.append(f"> {bound}")
            case "gte":
                ok &= actual >= bound
                parts.append(f">= {bound}")
            case "eq":
                ok &= actual == bound
                parts.append(f"== {bound}")
            case "between":
                if not isinstance(bound, list | tuple) or len(bound) != 2:
                    raise ComparisonError("`between` takes [low, high]")
                low, high = bound
                ok &= low <= actual <= high
                parts.append(f"in [{low}, {high}]")
    return bool(ok), " and ".join(parts)


def evaluate(samples: list[float], expr: Any) -> tuple[bool, dict[str, Any], str]:
    """Evaluate an expression that may be keyed by percentile selectors.

    ``{"p95": {"lt": 1200}, "max": {"lt": 2500}}`` checks two selectors;
    ``{"lt": 1200}`` applies to the mean of the samples.

    Returns ``(ok, actual_by_selector, description)``.
    """
    if not samples:
        raise ComparisonError("no samples to compare")

    if isinstance(expr, dict) and set(expr) & set(SELECTORS):
        unknown = set(expr) - set(SELECTORS)
        if unknown:
            raise ComparisonError(f"unknown selector(s): {', '.join(sorted(unknown))}")
        ok = True
        actual: dict[str, Any] = {}
        described: list[str] = []
        for selector, sub in expr.items():
            value = select(samples, selector)
            passed, desc = check(value, sub)
            ok &= passed
            actual[selector] = round(value, 3)
            described.append(f"{selector} {desc}")
        return bool(ok), actual, ", ".join(described)

    value = select(samples, "mean")
    passed, desc = check(value, expr)
    return passed, {"mean": round(value, 3)}, f"mean {desc}"
