from dataclasses import dataclass, field
import json

from app.db.models import LLMProvider, ModelRouteConfig
from app.services.debate_team import MAX_DEBATERS
from app.services.expert_team import MAX_ADAPTIVE_EXPERTS


@dataclass(frozen=True)
class ExpertModelRoute:
    planner_provider: LLMProvider | None = None
    expert_providers: list[LLMProvider] = field(default_factory=list)
    reviewer_provider: LLMProvider | None = None
    synthesizer_provider: LLMProvider | None = None


@dataclass(frozen=True)
class DebateModelRoute:
    debater_providers: list[LLMProvider] = field(default_factory=list)
    synthesizer_provider: LLMProvider | None = None


@dataclass(frozen=True)
class ModelRoutes:
    enabled_providers: list[LLMProvider]
    normal_provider: LLMProvider | None
    expert: ExpertModelRoute
    debate: DebateModelRoute


def resolve_model_routes(
    enabled_providers: list[LLMProvider],
    config: ModelRouteConfig | None,
) -> ModelRoutes:
    enabled_by_id = {provider.id: provider for provider in enabled_providers}

    normal_provider = _pick_provider(
        config.normal_provider_id if config else None,
        enabled_by_id,
        enabled_providers[0] if enabled_providers else None,
    )
    expert_providers = _pick_provider_list(
        parse_provider_ids(config.expert_provider_ids) if config else [],
        enabled_by_id,
        enabled_providers,
        MAX_ADAPTIVE_EXPERTS,
    )
    debate_debater_providers = _pick_provider_list(
        parse_provider_ids(config.debate_debater_provider_ids) if config else [],
        enabled_by_id,
        enabled_providers,
        MAX_DEBATERS,
    )

    return ModelRoutes(
        enabled_providers=enabled_providers,
        normal_provider=normal_provider,
        expert=ExpertModelRoute(
            planner_provider=_pick_provider(
                config.expert_planner_provider_id if config else None,
                enabled_by_id,
                None,
            ),
            expert_providers=expert_providers,
            reviewer_provider=_pick_provider(
                config.expert_reviewer_provider_id if config else None,
                enabled_by_id,
                None,
            ),
            synthesizer_provider=_pick_provider(
                config.expert_synthesizer_provider_id if config else None,
                enabled_by_id,
                None,
            ),
        ),
        debate=DebateModelRoute(
            debater_providers=debate_debater_providers,
            synthesizer_provider=_pick_provider(
                config.debate_synthesizer_provider_id if config else None,
                enabled_by_id,
                None,
            ),
        ),
    )


def parse_provider_ids(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    return clean_provider_ids([item for item in parsed if isinstance(item, str)])


def dump_provider_ids(provider_ids: list[str]) -> str:
    return json.dumps(clean_provider_ids(provider_ids), ensure_ascii=False)


def clean_provider_ids(provider_ids: list[str], limit: int | None = None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for provider_id in provider_ids:
        provider_id = provider_id.strip()
        if not provider_id or provider_id in seen:
            continue
        cleaned.append(provider_id)
        seen.add(provider_id)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def clean_provider_id(provider_id: str | None) -> str | None:
    if provider_id is None:
        return None
    provider_id = provider_id.strip()
    return provider_id or None


def _pick_provider(
    provider_id: str | None,
    enabled_by_id: dict[str, LLMProvider],
    fallback: LLMProvider | None,
) -> LLMProvider | None:
    if provider_id and provider_id in enabled_by_id:
        return enabled_by_id[provider_id]
    return fallback


def _pick_provider_list(
    provider_ids: list[str],
    enabled_by_id: dict[str, LLMProvider],
    fallback: list[LLMProvider],
    limit: int,
) -> list[LLMProvider]:
    providers = [enabled_by_id[provider_id] for provider_id in provider_ids if provider_id in enabled_by_id]
    return providers[:limit] if providers else fallback[:limit]
