from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any, Literal

import httpx


@dataclass(frozen=True)
class LLMStreamChunk:
    event: Literal["message", "reasoning"]
    content: str


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[LLMStreamChunk]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        payload.update(self._default_extra_body())
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    data = line.removeprefix("data:").lstrip()
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    for stream_chunk in _parse_stream_chunk(chunk):
                        yield stream_chunk

    def _default_extra_body(self) -> dict[str, Any]:
        if "bigmodel.cn" in self.base_url and self.model.lower().startswith("glm-5"):
            return {"thinking": {"type": "enabled"}}
        return {}

    async def test_connection(self) -> tuple[bool, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/models", headers=headers)

        if 200 <= response.status_code < 300:
            return True, "Connection succeeded"

        detail = response.text.strip()
        if len(detail) > 240:
            detail = f"{detail[:240]}..."
        return False, f"Provider returned HTTP {response.status_code}: {detail}"


def _parse_stream_chunk(chunk: dict[str, Any]) -> list[LLMStreamChunk]:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return []

    result: list[LLMStreamChunk] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            continue

        reasoning = _text_value(delta.get("reasoning_content"))
        content = _text_value(delta.get("content"))

        if reasoning:
            result.append(LLMStreamChunk(event="reasoning", content=reasoning))
        if content:
            result.append(LLMStreamChunk(event="message", content=content))

    return result


def _text_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) or None

    return None
