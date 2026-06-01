from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(SQLEnum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(200))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelRouteConfig(Base):
    __tablename__ = "model_route_configs"
    __table_args__ = (UniqueConstraint("user_id", name="uq_model_route_configs_user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    normal_provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expert_planner_provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expert_provider_ids: Mapped[str] = mapped_column(Text, default="[]")
    expert_reviewer_provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expert_synthesizer_provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    debate_debater_provider_ids: Mapped[str] = mapped_column(Text, default="[]")
    debate_synthesizer_provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class TeamRun(Base):
    __tablename__ = "team_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    assistant_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(50), default="expert")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExpertOutput(Base):
    __tablename__ = "expert_outputs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    team_run_id: Mapped[str] = mapped_column(ForeignKey("team_runs.id"), index=True)
    role_name: Mapped[str] = mapped_column(String(80))
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("llm_providers.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("llm_providers.id"), nullable=True)
    model: Mapped[str] = mapped_column(String(200))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
