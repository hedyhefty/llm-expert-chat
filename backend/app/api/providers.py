from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.encryption import decrypt_secret, encrypt_secret
from app.db.models import LLMProvider, User
from app.db.session import get_db
from app.services.llm_provider import OpenAICompatibleClient

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


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ProviderRead]:
    providers = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.user_id == current_user.id)
        .order_by(LLMProvider.created_at.desc())
    ).all()
    return [_read_provider(provider) for provider in providers]


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
