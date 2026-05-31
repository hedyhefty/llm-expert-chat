from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.models import LLMProvider, ModelRouteConfig, User
from app.db.session import get_db
from app.services.llm_provider import OpenAICompatibleClient
from app.services.model_routing import (
    clean_provider_id,
    clean_provider_ids,
    dump_provider_ids,
    parse_provider_ids,
    resolve_model_routes,
)

router = APIRouter()


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=4000)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4000)
    enabled: bool | None = None


class ProviderRead(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    enabled: bool
    created_at: datetime


class ProviderTestResponse(BaseModel):
    ok: bool
    message: str


class ProviderRef(BaseModel):
    id: str
    name: str
    model: str
    enabled: bool


class ModelRoutePayload(BaseModel):
    normal_provider_id: str | None = None
    expert_planner_provider_id: str | None = None
    expert_provider_ids: list[str] = Field(default_factory=list)
    expert_reviewer_provider_id: str | None = None
    expert_synthesizer_provider_id: str | None = None
    debate_debater_provider_ids: list[str] = Field(default_factory=list)
    debate_synthesizer_provider_id: str | None = None


class ModelRouteEffectiveRead(BaseModel):
    normal: ProviderRef | None
    expert_planner: ProviderRef | None
    expert_providers: list[ProviderRef]
    expert_reviewer: ProviderRef | None
    expert_synthesizer: ProviderRef | None
    debate_debaters: list[ProviderRef]
    debate_synthesizer: ProviderRef | None


class ModelRouteRead(ModelRoutePayload):
    effective: ModelRouteEffectiveRead


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _get_owned_provider(provider_id: str, user: User, db: Session) -> LLMProvider:
    provider = db.get(LLMProvider, provider_id)
    if provider is None or provider.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider


def _read_provider(provider: LLMProvider) -> ProviderRead:
    return ProviderRead(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        model=provider.model,
        enabled=bool(provider.enabled),
        created_at=provider.created_at,
    )


def _provider_ref(provider: LLMProvider | None) -> ProviderRef | None:
    if provider is None:
        return None
    return ProviderRef(
        id=provider.id,
        name=provider.name,
        model=provider.model,
        enabled=bool(provider.enabled),
    )


def _provider_refs(providers: list[LLMProvider]) -> list[ProviderRef]:
    return [ref for provider in providers if (ref := _provider_ref(provider)) is not None]


def _read_route_config(
    config: ModelRouteConfig | None,
    providers: list[LLMProvider],
) -> ModelRouteRead:
    enabled_providers = [provider for provider in providers if provider.enabled == 1]
    routes = resolve_model_routes(enabled_providers, config)
    return ModelRouteRead(
        normal_provider_id=config.normal_provider_id if config else None,
        expert_planner_provider_id=config.expert_planner_provider_id if config else None,
        expert_provider_ids=parse_provider_ids(config.expert_provider_ids) if config else [],
        expert_reviewer_provider_id=config.expert_reviewer_provider_id if config else None,
        expert_synthesizer_provider_id=config.expert_synthesizer_provider_id if config else None,
        debate_debater_provider_ids=parse_provider_ids(config.debate_debater_provider_ids) if config else [],
        debate_synthesizer_provider_id=config.debate_synthesizer_provider_id if config else None,
        effective=ModelRouteEffectiveRead(
            normal=_provider_ref(routes.normal_provider),
            expert_planner=_provider_ref(routes.expert.planner_provider),
            expert_providers=_provider_refs(routes.expert.expert_providers),
            expert_reviewer=_provider_ref(routes.expert.reviewer_provider),
            expert_synthesizer=_provider_ref(routes.expert.synthesizer_provider),
            debate_debaters=_provider_refs(routes.debate.debater_providers),
            debate_synthesizer=_provider_ref(routes.debate.synthesizer_provider),
        ),
    )


def _get_route_config(user: User, db: Session) -> ModelRouteConfig | None:
    return db.scalar(select(ModelRouteConfig).where(ModelRouteConfig.user_id == user.id))


def _get_or_create_route_config(user: User, db: Session) -> ModelRouteConfig:
    config = _get_route_config(user, db)
    if config is not None:
        return config

    config = ModelRouteConfig(
        user_id=user.id,
        expert_provider_ids="[]",
        debate_debater_provider_ids="[]",
    )
    db.add(config)
    db.flush()
    return config


def _list_owned_providers(user: User, db: Session) -> list[LLMProvider]:
    return db.scalars(
        select(LLMProvider)
        .where(LLMProvider.user_id == user.id)
        .order_by(LLMProvider.created_at.desc())
    ).all()


def _validate_provider_ids(
    provider_ids: list[str],
    providers: list[LLMProvider],
) -> None:
    owned_provider_ids = {provider.id for provider in providers}
    invalid_provider_ids = sorted(set(provider_ids) - owned_provider_ids)
    if invalid_provider_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Routing contains unknown providers",
        )


