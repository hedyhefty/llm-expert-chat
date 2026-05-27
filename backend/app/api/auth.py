from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_db

router = APIRouter()


class AuthRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address")
        return normalized


class UserRead(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(request: AuthRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    email = request.email.lower()
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(request.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    return AuthResponse(access_token=create_access_token(user.id), user=UserRead.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(request: AuthRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    email = request.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return AuthResponse(access_token=create_access_token(user.id), user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead.model_validate(current_user)
