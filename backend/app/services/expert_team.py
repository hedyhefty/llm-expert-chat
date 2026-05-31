from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Literal

import httpx

from app.core.encryption import decrypt_secret
from app.db.models import LLMProvider
from app.services.llm_provider import OpenAICompatibleClient

ExpertEventType = Literal["expert_start", "expert_delta", "expert_reasoning_delta", "expert_done"]
MAX_ADAPTIVE_EXPERTS = 4


@dataclass(frozen=True)
class ExpertRole:
    key: str
    title: str
    provider: LLMProvider


@dataclass(frozen=True)
class PlanExpert:
    title: str
    assignment: str
    focus: str


@dataclass(frozen=True)
class TeamPlan:
    task_type: str
    goal: str
    experts: list[PlanExpert]
    review_criteria: list[str]
    note: str = ""


@dataclass(frozen=True)
class RoleResult:
    content: str
    reasoning: str = ""
    error: str | None = None


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
    def __init__(
        self,
        providers: list[LLMProvider],
        planner_provider: LLMProvider | None = None,
        expert_providers: list[LLMProvider] | None = None,
        reviewer_provider: LLMProvider | None = None,
        synthesizer_provider: LLMProvider | None = None,
    ) -> None:
        if not providers and not expert_providers:
            raise ValueError("Expert mode requires at least one enabled provider")

        self.providers = providers or list(expert_providers or [])
        self.expert_providers = expert_providers or self.providers
        self.planner_provider = planner_provider
        self.reviewer_provider = reviewer_provider
        self.synthesizer_provider = synthesizer_provider
        self.max_experts = min(MAX_ADAPTIVE_EXPERTS, max(2, len(self.expert_providers)))

    async def stream(self, message: str) -> AsyncIterator[ExpertStreamEvent]:
        planner_role = ExpertRole(
            key="planner",
            title="Team Planner",
            provider=self.planner_provider or self._provider_at(0),
        )
        yield self._start_event(planner_role)
        planner_result = await self._run_role(planner_role, self._planner_messages(message))
        plan = _parse_plan(planner_result.content, max_experts=self.max_experts, message=message)
        if planner_result.error:
            plan = _fallback_plan(message, self.max_experts, note="Planner request failed; using fallback plan.")

        yield ExpertStreamEvent(
            event="expert_done",
            role=planner_role.key,
            title=planner_role.title,
            provider_name=planner_role.provider.name,
            model=planner_role.provider.model,
            content=_format_plan_summary(plan),
            reasoning=planner_result.reasoning,
            error=planner_result.error,
        )

        expert_results: list[tuple[PlanExpert, RoleResult]] = []
        for index, planned_expert in enumerate(plan.experts):
            role = ExpertRole(
                key=f"expert_{index + 1}",
                title=planned_expert.title or f"Expert {index + 1}",
                provider=self._expert_provider_at(index),
            )
            content = ""
            reasoning = ""
            error_text: str | None = None
            async for event in self._stream_role(
                role,
                self._expert_messages(message, plan, planned_expert, expert_results),
            ):
                if event.event == "expert_delta":
                    content += event.content
                elif event.event == "expert_reasoning_delta":
                    reasoning += event.reasoning
                elif event.event == "expert_done":
                    content = event.content
                    reasoning = event.reasoning
                    error_text = event.error
                yield event
            expert_results.append((planned_expert, RoleResult(content=content, reasoning=reasoning, error=error_text)))

        reviewer_role = ExpertRole(
            key="reviewer",
            title="Reviewer",
            provider=self.reviewer_provider or self._provider_at(len(plan.experts)),
        )
        reviewer_result = RoleResult(content="")
        async for event in self._stream_role(reviewer_role, self._reviewer_messages(message, plan, expert_results)):
            if event.event == "expert_done":
                reviewer_result = RoleResult(
                    content=event.content,
                    reasoning=event.reasoning,
                    error=event.error,
                )
            yield event

        synthesizer_role = ExpertRole(
            key="synthesizer",
            title="Synthesizer",
            provider=self.synthesizer_provider or self._provider_at(len(plan.experts) + 1),
        )
        async for event in self._stream_role(
            synthesizer_role,
            self._synthesizer_messages(message, plan, expert_results, reviewer_result),
        ):
            yield event

    def _provider_at(self, index: int) -> LLMProvider:
        return self.providers[index % len(self.providers)]

    def _expert_provider_at(self, index: int) -> LLMProvider:
        return self.expert_providers[index % len(self.expert_providers)]

    def _start_event(self, role: ExpertRole) -> ExpertStreamEvent:
        return ExpertStreamEvent(
            event="expert_start",
            role=role.key,
            title=role.title,
            provider_name=role.provider.name,
            model=role.provider.model,
        )

    async def _run_role(
        self,
        role: ExpertRole,
        messages: list[dict[str, str]],
    ) -> RoleResult:
        content = ""
        reasoning = ""
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
                    continue

                for part in splitter.feed(chunk.content):
                    if part.event == "reasoning":
                        reasoning += part.content
                    else:
                        content += part.content

            for part in splitter.flush():
                if part.event == "reasoning":
                    reasoning += part.content
                else:
                    content += part.content
        except httpx.HTTPError as error:
            return RoleResult(content="", reasoning=reasoning, error=str(error))

        return RoleResult(content=content, reasoning=reasoning)

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

    def _planner_messages(self, message: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the coordinator of an adaptive expert team. Decide how to decompose the user's "
                    "request into focused expert assignments. Return only a valid JSON object. Do not use "
                    "Markdown fences or explanatory prose. Use the same language as the user for titles and "
                    "assignments when practical. The JSON schema is: "
                    "{"
                    '"task_type": "short label", '
                    '"goal": "team goal", '
                    '"experts": ['
                    '{"title": "concise expert title", "assignment": "what this expert should do", '
                    '"focus": "specific angle or deliverable"}'
                    "], "
                    '"review_criteria": ["criterion"]'
                    "}. "
                    f"Choose between 2 and {self.max_experts} experts. Do not include reviewer or synthesizer "
                    "as experts; they will be added automatically."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Max experts available: {self.max_experts}\n\n"
                    f"User request:\n{message}"
                ),
            },
        ]

    def _expert_messages(
        self,
        message: str,
        plan: TeamPlan,
        planned_expert: PlanExpert,
        prior_results: list[tuple[PlanExpert, RoleResult]],
    ) -> list[dict[str, str]]:
        prior_text = _format_prior_results(prior_results)
        return [
            {
                "role": "system",
                "content": (
                    f"You are {planned_expert.title} in an adaptive expert team. Work only on your assigned "
                    "slice, but coordinate with prior expert contributions when they exist. Produce a useful "
                    "expert contribution for the reviewer and synthesizer, not the final user-facing answer. "
                    "Be concrete, avoid unnecessary repetition, and use the same language as the user."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Team goal:\n{plan.goal}\n\n"
                    f"Your assignment:\n{planned_expert.assignment}\n\n"
                    f"Your focus:\n{planned_expert.focus}\n\n"
                    f"Full team plan:\n{_format_plan_for_prompt(plan)}\n\n"
                    f"Prior expert contributions:\n{prior_text}"
                ),
            },
        ]

    def _reviewer_messages(
        self,
        message: str,
        plan: TeamPlan,
        expert_results: list[tuple[PlanExpert, RoleResult]],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the reviewer for an adaptive expert team. Audit the expert contributions against "
                    "the original user request and the review criteria. Find gaps, contradictions, overclaims, "
                    "missing caveats, and formatting issues. Do not write the final answer; write review notes "
                    "that help the synthesizer produce the final answer. Use the same language as the user."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Team plan:\n{_format_plan_for_prompt(plan)}\n\n"
                    f"Review criteria:\n{_format_list(plan.review_criteria)}\n\n"
                    f"Expert contributions:\n{_format_expert_results(expert_results)}"
                ),
            },
        ]

    def _synthesizer_messages(
        self,
        message: str,
        plan: TeamPlan,
        expert_results: list[tuple[PlanExpert, RoleResult]],
        reviewer_result: RoleResult,
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the synthesizer for an adaptive expert team. Produce the final user-facing answer "
                    "by combining the strongest expert contributions and the review notes. Resolve conflicts, "
                    "remove internal duplication, and do not expose the team's internal process unless the user "
                    "explicitly asked for it or it directly improves clarity. Use the same language as the user."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{message}\n\n"
                    f"Team plan:\n{_format_plan_for_prompt(plan)}\n\n"
                    f"Expert contributions:\n{_format_expert_results(expert_results)}\n\n"
                    f"Reviewer notes:\n{reviewer_result.content}"
                ),
            },
        ]