def _remove_provider_from_route_config(provider_id: str, user: User, db: Session) -> None:
    config = _get_route_config(user, db)
    if config is None:
        return

    for field_name in (
        "normal_provider_id",
        "expert_planner_provider_id",
        "expert_reviewer_provider_id",
        "expert_synthesizer_provider_id",
        "debate_synthesizer_provider_id",
    ):
        if getattr(config, field_name) == provider_id:
            setattr(config, field_name, None)

    config.expert_provider_ids = dump_provider_ids(
        [item for item in parse_provider_ids(config.expert_provider_ids) if item != provider_id]
    )
    config.debate_debater_provider_ids = dump_provider_ids(
        [item for item in parse_provider_ids(config.debate_debater_provider_ids) if item != provider_id]
    )


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ProviderRead]:
    providers = _list_owned_providers(current_user, db)
    return [_read_provider(provider) for provider in providers]


@router.get("/routing", response_model=ModelRouteRead)
async def get_model_routing(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelRouteRead:
    return _read_route_config(_get_route_config(current_user, db), _list_owned_providers(current_user, db))


@router.put("/routing", response_model=ModelRouteRead)
async def update_model_routing(
    request: ModelRoutePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ModelRouteRead:
    providers = _list_owned_providers(current_user, db)
    scalar_provider_ids = clean_provider_ids(
        [
            provider_id
            for provider_id in [
                request.normal_provider_id,
                request.expert_planner_provider_id,
                request.expert_reviewer_provider_id,
                request.expert_synthesizer_provider_id,
                request.debate_synthesizer_provider_id,
            ]
            if provider_id is not None
        ]
    )
    expert_provider_ids = clean_provider_ids(request.expert_provider_ids, limit=4)
    debate_debater_provider_ids = clean_provider_ids(request.debate_debater_provider_ids, limit=4)
    _validate_provider_ids(
        scalar_provider_ids + expert_provider_ids + debate_debater_provider_ids,
        providers,
    )

    config = _get_or_create_route_config(current_user, db)
    config.normal_provider_id = clean_provider_id(request.normal_provider_id)
    config.expert_planner_provider_id = clean_provider_id(request.expert_planner_provider_id)
    config.expert_provider_ids = dump_provider_ids(expert_provider_ids)
    config.expert_reviewer_provider_id = clean_provider_id(request.expert_reviewer_provider_id)
    config.expert_synthesizer_provider_id = clean_provider_id(request.expert_synthesizer_provider_id)
    config.debate_debater_provider_ids = dump_provider_ids(debate_debater_provider_ids)
    config.debate_synthesizer_provider_id = clean_provider_id(request.debate_synthesizer_provider_id)

    db.commit()
    db.refresh(config)
    return _read_route_config(config, providers)


@router.post("", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    request: ProviderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderRead:
    provider = LLMProvider(
        user_id=current_user.id,
        name=request.name.strip(),
        base_url=_normalize_base_url(request.base_url),
        model=request.model.strip(),
        encrypted_api_key=encrypt_secret(request.api_key.strip()),
        enabled=1 if request.enabled else 0,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return _read_provider(provider)


@router.patch("/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: str,
    request: ProviderUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderRead:
    provider = _get_owned_provider(provider_id, current_user, db)

    if request.name is not None:
        provider.name = request.name.strip()
    if request.base_url is not None:
        provider.base_url = _normalize_base_url(request.base_url)
    if request.model is not None:
        provider.model = request.model.strip()
    if request.api_key is not None:
        provider.encrypted_api_key = encrypt_secret(request.api_key.strip())
    if request.enabled is not None:
        provider.enabled = 1 if request.enabled else 0

    db.commit()
    db.refresh(provider)
    return _read_provider(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    provider = _get_owned_provider(provider_id, current_user, db)
    _remove_provider_from_route_config(provider.id, current_user, db)
    db.delete(provider)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(
    provider_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderTestResponse:
    provider = _get_owned_provider(provider_id, current_user, db)
    client = OpenAICompatibleClient(
        base_url=provider.base_url,
        api_key=decrypt_secret(provider.encrypted_api_key),
        model=provider.model,
    )

    try:
        ok, message = await client.test_connection()
    except httpx.HTTPError as error:
        return ProviderTestResponse(ok=False, message=str(error))

    return ProviderTestResponse(ok=ok, message=message)
