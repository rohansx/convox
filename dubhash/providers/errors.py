"""Provider error types, shared by every vendor client and the registry."""


class ProviderError(RuntimeError):
    """A vendor call failed (HTTP error, timeout, fatal socket error)."""


class ProviderUnavailable(ProviderError):
    """The provider cannot be built: unknown name or missing API key."""