def _parse_plan(raw_content: str, max_experts: int, message: str) -> TeamPlan:
    if not raw_content.strip():
        return _fallback_plan(message, max_experts, note="Planner returned an empty plan; using fallback plan.")

    try:
        payload = _extract_json_object(raw_content)
    except ValueError:
        return _fallback_plan(message, max_experts, note="Planner returned non-JSON output; using fallback plan.")

    task_type = _clean_text(payload.get("task_type"), fallback="adaptive")
    goal = _clean_text(payload.get("goal"), fallback="Answer the user's request thoroughly and clearly.")
    criteria = _string_list(payload.get("review_criteria")) or [
        "Completeness",
        "Accuracy",
        "Usefulness",
        "Clarity",
    ]

    experts: list[PlanExpert] = []
    raw_experts = payload.get("experts")
    if isinstance(raw_experts, list):
        for index, item in enumerate(raw_experts[:max_experts]):
            if not isinstance(item, dict):
                continue

            title = _clean_text(item.get("title"), fallback=f"Expert {index + 1}")
            assignment = _clean_text(item.get("assignment"), fallback="")
            focus = _clean_text(item.get("focus"), fallback=assignment)
            if assignment or focus:
                experts.append(
                    PlanExpert(
                        title=title,
                        assignment=assignment or focus,
                        focus=focus or assignment,
                    )
                )

    min_experts = min(2, max_experts)
    if len(experts) < min_experts:
        fallback = _fallback_plan(message, max_experts)
        experts.extend(fallback.experts[len(experts) : min_experts])

    return TeamPlan(
        task_type=task_type,
        goal=goal,
        experts=experts[:max_experts],
        review_criteria=criteria[:8],
    )


