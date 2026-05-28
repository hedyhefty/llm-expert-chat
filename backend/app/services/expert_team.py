from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.encryption import decrypt_secret
from app.db.models import LLMProvider
from app.services.llm_provider import OpenAICompatibleClient

ExpertEventType = Literal["expert_start", "expert_delta", "expert_reasoning_delta", "expert_done"]


@dataclass(frozen=True)
class ExpertRole:
    key: str
    title: str
    provider: LLMProvider


@dataclass(frozen=True)
class ExpertStreamEvent:
    event: ExpertEventType
    role: str
    title: str
    provider_name: str
    model: str
    content: str = ""
    reasoning: str = ""
    error: str | None = None


class ExpertTeam:
    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("Expert mode requires at least one enabled provider")

        self.roles = [
            ExpertRole(key="expert_a", title="Expert A", provider=providers[0]),
            ExpertRole(key="expert_b", title="Expert B", provider=providers[1] if len(providers) > 1 else providers[0]),
            ExpertRole(key="synthesizer", title="Synthesizer", provider=providers[2] if len(providers) > 2 else providers[0]),
        ]

    async def stream(self, message: str) -> AsyncIterator[ExpertStreamEvent]:
        expert_a = ""
        expert_b = ""

        async for event in self._stream_role(self.roles[0], self._expert_a_messages(message)):
            if event.event == "expert_delta":
                expert_a += event.content
            yield event

        async for event in self._stream_role(self.roles[1], self._expert_b_messages(message, expert_a)):
            if event.event == "expert_delta":
                expert_b += event.content
            yield event

        async for event in self._stream_role(
            self.roles[2],
            self._synthesizer_messages(message, expert_a, expert_b),
        ):
            yield event

    async def _stream_role(
        self,
        role: ExpertRole,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ExpertStreamEvent]:
        yield ExpertStreamEvent(
            event="expert_start",
            role=role.key,
            title=role.title,
            provider_name=role.provider.name,
            model=role.provider.model,
        )

        content = ""
        reasoning = ""
        error_text: str | None = None
        splitter = _ThinkTagSplitter()
        client = OpenAICompatibleClient(
            base_url=role.provider.base_url,
            api_key=decrypt_secret(role.provider.encrypted_api_key),
            model=role.provider.model,
        )

        try:
            async for chunk in client.stream_chat(messages):
                if chunk.event == "reasoning":
                    reasoning += chunk.content
                    yield ExpertStreamEvent(
                        event="expert_reasoning_delta",
                        role=role.key,
                        title=role.title,
                        provider_name=role.provider.name,
                        model=role.provider.model,
                        reasoning=chunk.content,
                    )
                else:
                    for part in splitter.feed(chunk.content):
                        if part.event == "reasoning":
                            reasoning += part.content
                            yield ExpertStreamEvent(
                                event="expert_reasoning_delta",
                                role=role.key,
                                title=role.title,
                                provider_name=role.provider.name,
                                model=role.provider.model,
                                reasoning=part.content,
                            )
                        else:
                            content += part.content
                            yield ExpertStreamEvent(
                                event="expert_delta",
                                role=role.key,
                                title=role.title,
                                provider_name=role.provider.name,
                                model=role.provider.model,
                                content=part.content,
                            )
            for part in splitter.flush():
                if part.event == "reasoning":
                    reasoning += part.content
                    yield ExpertStreamEvent(
                        event="expert_reasoning_delta",
                        role=role.key,
                        title=role.title,
                        provider_name=role.provider.name,
                        model=role.provider.model,
                        reasoning=part.content,
                    )
                else:
                    content += part.content
                    yield ExpertStreamEvent(
                        event="expert_delta",
                        role=role.key,
                        title=role.title,
                        provider_name=role.provider.name,
                        model=role.provider.model,
                        content=part.content,
                    )
        except httpx.HTTPError as error:
            error_text = str(error)
            content = f"{role.title} request failed: {error}"
            yield ExpertStreamEvent(
                event="expert_delta",
                role=role.key,
                title=role.title,
                provider_name=role.provider.name,
                model=role.provider.model,
                content=content,
                error=str(error),
            )

        yield ExpertStreamEvent(
            event="expert_done",
            role=role.key,
            title=role.title,
            provider_name=role.provider.name,
            model=role.provider.model,
            content=content,
            reasoning=reasoning,
            error=error_text,
        )

    def _expert_a_messages(self, message: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are Expert A in a small expert team. Provide a complete, direct, and useful answer. "
                    "State assumptions briefly when needed. Do not mention internal team process."
                ),
            },
            {"role": "user", "content": message},
        ]

    def _expert_b_messages(self, message: str, expert_a: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are Expert B. Review Expert A's answer critically. Find mistakes, omissions, "
                    "edge cases, and useful additions. Be concise and constructive."
                ),
            },
            {
                "role": "user",
                "content": f"Original user request:\n{message}\n\nExpert A answer:\n{expert_a}",
            },
        ]

    def _synthesizer_messages(self, message: str, expert_a: str, expert_b: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the Synthesizer. Produce the final user-facing answer by combining Expert A's "
                    "main answer with Expert B's critique. Do not expose the deliberation unless it is useful."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Expert A answer:\n{expert_a}\n\n"
                    f"Expert B critique and additions:\n{expert_b}"
                ),
            },
        ]


@dataclass(frozen=True)
class _ThinkPart:
    event: Literal["message", "reasoning"]
    content: str


class _ThinkTagSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_reasoning = False

    def feed(self, token: str) -> list[_ThinkPart]:
        self.buffer += token
        parts: list[_ThinkPart] = []

        while self.buffer:
            lower_buffer = self.buffer.lower()
            if self.in_reasoning:
                end_index = lower_buffer.find("</think>")
                if end_index == -1:
                    keep = _partial_tag_suffix_length(lower_buffer, "</think>")
                    emit_text = self.buffer[:-keep] if keep else self.buffer
                    self.buffer = self.buffer[-keep:] if keep else ""
                    if emit_text:
                        parts.append(_ThinkPart(event="reasoning", content=emit_text))
                    break

                if end_index:
                    parts.append(_ThinkPart(event="reasoning", content=self.buffer[:end_index]))
                self.buffer = self.buffer[end_index + len("</think>") :]
                self.in_reasoning = False
                continue

            start_index = lower_buffer.find("<think>")
            if start_index == -1:
                keep = _partial_tag_suffix_length(lower_buffer, "<think>")
                emit_text = self.buffer[:-keep] if keep else self.buffer
                self.buffer = self.buffer[-keep:] if keep else ""
                if emit_text:
                    parts.append(_ThinkPart(event="message", content=emit_text))
                break

            if start_index:
                parts.append(_ThinkPart(event="message", content=self.buffer[:start_index]))
            self.buffer = self.buffer[start_index + len("<think>") :]
            self.in_reasoning = True

        return parts

    def flush(self) -> list[_ThinkPart]:
        if not self.buffer:
            return []

        event = "reasoning" if self.in_reasoning else "message"
        content = self.buffer
        self.buffer = ""
        self.in_reasoning = False
        return [_ThinkPart(event=event, content=content)]


def _partial_tag_suffix_length(text: str, tag: str) -> int:
    max_length = min(len(text), len(tag) - 1)
    for length in range(max_length, 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0
