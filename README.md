# LLM Expert Chat

An enhanced ChatGPT-style web chat app for personal multi-device use, with configurable LLM providers and an expert-team orchestration mode.

## Stack

- Frontend: Vue 3 + Vite + TypeScript
- Backend: FastAPI + SQLAlchemy
- Database: MySQL 8
- Streaming: Server-Sent Events

## Layout

```text
backend/
  app/
    api/
    core/
    db/
    services/
frontend/
  src/
    api/
    components/
    views/
```

## First Milestone

1. User login.
2. ChatGPT-style conversation UI.
3. OpenAI-compatible provider configuration.
4. Normal single-model chat mode.
5. Expert mode with two experts and one synthesizer.
6. Expandable expert details below final answers.
