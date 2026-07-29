"""Assertion registry.

An assertion receives the trial bundle and its spec, and returns a verdict with
the evidence that justifies it. Evidence is not decoration: a failure a developer
cannot trace back to a turn is a failure they will learn to ignore.
"""

from __future__ import annotations

from collections.abc import Callable

from convox.model.enums import AssertionStatus, Layer
from convox.model.scenario import AssertionSpec
from convox.model.trial import AssertionResult, TrialArtifacts

AssertionFn = Callable[[TrialArtifacts, AssertionSpec], AssertionResult]

_ASSERTIONS: dict[str, AssertionFn] = {}
#: Capability each assertion needs from the target, if any.
_REQUIRES: dict[str, str] = {}


def assertion(name: str, *, requires: str | None = None) -> Callable[[AssertionFn], AssertionFn]:
    """Register an assertion under its public name.

    ``requires`` names a target capability. When the target does not declare it,
    the assertion short-circuits to ``unsupported`` — never to a pass.
    """

    def decorator(fn: AssertionFn) -> AssertionFn:
        _ASSERTIONS[name] = fn
        if requires:
            _REQUIRES[name] = requires
        return fn

    return decorator


def names() -> list[str]:
    return sorted(_ASSERTIONS)


def is_registered(name: str) -> bool:
    return name in _ASSERTIONS


def required_capability(name: str) -> str | None:
    return _REQUIRES.get(name)


def ok(spec: AssertionSpec, **kwargs: object) -> AssertionResult:
    return AssertionResult(name=spec.name, status=AssertionStatus.PASS, soft=spec.soft, **kwargs)  # type: ignore[arg-type]


def failed(
    spec: AssertionSpec,
    message: str,
    *,
    layer: Layer | None = None,
    **kwargs: object,
) -> AssertionResult:
    return AssertionResult(
        name=spec.name,
        status=AssertionStatus.FAIL,
        soft=spec.soft,
        message=message,
        suspected_layer=layer,
        **kwargs,  # type: ignore[arg-type]
    )


def unsupported(spec: AssertionSpec, reason: str) -> AssertionResult:
    return AssertionResult(
        name=spec.name,
        status=AssertionStatus.UNSUPPORTED,
        soft=spec.soft,
        message=reason,
    )


def errored(spec: AssertionSpec, reason: str) -> AssertionResult:
    return AssertionResult(
        name=spec.name,
        status=AssertionStatus.ERROR,
        soft=spec.soft,
        message=reason,
    )


def run(artifacts: TrialArtifacts, spec: AssertionSpec) -> AssertionResult:
    fn = _ASSERTIONS.get(spec.name)
    if fn is None:
        return errored(spec, f"unknown assertion {spec.name!r}")

    capability = _REQUIRES.get(spec.name)
    if capability and not artifacts.supports(capability):
        return unsupported(spec, f"target does not provide `{capability}`")

    try:
        return fn(artifacts, spec)
    except Exception as exc:  # noqa: BLE001 - a broken assertion must not kill a run
        return errored(spec, f"{type(exc).__name__}: {exc}")
