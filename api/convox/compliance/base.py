from abc import ABC, abstractmethod


class ComplianceModule(ABC):
    module_id: str
    enabled: bool = False

    @abstractmethod
    async def on_call_start(self, session_id: str, caller: str) -> bool:
        """Called before a call begins. Return False to block the call."""
        ...

    @abstractmethod
    async def on_call_end(self, session_id: str) -> None:
        """Called after a call ends. Handle retention, logging, etc."""
        ...
