from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

import httpx

from app.core.encryption import decrypt_secret
from app.db.models import LLMProvider
from app.services.expert_team import ExpertTeam
from app.services.llm_provider import OpenAICompatibleClient


class ChatMode(str, Enum):
    NORMAL = "normal"
    EXPERT = "expert"


@dataclass(frozen=True)
class StreamPart:
    event: str
    content: str


class ChatOrchestrator:
    async def stream_reply(self, message: str, mode: ChatMode, provider: LLMProvider | None = None) -> AsyncIterator[str]:
        if mode == ChatMode.EXPERT:
            team = ExpertTeam()
            results = await team.run(message)
            final = next(result.content for result in results if result.role_name == "synthesizer")
            yield _to_sse(final)
            return

        if provider is None:
            yield _to_sse("Normal chat placeholder. Configure a provider to enable real LLM responses.")
            return

        client = OpenAICompatibleClient(
            base_url=provider.base_url,
            api_key=decrypt_secret(provider.encrypted_api_key),
            model=provider.model,
        )
        messages = [{"role": "user", "content": message}]

        splitter = ReasoningSplitter()
        try:
            async for token in client.stream_chat(messages):
                for part in splitter.feed(token):
                    yield _to_sse(part.content, event=part.event)
            for part in splitter.flush():
                yield _to_sse(part.content, event=part.event)
        except httpx.HTTPError as error:
            yield _to_sse(f"Provider request failed: {error}")
        else:
            yield "event: done\ndata: [DONE]\n\n"


def _to_sse(data: str, event: str | None = None) -> str:
    lines = data.splitlines() or [""]
    event_line = f"event: {event}\n" if event else ""
    return event_line + "".join(f"data: {line}\n" for line in lines) + "\n"


class ReasoningSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_reasoning = False

    def feed(self, token: str) -> list[StreamPart]:
        self.buffer += token
        parts: list[StreamPart] = []

        while self.buffer:
            lower_buffer = self.buffer.lower()
            if self.in_reasoning:
                end_index = lower_buffer.find("</think>")
                if end_index == -1:
                    keep = _partial_tag_suffix_length(lower_buffer, "</think>")
                    emit_text = self.buffer[:-keep] if keep else self.buffer
                    self.buffer = self.buffer[-keep:] if keep else ""
                    if emit_text:
                        parts.append(StreamPart(event="reasoning", content=emit_text))
                    break

                if end_index:
                    parts.append(StreamPart(event="reasoning", content=self.buffer[:end_index]))
                self.buffer = self.buffer[end_index + len("</think>") :]
                self.in_reasoning = False
                continue

            start_index = lower_buffer.find("<think>")
            if start_index == -1:
                keep = _partial_tag_suffix_length(lower_buffer, "<think>")
                emit_text = self.buffer[:-keep] if keep else self.buffer
                self.buffer = self.buffer[-keep:] if keep else ""
                if emit_text:
                    parts.append(StreamPart(event="message", content=emit_text))
                break

            if start_index:
                parts.append(StreamPart(event="message", content=self.buffer[:start_index]))
            self.buffer = self.buffer[start_index + len("<think>") :]
            self.in_reasoning = True

        return parts

    def flush(self) -> list[StreamPart]:
        if not self.buffer:
            return []

        event = "reasoning" if self.in_reasoning else "message"
        content = self.buffer
        self.buffer = ""
        self.in_reasoning = False
        return [StreamPart(event=event, content=content)]


def _partial_tag_suffix_length(text: str, tag: str) -> int:
    max_length = min(len(text), len(tag) - 1)
    for length in range(max_length, 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0
