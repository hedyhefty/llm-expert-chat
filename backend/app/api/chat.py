from typing import Annotated
from datetime import datetime
import json

from fastapi import APIRouter, HTTPException, status
from fastapi import Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
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


@router.post("/stream")
async def stream_chat(
    request: ChatStreamRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    conversation = _get_or_create_conversation(db, current_user, request)
    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=request.message,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="",
    )
    conversation.updated_at = datetime.utcnow()
    if conversation.title == "New chat":
        conversation.title = _conversation_title(request.message)
    db.add(user_message)
    db.add(assistant_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)

    providers = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.user_id == current_user.id, LLMProvider.enabled == 1)
        .order_by(LLMProvider.created_at.desc())
    ).all()
    route_config = db.scalar(select(ModelRouteConfig).where(ModelRouteConfig.user_id == current_user.id))
    routes = resolve_model_routes(list(providers), route_config)
    orchestrator = ChatOrchestrator()
    stream = orchestrator.stream_reply(
        message=request.message,
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


async def _persisting_stream(
    stream,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    mode: ChatMode,
):
    assistant_content = ""
    expert_outputs: dict[str, dict[str, object]] = {}

    try:
        async for chunk in stream:
            event_type, data = _parse_sse(chunk)
            if data != "[DONE]":
                if event_type == "message":
                    assistant_content += data
                elif event_type.startswith("expert_"):
                    _capture_expert_event(expert_outputs, data)
            yield chunk
    finally:
        _save_stream_result(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            assistant_content=assistant_content,
            expert_outputs=expert_outputs,
            mode=mode,
        )


def _save_stream_result(
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    assistant_content: str,
    expert_outputs: dict[str, dict[str, object]],
    mode: ChatMode,
) -> None:
    db = SessionLocal()
    try:
        assistant_message = db.get(Message, assistant_message_id)
        conversation = db.get(Conversation, conversation_id)
        if assistant_message is None or conversation is None:
            return

        assistant_message.content = assistant_content
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
        for output in expert_outputs.values():
            db.add(
                ExpertOutput(
                    team_run_id=team_run.id,
                    role_name=str(output.get("role") or "expert"),
                    provider_id=None,
                    content=json.dumps(output, ensure_ascii=False),
                )
            )


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


def _conversation_title(message: str) -> str:
    title = " ".join(message.strip().split())
    return title[:60] if title else "New chat"
