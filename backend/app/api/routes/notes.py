"""Agent-written notes — see ports/notes.py and
plans/agentic_asset_mapping_phase7_8.md Phase 8c. Thin routes straight over
NoteRepo (no use case: a note is append-only with no state machine, so the
repo method itself is the guard — see ports/notes.py's docstring)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("")
def list_notes(scope: str | None = None, limit: int = 20, db: Session = Depends(get_db)):
    return container.note_repo(db).list(scope=scope, limit=limit)


@router.post("/{note_id}/dismiss")
def dismiss_note(note_id: int, db: Session = Depends(get_db)):
    container.note_repo(db).dismiss(note_id)
    db.commit()
    return {"status": "ok"}
