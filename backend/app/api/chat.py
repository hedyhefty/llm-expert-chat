from typing import Annotated
from datetime import datetime
import json
import re

from fastapi import APIRouter, HTTPException, status
from fastapi import Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Conversation, ExpertOutput, LLMProvider, Message, MessageRole, ModelRouteConfig, TeamRun, User
from app.db.session import SessionLocal, get_db
from app.services.orchestrator import ChatMode, ChatOrchestrator
from app.services.model_routing import resolve_model_routes

router = APIRouter()
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatStreamRequest(BaseModel):
    message: str
    mode: ChatMode = ChatMode.NORMAL
    conversation_id: str | None = None
    replace_assistant_message_id: str | None = None
    source_user_message_id: str | None = None


@router.post("/stream")
async def stream_chat(
    request: ChatStreamRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    conversation = _get_or_create_conversation(db, current_user, request)
    user_message, assistant_message = _prepare_stream_messages(db, current_user, conversation, request)

    providers = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.user_id == current_user.id, LLMProvider.enabled == 1)
        .order_by(LLMProvider.created_at.desc())
    ).all()
    route_config = db.scalar(select(ModelRouteConfig).where(ModelRouteConfig.user_id == current_user.id))
    routes = resolve_model_routes(list(providers), route_config)
    orchestrator = ChatOrchestrator()
    stream = orchestrator.stream_reply(
        message=user_message.content,
        mode=request.mode,
        provider=routes.normal_provider,
        providers=routes.enabled_providers,
        routes=routes,
    )
    persistent_stream = _persisting_stream(
        stream=stream,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        mode=request.mode,
    )
    return StreamingResponse(persistent_stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _get_or_create_conversation(
    db: Session,
    current_user: User,
    request: ChatStreamRequest,
) -> Conversation:
    if request.conversation_id:
        conversation = db.get(Conversation, request.conversation_id)
        if conversation is None or conversation.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return conversation

    conversation = Conversation(
        user_id=current_user.id,
        title=_conversation_title(request.message),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _prepare_stream_messages(
    db: Session,
    current_user: User,
    conversation: Conversation,
    request: ChatStreamRequest,
) -> tuple[Message, Message]:
    if request.replace_assistant_message_id:
        return _prepare_regeneration_messages(db, current_user, conversation, request)
    return _create_stream_messages(db, conversation, request)


def _create_stream_messages(
    db: Session,
    conversation: Conversation,
    request: ChatStreamRequest,
) -> tuple[Message, Message]:
    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=request.message,
        status="completed",
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="",
        status="generating",
    )
    conversation.updated_at = datetime.utcnow()
    if conversation.title == "New chat":
        conversation.title = _conversation_title(request.message)
    db.add(user_message)
    db.add(assistant_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message


def _prepare_regeneration_messages(
    db: Session,
    current_user: User,
    conversation: Conversation,
    request: ChatStreamRequest,
) -> tuple[Message, Message]:
    assistant_message = db.get(Message, request.replace_assistant_message_id)
    if (
        assistant_message is None
        or assistant_message.conversation_id != conversation.id
        or assistant_message.role != MessageRole.ASSISTANT
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant message not found")

    user_message = _get_regeneration_user_message(db, conversation, assistant_message, request)
    if user_message.conversation_id != conversation.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source message mismatch")

    _delete_assistant_team_runs(db, assistant_message.id)
    assistant_message.content = ""
    assistant_message.status = "generating"
    conversation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message


def _get_regeneration_user_message(
    db: Session,
    conversation: Conversation,
    assistant_message: Message,
    request: ChatStreamRequest,
) -> Message:
    if request.source_user_message_id:
        user_message = db.get(Message, request.source_user_message_id)
        if (
            user_message is None
            or user_message.conversation_id != conversation.id
            or user_message.role != MessageRole.USER
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source user message not found")
        return user_message

    team_run = db.scalar(select(TeamRun).where(TeamRun.assistant_message_id == assistant_message.id))
    if team_run is not None:
        user_message = db.get(Message, team_run.user_message_id)
        if user_message is not None and user_message.role == MessageRole.USER:
            return user_message

    user_message = db.scalar(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.role == MessageRole.USER,
            Message.created_at <= assistant_message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if user_message is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No source user message found")
    return user_message


def _delete_assistant_team_runs(db: Session, assistant_message_id: str) -> None:
    team_runs = list(
        db.scalars(select(TeamRun).where(TeamRun.assistant_message_id == assistant_message_id))
    )
    team_run_ids = [team_run.id for team_run in team_runs]
    if not team_run_ids:
        return

    db.execute(delete(ExpertOutput).where(ExpertOutput.team_run_id.in_(team_run_ids)))
    db.execute(delete(TeamRun).where(TeamRun.id.in_(team_run_ids)))


async def _persisting_stream(
    stream,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    mode: ChatMode,
):
    assistant_content = ""
    expert_outputs: dict[str, dict[str, object]] = {}
    completed = False
    failed = False

    try:
        yield _to_json_sse(
            "meta",
            {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "mode": mode.value,
            },
        )
        async for chunk in stream:
            event_type, data = _parse_sse(chunk)
            if event_type == "done" and data == "[DONE]":
                completed = True
            elif data != "[DONE]":
                if event_type == "message":
                    assistant_content += data
                elif event_type.startswith("expert_"):
                    _capture_expert_event(expert_outputs, data)
            yield chunk
    except Exception:
        failed = True
        raise
    finally:
        _save_stream_result(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            assistant_content=assistant_content,
            expert_outputs=expert_outputs,
            mode=mode,
            result_status=_stream_result_status(completed, failed),
        )


def _save_stream_result(
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    assistant_content: str,
    expert_outputs: dict[str, dict[str, object]],
    mode: ChatMode,
    result_status: str,
) -> None:
    db = SessionLocal()
    try:
        assistant_message = db.get(Message, assistant_message_id)
        conversation = db.get(Conversation, conversation_id)
        if assistant_message is None or conversation is None:
            return

        assistant_message.content = assistant_content
        assistant_message.status = result_status
        conversation.updated_at = datetime.utcnow()

        existing_team_run = db.scalar(
            select(TeamRun).where(TeamRun.assistant_message_id == assistant_message_id)
        )
        if existing_team_run is None:
            _save_team_outputs(
                db=db,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                mode=mode,
                expert_outputs=expert_outputs,
            )
        db.commit()
    finally:
        db.close()


def _save_team_outputs(
    db: Session,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    mode: ChatMode,
    expert_outputs: dict[str, dict[str, object]],
) -> None:
    if mode in {ChatMode.EXPERT, ChatMode.DEBATE} and expert_outputs:
        team_run = TeamRun(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            mode=mode.value,
        )
        db.add(team_run)
        db.flush()
        for output in _ordered_expert_outputs(expert_outputs, mode):
            db.add(
                ExpertOutput(
                    team_run_id=team_run.id,
                    role_name=str(output.get("role") or "expert"),
                    provider_id=None,
                    content=json.dumps(output, ensure_ascii=False),
                )
            )


def _stream_result_status(completed: bool, failed: bool) -> str:
    if completed:
        return "completed"
    if failed:
        return "failed"
    return "interrupted"


def _to_sse(data: str, event: str | None = None) -> str:
    lines = data.splitlines() or [""]
    event_line = f"event: {event}\n" if event else ""
    return event_line + "".join(f"data: {line}\n" for line in lines) + "\n"


def _to_json_sse(event: str, data: dict[str, object]) -> str:
    return _to_sse(json.dumps(data, ensure_ascii=False), event=event)


def _parse_sse(chunk: str) -> tuple[str, str]:
    event_type = "message"
    data_lines: list[str] = []
    for line in chunk.splitlines():
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
    return event_type, "\n".join(data_lines)


def _capture_expert_event(expert_outputs: dict[str, dict[str, object]], data: str) -> None:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    role = payload.get("role")
    if not isinstance(role, str):
        return

    output = expert_outputs.setdefault(
        role,
        {
            "role": role,
            "title": payload.get("title") if isinstance(payload.get("title"), str) else role,
            "provider_name": payload.get("provider_name") if isinstance(payload.get("provider_name"), str) else "",
            "model": payload.get("model") if isinstance(payload.get("model"), str) else "",
            "content": "",
            "reasoning": "",
            "error": None,
            "done": False,
        },
    )
    for key in ("title", "provider_name", "model"):
        if isinstance(payload.get(key), str):
            output[key] = payload[key]

    event = payload.get("event")
    if event == "expert_delta" and isinstance(payload.get("content"), str):
        output["content"] = f"{output.get('content', '')}{payload['content']}"
    elif event == "expert_reasoning_delta" and isinstance(payload.get("reasoning"), str):
        output["reasoning"] = f"{output.get('reasoning', '')}{payload['reasoning']}"
    elif event == "expert_done":
        if isinstance(payload.get("content"), str):
            output["content"] = payload["content"]
        if isinstance(payload.get("reasoning"), str):
            output["reasoning"] = payload["reasoning"]
        output["error"] = payload.get("error") if isinstance(payload.get("error"), str) else None
        output["done"] = True


def _ordered_expert_outputs(
    expert_outputs: dict[str, dict[str, object]],
    mode: ChatMode,
) -> list[dict[str, object]]:
    return sorted(
        expert_outputs.values(),
        key=lambda output: _role_sort_key(str(output.get("role") or ""), mode),
    )


def _role_sort_key(role: str, mode: ChatMode) -> tuple[int, int, str]:
    if mode == ChatMode.DEBATE:
        if match := re.fullmatch(r"debater_(\d+)", role):
            return (0, int(match.group(1)), role)
        if match := re.fullmatch(r"debater_(\d+)_response", role):
            return (1, int(match.group(1)), role)
        if role == "synthesizer":
            return (2, 0, role)
        return (99, 0, role)

    if role == "planner":
        return (0, 0, role)
    if match := re.fullmatch(r"expert_(\d+)", role):
        return (1, int(match.group(1)), role)
    if role == "reviewer":
        return (2, 0, role)
    if role == "synthesizer":
        return (3, 0, role)
    return (99, 0, role)


def _conversation_title(message: str) -> str:
    title = " ".join(message.strip().split())
    return title[:60] if title else "New chat"
