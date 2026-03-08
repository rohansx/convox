from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from openai import AsyncOpenAI

from convox.config import settings
from convox.providers.base import LLMProvider

logger = structlog.get_logger()

# GPT-4o-mini pricing (per 1k tokens)
GPT4O_MINI_INPUT_PER_1K = 0.00015
GPT4O_MINI_OUTPUT_PER_1K = 0.0006


class OpenAILLM(LLMProvider):
    provider_id = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set CONVOX_OPENAI_API_KEY.")
        self.client = AsyncOpenAI(api_key=self.api_key)

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
    ) -> dict:
        """Non-streaming chat completion. Returns full response with usage."""
        full_messages = [{"role": "system", "content": system_prompt}, *messages]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
        )

        choice = response.choices[0]
        usage = response.usage

        logger.info(
            "openai_chat_complete",
            model=self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

        return {
            "content": choice.message.content or "",
            "role": "assistant",
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "model": self.model,
        }

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Streaming chat completion. Yields text chunks."""
        full_messages = [{"role": "system", "content": system_prompt}, *messages]

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def cost_per_1k_tokens(self) -> tuple[float, float]:
        """Returns (input_cost, output_cost) per 1k tokens."""
        if "gpt-4o-mini" in self.model:
            return (GPT4O_MINI_INPUT_PER_1K, GPT4O_MINI_OUTPUT_PER_1K)
        if "gpt-4o" in self.model:
            return (0.0025, 0.01)
        if "gpt-4.1" in self.model:
            return (0.002, 0.008)
        # Default to gpt-4o-mini pricing
        return (GPT4O_MINI_INPUT_PER_1K, GPT4O_MINI_OUTPUT_PER_1K)
