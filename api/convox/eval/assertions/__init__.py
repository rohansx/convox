"""Built-in assertion implementations."""

_LOADED = False


def load_builtins() -> None:
    """Import every built-in assertion module so the registry is populated."""
    global _LOADED
    if _LOADED:
        return
    from convox.eval.assertions import audio, deterministic, judges  # noqa: F401

    _LOADED = True
