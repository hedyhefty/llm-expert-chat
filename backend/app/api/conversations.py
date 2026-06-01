from datetime import datetime
import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Conversation, ExpertOutput, Message, MessageRole, TeamRun, User
from app.db.session import get_db

router = APIRouter()


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", max_length=255)


class ConversationRead(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExpertOutputRead(BaseModel):
    role: str
    title: str
    provider_name: str
    model: str
    content: str
    reasoning: str = ""
    done: bool = True
    error: str | None = None


class ConversationMessageRead(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    mode: str | None = None
    reasoning: str = ""
    experts: list[ExpertOutputRead] = Field(default_factory=list)


class ConversationDetailRead(ConversationRead):
    messages: list[ConversationMessageRead]


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == current_user.id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        )
    )


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: ConversationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Conversation:
    title = _clean_title(request.title)
    conversation = Conversation(user_id=current_user.id, title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetailRead)
async def get_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationDetailRead:
    conversation = _get_user_conversation(db, current_user, conversation_id)
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(
                Message.created_at.asc(),
                case((Message.role == MessageRole.USER, 0), else_=1).asc(),
            )
        )
    )
    team_runs = list(
        db.scalars(
            select(TeamRun)
            .where(TeamRun.conversation_id == conversation.id)
            .order_by(TeamRun.created_at.asc())
        )
    )
    run_by_assistant_id = {
        run.assistant_message_id: run for run in team_runs if run.assistant_message_id is not None
    }
    outputs_by_run_id = _load_outputs_by_run_id(db, team_runs)

    return ConversationDetailRead(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            _message_read(message, run_by_assistant_id.get(message.id), outputs_by_run_id)
            for message in messages
            if message.role.value in {"user", "assistant"}
        ],
    )


def _get_user_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _load_outputs_by_run_id(
    db: Session,
    team_runs: list[TeamRun],
) -> dict[str, list[ExpertOutput]]:
    run_ids = [run.id for run in team_runs]
    if not run_ids:
        return {}

    outputs = list(
        db.scalars(
            select(ExpertOutput)
            .where(ExpertOutput.team_run_id.in_(run_ids))
            .order_by(ExpertOutput.created_at.asc())
        )
    )
    result: dict[str, list[ExpertOutput]] = {}
    for output in outputs:
        result.setdefault(output.team_run_id, []).append(output)
    return result


def _message_read(
    message: Message,
    team_run: TeamRun | None,
    outputs_by_run_id: dict[str, list[ExpertOutput]],
) -> ConversationMessageRead:
    experts: list[ExpertOutputRead] = []
    if team_run is not None:
        experts = _sort_experts(
            [_expert_output_read(output) for output in outputs_by_run_id.get(team_run.id, [])],
            team_run.mode,
        )

    synthesizer = next((expert for expert in experts if expert.role == "synthesizer"), None)
    return ConversationMessageRead(
        id=message.id,
        role=message.role.value,  # type: ignore[arg-type]
        content=message.content,
        created_at=message.created_at,
        mode=team_run.mode if team_run is not None else None,
        reasoning=synthesizer.reasoning if synthesizer is not None else "",
        experts=experts,
    )


def _expert_output_read(output: ExpertOutput) -> ExpertOutputRead:
    payload = _json_dict(output.content)
    role = _string_value(payload.get("role"), output.role_name)
    title = _string_value(payload.get("title"), role)
    provider_name = _string_value(payload.get("provider_name"), "Unknown provider")
    model = _string_value(payload.get("model"), "Unknown model")
    content = _string_value(payload.get("content"), output.content)
    reasoning = _string_value(payload.get("reasoning"), "")
    error = payload.get("error")
    return ExpertOutputRead(
        role=role,
        title=title,
        provider_name=provider_name,
        model=model,
        content=content,
        reasoning=reasoning,
        done=True,
        error=error if isinstance(error, str) else None,
    )


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_value(value: object, fallback: str) -> str:
    return value if isinstance(value, str) else fallback


def _sort_experts(experts: list[ExpertOutputRead], mode: str | None) -> list[ExpertOutputRead]:
    if mode == "debate":
        return sorted(experts, key=lambda expert: _debate_role_sort_key(expert.role))
    return sorted(experts, key=lambda expert: _collaboration_role_sort_key(expert.role))


def _debate_role_sort_key(role: str) -> tuple[int, int, str]:
    if match := re.fullmatch(r"debater_(\d+)", role):
        return (0, int(match.group(1)), role)
    if match := re.fullmatch(r"debater_(\d+)_response", role):
        return (1, int(match.group(1)), role)
    if role == "synthesizer":
        return (2, 0, role)
    return (99, 0, role)


def _collaboration_role_sort_key(role: str) -> tuple[int, int, str]:
    if role == "planner":
        return (0, 0, role)
    if match := re.fullmatch(r"expert_(\d+)", role):
        return (1, int(match.group(1)), role)
    if role == "reviewer":
        return (2, 0, role)
    if role == "synthesizer":
        return (3, 0, role)
    return (99, 0, role)


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.strip().split())
    return cleaned[:255] if cleaned else "New chat"
