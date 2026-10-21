"""Shared contract tests. Each file is parametrised over implementations of one
interface; a provider is "done" when it is added to the list and passes. Real providers
add a factory wrapped in pytest.param(..., marks=pytest.mark.live) that skips without
its key."""
