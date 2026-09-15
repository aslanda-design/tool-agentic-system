"""Outbound port for portfolio_assistant's chat sessions — see
plans/agentic_asset_mapping_phase7_8.md Phase 8f. Implementation:
adapters/persistence/repositories.py::SqlChatRepo (`chat_sessions` +
`chat_messages`, migration 0004). Its own small port file, same reasoning
as ports/notes.py: a distinct concern with no overlap in fields or
invariants with the portfolio/asset repos."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import ChatMessage, ChatSession


class ChatRepo(ABC):
    @abstractmethod
    def create_session(self, title: str = "") -> int:
        """Start a new conversation thread. Returns its id."""

    @abstractmethod
    def list_sessions(self, limit: int = 50) -> list[ChatSession]:
        """Most recently active first (by updated_at, not created_at) —
        what the frontend's history sidebar shows."""

    @abstractmethod
    def get_session(self, session_id: int) -> ChatSession | None: ...

    @abstractmethod
    def rename_session(self, session_id: int, title: str) -> None:
        """No-op if the session doesn't exist."""

    @abstractmethod
    def delete_session(self, session_id: int) -> None:
        """Deletes the session and every message in it (ON DELETE CASCADE).
        No-op if the session doesn't exist."""

    @abstractmethod
    def touch_session(self, session_id: int, as_of: datetime | None = None) -> None:
        """Bump updated_at to now (or `as_of`, for tests) — called once per
        turn so the session floats to the top of the history sidebar."""

    @abstractmethod
    def add_message(
        self, session_id: int, role: str, content: str, tool_calls: list[dict] | None = None
    ) -> int:
        """Append one message. `role` is 'user' or 'assistant'; `tool_calls`
        is only ever set on an 'assistant' row (which tools that reply
        used — for the UI, never replayed back into the model as session
        memory, see ai.common.agent_loop.run_agent's `history` param).
        Returns the new message's id."""

    @abstractmethod
    def list_messages(self, session_id: int, limit: int | None = None) -> list[ChatMessage]:
        """Oldest first (transcript order). `limit`, if given, returns the
        most recent `limit` messages, still in oldest-first order — used to
        bound how much history gets replayed into a new turn."""
