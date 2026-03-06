from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class STTProvider(ABC):
    provider_id: str
    supported_languages: list[str]

    @abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str,
        sample_rate: int = 16000,
    ) -> AsyncIterator[dict]: ...

    @abstractmethod
    def cost_per_second(self) -> float: ...


class TTSProvider(ABC):
    provider_id: str
    supported_languages: list[str]

    @abstractmethod
    async def synthesize_stream(
        self,
        text_stream: AsyncIterator[str],
        voice: str,
    ) -> AsyncIterator[bytes]: ...

    @abstractmethod
    def cost_per_character(self) -> float: ...


class LLMProvider(ABC):
    provider_id: str

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    def cost_per_1k_tokens(self) -> tuple[float, float]: ...


class TelephonyAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def initiate_call(self, to_number: str, from_number: str, webhook_url: str) -> dict: ...

    @abstractmethod
    async def end_call(self, call_id: str) -> None: ...

    @abstractmethod
    def parse_inbound_webhook(self, payload: dict) -> dict: ...
