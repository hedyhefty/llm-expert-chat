from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, conversations, providers
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Expert Chat API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(conversations.router, prefix="/api/conversations", tags=["conversations"])
    app.include_router(providers.router, prefix="/api/providers", tags=["providers"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

