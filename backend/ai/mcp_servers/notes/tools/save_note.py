"""MCP tool: write a note. Thin wrapper over NoteRepo.add — the WRITE
`import_reviewer`/`weekly_report` are allowed (see backend/ai/AGENTS.md rule
6's second case: a note is append-only with no state machine, so add()
itself is the whole guard, not a use case)."""

from __future__ import annotations

from app.adapters.persistence.session import SessionLocal
from app.container import note_repo


def save_note(agent: str, scope: str, title: str, body: str, account_id: int | None = None) -> dict:
    """Write a short note — an agent's finding or summary, shown to the
    user later (Accounts page / Dashboard). This is a terminal action:
    every note-writing agent run must end with exactly one call to this.

    Args:
        agent: Which agent wrote this, e.g. "import_reviewer" or "weekly_report".
        scope: "account" for one account's finding, "portfolio" for a
            portfolio-wide one.
        title: A short one-line summary (shown as the note's heading).
        body: The full note, Markdown.
        account_id: The account this note is about, when scope is "account".

    Returns:
        The saved note's id, echoing back agent/scope/account_id/title/body.
    """
    db = SessionLocal()
    try:
        note_id = note_repo(db).add(agent, scope, title, body, account_id)
        db.commit()
        return {"id": note_id, "agent": agent, "scope": scope, "account_id": account_id, "title": title, "body": body}
    finally:
        db.close()
