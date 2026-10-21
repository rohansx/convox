"""Provider errors and selection by name."""

from __future__ import annotations


class ProviderError(RuntimeError):
    """A vendor call failed (HTTP error, timeout, fatal socket error)."""


class ProviderUnavailable(ProviderError):
    """The provider cannot be built: unknown name or missing API key."""
