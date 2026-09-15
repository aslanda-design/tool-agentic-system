"""portfolio_assistant's chat sessions — see ports/chat.py and
plans/agentic_asset_mapping_phase7_8.md Phase 8f. Session CRUD is thin
routes straight over ChatRepo (no use case: same "a plain repo write is
its own guard" reasoning as notes.py). Sending a message is the one route
that runs a real LLM call — see PortfolioAssistantAgent, which owns the
write path for both the user's message and the assistant's reply."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import ChatMessageRequest, CreateChatSessionRequest
from app.config import settings

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/sessions")
def list_sessions(limit: int = 50, db: Session = Depends(get_db)):
    return container.chat_repo(db).list_sessions(limit)


@router.post("/sessions", status_code=201)
def create_session(request: CreateChatSessionRequest, db: Session = Depends(get_db)):
    session_id = container.chat_repo(db).create_session(request.title)
    db.commit()
    return container.chat_repo(db).get_session(session_id)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    repo = container.chat_repo(db)
    session = repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"session": session, "messages": repo.list_messages(session_id)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    container.chat_repo(db).delete_session(session_id)
    db.commit()
    return {"status": "ok"}


@router.post("/sessions/{session_id}/messages", status_code=201)
def send_message(session_id: int, request: ChatMessageRequest, db: Session = Depends(get_db)):
    """Runs the portfolio_assistant agent synchronously — real LLM latency,
    up to `AGENT_TIMEOUT_SECONDS`. The agent itself persists both the
    user's message and its own reply (see
    ai/agents/portfolio_assistant/agent.py, in its own DB session); this
    route only touches `chat_repo` to check the session exists first and
    to read the two rows the agent just wrote back for the response."""
    if not settings.agent_enabled:
        raise HTTPException(status_code=409, detail="The portfolio assistant is not enabled (AGENT_ENABLED=false).")
    if container.chat_repo(db).get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    result = container.build_portfolio_assistant_agent().send_message(session_id, request.message)
    messages = container.chat_repo(db).list_messages(session_id, limit=2)  # the user + assistant rows just written
    return {
        "status": result.status,
        "reply": messages[-1].content if messages else "",
        "messages": messages,
        "error": result.error,
    }
