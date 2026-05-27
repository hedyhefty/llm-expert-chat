from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import LLMProvider, User
from app.db.session import get_db
from app.services.orchestrator import ChatMode, ChatOrchestrator

router = APIRouter()


class ChatStreamRequest(BaseModel):
    message: str
    mode: ChatMode = ChatMode.NORMAL


@router.post("/stream")
async def stream_chat(
    request: ChatStreamRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    provider = db.scalar(
        select(LLMProvider)
        .where(LLMProvider.user_id == current_user.id, LLMProvider.enabled == 1)
        .order_by(LLMProvider.created_at.desc())
        .limit(1)
    )
    orchestrator = ChatOrchestrator()
    stream = orchestrator.stream_reply(message=request.message, mode=request.mode, provider=provider)
    return StreamingResponse(stream, media_type="text/event-stream")
