"""Route-level tests for app/api/routes/chat.py (Phase 8f of
plans/agentic_asset_mapping_phase7_8.md). Real Postgres + TestClient for
session CRUD (cheap, no LLM involved); `send_message`'s actual agent call
is monkeypatched (a fake PortfolioAssistantAgent) so this file never needs
Ollama — the agent's own behavior is covered by
test_portfolio_assistant_agent.py."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from ai.common.agent_loop import AgentRunResult
from app.adapters.persistence.session import SessionLocal
from app.api.routes import chat as chat_route
from app.main import app


def _cleanup(session_ids: list[int]) -> None:
    if not session_ids:
        return
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM chat_sessions WHERE id = ANY(:ids)"), {"ids": session_ids})
        db.commit()
    finally:
        db.close()


def test_create_list_get_delete_session_round_trip():
    with TestClient(app) as client:
        created = client.post("/api/chat/sessions", json={"title": "My chat"})
        assert created.status_code == 201
        session_id = created.json()["id"]
        try:
            listed = client.get("/api/chat/sessions").json()
            assert any(s["id"] == session_id for s in listed)

            fetched = client.get(f"/api/chat/sessions/{session_id}")
            assert fetched.status_code == 200
            assert fetched.json()["session"]["title"] == "My chat"
            assert fetched.json()["messages"] == []

            deleted = client.delete(f"/api/chat/sessions/{session_id}")
            assert deleted.status_code == 200
            assert client.get(f"/api/chat/sessions/{session_id}").status_code == 404
        finally:
            _cleanup([session_id])


def test_get_unknown_session_is_404():
    with TestClient(app) as client:
        assert client.get("/api/chat/sessions/999999999").status_code == 404


def test_send_message_is_409_when_agent_disabled(monkeypatch):
    monkeypatch.setattr(chat_route.settings, "agent_enabled", False)
    with TestClient(app) as client:
        created = client.post("/api/chat/sessions", json={})
        session_id = created.json()["id"]
        try:
            response = client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
            assert response.status_code == 409
        finally:
            _cleanup([session_id])


def test_send_message_is_404_for_an_unknown_session(monkeypatch):
    monkeypatch.setattr(chat_route.settings, "agent_enabled", True)
    with TestClient(app) as client:
        response = client.post("/api/chat/sessions/999999999/messages", json={"message": "hi"})
        assert response.status_code == 404


def test_send_message_calls_the_agent_and_returns_the_persisted_reply(monkeypatch):
    monkeypatch.setattr(chat_route.settings, "agent_enabled", True)

    class _FakeAgent:
        def send_message(self, session_id, message):
            db = SessionLocal()
            try:
                repo = chat_route.container.chat_repo(db)
                repo.add_message(session_id, "user", message)
                repo.add_message(session_id, "assistant", "€15,744.")
                db.commit()
            finally:
                db.close()
            return AgentRunResult(status="REPLIED", steps=1, final_message="€15,744.")

    monkeypatch.setattr(chat_route.container, "build_portfolio_assistant_agent", lambda: _FakeAgent())

    with TestClient(app) as client:
        created = client.post("/api/chat/sessions", json={})
        session_id = created.json()["id"]
        try:
            response = client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "what's my total value?"})
            assert response.status_code == 201
            body = response.json()
            assert body["status"] == "REPLIED"
            assert body["reply"] == "€15,744."
            assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        finally:
            _cleanup([session_id])