def _extract_json_object(raw_content: str) -> dict[str, object]:
    content = raw_content.strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")

    parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Planner output must be a JSON object")
    return parsed


def _fallback_plan(message: str, max_experts: int, note: str = "") -> TeamPlan:
    fallback_experts = [
        PlanExpert(
            title="Primary Expert",
            assignment="Develop the main answer and cover the user's core request.",
            focus="Completeness and direct usefulness.",
        ),
        PlanExpert(
            title="Critical Expert",
            assignment="Identify gaps, risks, edge cases, and assumptions in the main approach.",
            focus="Accuracy, caveats, and missed details.",
        ),
        PlanExpert(
            title="Implementation Expert",
            assignment="Translate the answer into concrete steps, examples, or actionable details where useful.",
            focus="Practical execution.",
        ),
        PlanExpert(
            title="Clarity Expert",
            assignment="Improve structure, wording, and final readability for the target user.",
            focus="Communication quality.",
        ),
    ]
    goal = "Answer the user's request thoroughly and clearly."
    if message.strip():
        goal = f"Answer this request thoroughly and clearly: {message.strip()[:220]}"

    return TeamPlan(
        task_type="adaptive",
        goal=goal,
        experts=fallback_experts[:max_experts],
        review_criteria=["Completeness", "Accuracy", "Usefulness", "Clarity"],
        note=note,
    )


def _clean_text(value: object, fallback: str) -> str:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if cleaned:
            return cleaned[:500]
    return fallback


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = " ".join(item.split())
            if cleaned:
                result.append(cleaned[:240])
    return result


def _format_plan_summary(plan: TeamPlan) -> str:
    lines = ["### Team plan"]
    if plan.note:
        lines.append(f"- Note: {plan.note}")
    lines.extend(
        [
            f"- Task type: {plan.task_type}",
            f"- Goal: {plan.goal}",
            "- Experts:",
        ]
    )
    lines.extend(
        f"  - **{expert.title}**: {expert.assignment} Focus: {expert.focus}"
        for expert in plan.experts
    )
    lines.append("- Review criteria:")
    lines.extend(f"  - {criterion}" for criterion in plan.review_criteria)
    return "\n".join(lines)


def _format_plan_for_prompt(plan: TeamPlan) -> str:
    return (
        f"Task type: {plan.task_type}\n"
        f"Goal: {plan.goal}\n"
        f"Experts:\n{_format_plan_experts(plan.experts)}\n"
        f"Review criteria:\n{_format_list(plan.review_criteria)}"
    )


def _format_plan_experts(experts: list[PlanExpert]) -> str:
    return "\n".join(
        f"- {expert.title}: {expert.assignment} Focus: {expert.focus}" for expert in experts
    )


def _format_prior_results(results: list[tuple[PlanExpert, RoleResult]]) -> str:
    if not results:
        return "None yet."
    return _format_expert_results(results)


def _format_expert_results(results: list[tuple[PlanExpert, RoleResult]]) -> str:
    if not results:
        return "No expert contributions."

    sections: list[str] = []
    for expert, result in results:
        error = f"\nError: {result.error}" if result.error else ""
        content = result.content.strip() or "(No content returned.)"
        sections.append(
            f"## {expert.title}\n"
            f"Assignment: {expert.assignment}\n"
            f"Focus: {expert.focus}\n"
            f"Contribution:\n{content}{error}"
        )
    return "\n\n".join(sections)


def _format_list(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


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
