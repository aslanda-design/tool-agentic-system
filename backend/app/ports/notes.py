"""Outbound port for agent-written notes — see plans/agentic_asset_mapping_phase7_8.md
Phase 8c. Implementation: adapters/persistence/repositories.py::SqlNoteRepo
(the `ai_notes` table, migration 0003). Deliberately its own small port
file rather than another method group on repositories.py::PortfolioRepo —
notes are a distinct concern (agent output, not portfolio state) with no
overlap in fields or invariants with anything else there.

This is also the second (and, by design, the simplest possible) legitimate
write path an MCP tool can use — see ai/mcp_servers/notes/tools/save_note.py
and backend/ai/AGENTS.md rule 6. A note is append-only and has no state
machine to guard (unlike AssetRepo.apply_listing's currency/identifier
invariants), so `add()` itself — not a use case — is the whole guard."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import Note


class NoteRepo(ABC):
    @abstractmethod
    def add(self, agent: str, scope: str, title: str, body: str, account_id: int | None = None) -> int:
        """Write one note. Returns the new note's id. Never overwrites or
        merges with an existing note — every call is a new row, even a
        repeat "nothing to report" from the same agent on the same day."""

    @abstractmethod
    def list(self, scope: str | None = None, since: datetime | None = None, limit: int = 20) -> list[Note]:
        """Most recent notes first. `scope` filters to 'account' or
        'portfolio' notes; omit for both. Includes dismissed notes — callers
        that want to hide them filter on `dismissed_at is None` themselves,
        since some callers (an audit view) want everything."""

    @abstractmethod
    def dismiss(self, note_id: int) -> None:
        """Set dismissed_at to now. A no-op (not an error) if the note
        doesn't exist or is already dismissed — dismissing is idempotent
        from the caller's point of view."""
