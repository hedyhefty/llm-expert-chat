from collections.abc import AsyncIterator
import json

import httpx


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line.removeprefix("data: ")
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if content:
                            yield content

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
