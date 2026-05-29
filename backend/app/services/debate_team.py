import asyncio
from collections.abc import AsyncIterator

import httpx

from app.core.encryption import decrypt_secret
from app.db.models import LLMProvider
from app.services.expert_team import ExpertRole, ExpertStreamEvent, RoleResult, _ThinkTagSplitter
from app.services.llm_provider import OpenAICompatibleClient

MAX_DEBATERS = 4


class DebateTeam:
    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("Debate mode requires at least one enabled provider")

        self.providers = providers
        self.debater_count = min(MAX_DEBATERS, max(2, len(providers)))

    async def stream(self, message: str) -> AsyncIterator[ExpertStreamEvent]:
        debaters = [
            ExpertRole(
                key=f"debater_{index + 1}",
                title=f"Debater {index + 1}",
                provider=self._provider_at(index),
            )
            for index in range(self.debater_count)
        ]

        initial_results: dict[str, RoleResult] = {}
        initial_runs = [(role, self._initial_answer_messages(message, role)) for role in debaters]
        async for event in self._stream_many(initial_runs, initial_results):
            yield event

        response_results: dict[str, RoleResult] = {}
        response_runs = [
            (
                ExpertRole(
                    key=f"{role.key}_response",
                    title=f"{role.title} Response",
                    provider=role.provider,
                ),
                self._debate_response_messages(message, role, debaters, initial_results),
            )
            for role in debaters
        ]
        async for event in self._stream_many(response_runs, response_results):
            yield event

        synthesizer_role = ExpertRole(
            key="synthesizer",
            title="Synthesizer",
            provider=self._provider_at(self.debater_count),
        )
        async for event in self._stream_role(
            synthesizer_role,
            self._synthesis_messages(message, debaters, initial_results, response_results),
        ):
            yield event

    def _provider_at(self, index: int) -> LLMProvider:
        return self.providers[index % len(self.providers)]

    async def _stream_many(
        self,
        runs: list[tuple[ExpertRole, list[dict[str, str]]]],
        results: dict[str, RoleResult],
    ) -> AsyncIterator[ExpertStreamEvent]:
        queue: asyncio.Queue[ExpertStreamEvent | None] = asyncio.Queue()

        async def worker(role: ExpertRole, messages: list[dict[str, str]]) -> None:
            content = ""
            reasoning = ""
            error_text: str | None = None
            try:
                async for event in self._stream_role(role, messages):
                    if event.event == "expert_delta":
                        content += event.content
                    elif event.event == "expert_reasoning_delta":
                        reasoning += event.reasoning
                    elif event.event == "expert_done":
                        content = event.content
                        reasoning = event.reasoning
                        error_text = event.error
                    await queue.put(event)
            finally:
                results[role.key] = RoleResult(content=content, reasoning=reasoning, error=error_text)
                await queue.put(None)

        tasks = [asyncio.create_task(worker(role, messages)) for role, messages in runs]
        completed = 0
        try:
            while completed < len(tasks):
                event = await queue.get()
                if event is None:
                    completed += 1
                    continue
                yield event
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
                    continue

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

    def _initial_answer_messages(self, message: str, role: ExpertRole) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    f"You are {role.title} in a multi-model debate. First round: independently answer the "
                    "user's question. Do not mention other debaters because you cannot see their answers yet. "
                    "Be direct, explicit about assumptions, and use the same language as the user."
                ),
            },
            {"role": "user", "content": message},
        ]

    def _debate_response_messages(
        self,
        message: str,
        role: ExpertRole,
        debaters: list[ExpertRole],
        initial_results: dict[str, RoleResult],
    ) -> list[dict[str, str]]:
        own_answer = initial_results.get(role.key, RoleResult(content="")).content
        other_answers = _format_peer_answers(
            [
                (debater, initial_results.get(debater.key, RoleResult(content="")))
                for debater in debaters
                if debater.key != role.key
            ]
        )
        return [
            {
                "role": "system",
                "content": (
                    f"You are {role.title} in the second round of a multi-model debate. Read the other "
                    "debaters' answers, then decide what to accept, what to challenge, and what to revise. "
                    "Use this exact Markdown structure: "
                    "## Agreement, ## Defense, ## Revision, ## Final Position. "
                    "Do not be contrarian for its own sake; defend your view only when there is a clear reason. "
                    "Use the same language as the user."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Your initial answer:\n{own_answer}\n\n"
                    f"Other debaters' initial answers:\n{other_answers}"
                ),
            },
        ]

    def _synthesis_messages(
        self,
        message: str,
        debaters: list[ExpertRole],
        initial_results: dict[str, RoleResult],
        response_results: dict[str, RoleResult],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the final synthesizer for a multi-model debate. Produce the final user-facing "
                    "answer. Weigh the initial answers and the agreement/defense/revision round. Do not simply "
                    "average the answers; resolve conflicts, identify the strongest reasoning, and surface "
                    "important uncertainty. Use the same language as the user."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Initial answers:\n{_format_peer_answers([(role, initial_results.get(role.key, RoleResult(content=''))) for role in debaters])}\n\n"
                    f"Debate responses:\n{_format_debate_responses(debaters, response_results)}"
                ),
            },
        ]


def _format_peer_answers(results: list[tuple[ExpertRole, RoleResult]]) -> str:
    if not results:
        return "No peer answers available."

    sections: list[str] = []
    for role, result in results:
        content = result.content.strip() or "(No answer returned.)"
        error = f"\nError: {result.error}" if result.error else ""
        sections.append(f"## {role.title}\nProvider: {role.provider.name} / {role.provider.model}\n{content}{error}")
    return "\n\n".join(sections)


def _format_debate_responses(debaters: list[ExpertRole], results: dict[str, RoleResult]) -> str:
    sections: list[str] = []
    for role in debaters:
        response_key = f"{role.key}_response"
        result = results.get(response_key, RoleResult(content=""))
        content = result.content.strip() or "(No response returned.)"
        error = f"\nError: {result.error}" if result.error else ""
        sections.append(f"## {role.title} Response\n{content}{error}")
    return "\n\n".join(sections)
