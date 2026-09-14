"""Read/act on the security resolver's output (application/resolve_security.py)
— see plans/agentic_asset_mapping.md Phase 4. `POST /api/assets/{id}/resolve`
(run the resolver for one asset right now) lives in assets.py, grouped with
the rest of that resource; everything about an existing resolution lives here."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db
from app.api.schemas import AcceptCandidateRequest, AddCandidateRequest, FlagForReviewRequest
from app.config import settings
from app.domain.errors import DomainError, ResolutionNotFoundError
from app.domain.listings import ResolutionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resolutions", tags=["resolutions"])


def _parse_statuses(status: str | None) -> list[ResolutionStatus]:
    if not status:
        return [ResolutionStatus.NEEDS_REVIEW, ResolutionStatus.NEEDS_AGENT]
    statuses = []
    for raw in status.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            statuses.append(ResolutionStatus(raw))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status: {raw!r}") from None
    return statuses


@router.get("")
def list_resolutions(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    """Open resolutions waiting on a decision — defaults to NEEDS_REVIEW +
    NEEDS_AGENT (the "needs attention" set). `status` is a comma list, e.g.
    `?status=NEEDS_REVIEW`."""
    return container.resolution_repo(db).list_by_status(_parse_statuses(status), limit)


@router.get("/recent")
def list_recent_resolutions(
    decided_by: str | None = None, days: int = 14, limit: int = 100, db: Session = Depends(get_db)
):
    """Resolutions decided in the last `days` days — a spot-check view of
    what the resolver has actually done. `decided_by` is a comma list (e.g.
    `?decided_by=rules,agent` to see automated decisions but not manual ones)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    decided_by_list = [v.strip() for v in decided_by.split(",") if v.strip()] if decided_by else None
    return container.resolution_repo(db).list_recent(since, decided_by_list, limit)


@router.get("/{resolution_id}")
def get_resolution(resolution_id: int, db: Session = Depends(get_db)):
    resolution = container.resolution_repo(db).get(resolution_id)
    if resolution is None:
        raise HTTPException(status_code=404, detail="Resolution not found")
    return resolution


@router.post("/{resolution_id}/accept")
def accept_resolution_candidate(resolution_id: int, request: AcceptCandidateRequest, db: Session = Depends(get_db)):
    """Apply one of the resolution's candidates as the asset's pricing
    listing. Refreshes market data and rebuilds snapshots right away rather
    than making the user wait for the next scheduled refresh — same
    reasoning as POST /api/assets/{id}/map."""
    try:
        result = container.build_resolve_security_use_case(db).accept(
            resolution_id, request.candidate_id, decided_by="user", note=request.note
        )
    except ResolutionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    container.build_refresh_market_data_use_case(db).refresh_all()
    db.commit()
    container.build_snapshots_use_case(db).execute()
    db.commit()
    return result


@router.post("/{resolution_id}/candidates")
def add_resolution_candidate(resolution_id: int, request: AddCandidateRequest, db: Session = Depends(get_db)):
    """Validate and score a symbol the user typed in — adds it as a
    candidate on the resolution (see accept() to actually apply one)."""
    try:
        candidate = container.build_resolve_security_use_case(db).add_candidate(
            resolution_id, request.symbol, source="user"
        )
    except ResolutionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return candidate


@router.post("/{resolution_id}/flag-for-review")
def flag_resolution_for_review(resolution_id: int, request: FlagForReviewRequest, db: Session = Depends(get_db)):
    try:
        result = container.build_resolve_security_use_case(db).flag_for_review(
            resolution_id, request.note, flagged_by="user"
        )
    except ResolutionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return result


@router.post("/{resolution_id}/agent", status_code=202)
def request_agent_resolution(resolution_id: int, db: Session = Depends(get_db)):
    """Run the security_resolver agent (a local LLM via Ollama — see
    plans/agentic_asset_mapping.md Phase 6) on one resolution right now.
    Synchronous, and can take up to AGENT_TIMEOUT_SECONDS: this is an
    explicit, user-initiated action, never something scheduled or wired
    into BackgroundTasks (see backend/AGENTS.md's "Security resolver"
    section for why that distinction matters here). The agent always
    leaves the resolution in a terminal-for-now state (RESOLVED_BY_AGENT
    or NEEDS_REVIEW), never stuck — see SecurityResolverAgent's fallback."""
    if not settings.agent_enabled:
        raise HTTPException(status_code=409, detail="The resolution agent is not enabled (AGENT_ENABLED=false).")
    resolution = container.resolution_repo(db).get(resolution_id)
    if resolution is None:
        raise HTTPException(status_code=404, detail="Resolution not found")

    result = container.build_security_resolver_agent().run(resolution_id)
    return {
        "agent_run": {
            "status": result.status,
            "steps": result.steps,
            "final_message": result.final_message,
            "error": result.error,
        },
        "resolution": container.resolution_repo(db).get(resolution_id),
    }
