"""Claude (Anthropic) API client wrapper."""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic
from fastapi import Request

log = structlog.get_logger()


class ClaudeClient:
    """Async wrapper around AsyncAnthropic for Claude conversations.

    Provides simple ask() for single-turn and ask_with_history()
    for multi-turn conversations.  Logs token usage on every call.
    """

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str = "claude-opus-4-6",
    ) -> None:
        self.client = client
        self.model = model

    async def ask(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Send a single prompt and return the text response."""
        log.debug(
            "claude_ask",
            model=self.model,
            prompt_length=len(prompt),
        )

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        message = await self.client.messages.create(**kwargs)

        log.info(
            "claude_response",
            model=self.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

        return message.content[0].text

    async def ask_with_history(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Send a multi-turn conversation and return the latest response."""
        log.debug(
            "claude_ask_with_history",
            model=self.model,
            message_count=len(messages),
        )

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        message = await self.client.messages.create(**kwargs)

        log.info(
            "claude_response",
            model=self.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

        return message.content[0].text


def get_claude_client(request: Request) -> ClaudeClient:
    """FastAPI dependency — retrieve ClaudeClient from app state."""
    return request.app.state.claude_client
