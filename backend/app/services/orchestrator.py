from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from enum import Enum
import json

import httpx

from app.core.encryption import decrypt_secret
from app.db.models import LLMProvider
from app.services.debate_team import DebateTeam
from app.services.expert_team import ExpertTeam
from app.services.llm_provider import OpenAICompatibleClient
from app.services.model_routing import ModelRoutes


class ChatMode(str, Enum):
    NORMAL = "normal"
    EXPERT = "expert"
    DEBATE = "debate"


@dataclass(frozen=True)
class StreamPart:
    event: str
    content: str


class ChatOrchestrator:
    async def stream_reply(
        self,
        message: str,
        mode: ChatMode,
        provider: LLMProvider | None = None,
        providers: list[LLMProvider] | None = None,
        routes: ModelRoutes | None = None,
    ) -> AsyncIterator[str]:
        if mode in {ChatMode.EXPERT, ChatMode.DEBATE}:
            expert_providers = (
                routes.enabled_providers
                if routes
                else providers or ([provider] if provider is not None else [])
            )
            if not expert_providers:
                yield _to_sse(f"{mode.value.title()} mode needs at least one enabled provider.")
                return

            team = (
                DebateTeam(
                    expert_providers,
                    debater_providers=routes.debate.debater_providers if routes else None,
                    synthesizer_provider=routes.debate.synthesizer_provider if routes else None,
                )
                if mode == ChatMode.DEBATE
                else ExpertTeam(
                    expert_providers,
                    planner_provider=routes.expert.planner_provider if routes else None,
                    expert_providers=routes.expert.expert_providers if routes else None,
                    reviewer_provider=routes.expert.reviewer_provider if routes else None,
                    synthesizer_provider=routes.expert.synthesizer_provider if routes else None,
                )
            )
            async for event in team.stream(message):
                yield _to_json_sse(event.event, asdict(event))
                if event.role == "synthesizer" and event.event == "expert_delta":
                    yield _to_sse(event.content, event="message")
                elif event.role == "synthesizer" and event.event == "expert_reasoning_delta":
                    yield _to_sse(event.reasoning, event="reasoning")

            yield "event: done\ndata: [DONE]\n\n"
            return

        normal_providers = _normal_provider_candidates(provider, providers)
        if not normal_providers:
            yield _to_sse("Normal chat placeholder. Configure a provider to enable real LLM responses.")
            return

        messages = [{"role": "user", "content": message}]
        errors: list[str] = []

        for candidate in normal_providers:
            client = OpenAICompatibleClient(
                base_url=candidate.base_url,
                api_key=decrypt_secret(candidate.encrypted_api_key),
                model=candidate.model,
            )
            splitter = ReasoningSplitter()
            emitted_content = False
            try:
                async for chunk in client.stream_chat(messages):
                    if chunk.event == "reasoning":
                        yield _to_sse(chunk.content, event="reasoning")
                        continue

                    for part in splitter.feed(chunk.content):
                        emitted_content = True
                        yield _to_sse(part.content, event=part.event)
                for part in splitter.flush():
                    emitted_content = True
                    yield _to_sse(part.content, event=part.event)
            except httpx.HTTPError as error:
                errors.append(f"{candidate.name} / {candidate.model}: {error}")
                if emitted_content:
                    yield _to_sse(f"\n\nProvider stream interrupted: {error}")
                    yield "event: done\ndata: [DONE]\n\n"
                    return
                continue

            yield "event: done\ndata: [DONE]\n\n"
            return

        detail = "; ".join(errors) if errors else "No provider returned a response."
        yield _to_sse(f"All configured providers failed: {detail}")
        yield "event: done\ndata: [DONE]\n\n"


def _to_sse(data: str, event: str | None = None) -> str:
    lines = data.splitlines() or [""]
    event_line = f"event: {event}\n" if event else ""
    return event_line + "".join(f"data: {line}\n" for line in lines) + "\n"


def _to_json_sse(event: str, data: dict[str, object]) -> str:
    return _to_sse(json.dumps(data, ensure_ascii=False), event=event)


def _normal_provider_candidates(
    provider: LLMProvider | None,
    providers: list[LLMProvider] | None,
) -> list[LLMProvider]:
    candidates: list[LLMProvider] = []
    if provider is not None:
        candidates.append(provider)
    for candidate in providers or []:
        if all(existing.id != candidate.id for existing in candidates):
            candidates.append(candidate)
    return candidates


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
