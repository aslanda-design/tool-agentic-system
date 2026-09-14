"""Background refresh jobs. The app is off most of the time, so this is
convenience on top of the startup gap-fill in main.py — never the sole
source of truth for snapshot history. Each job opens its own DB session
(APScheduler runs jobs on a background thread, so it can't share a
request-scoped session) and commits once, same as an API request would.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app import container
from app.adapters.market_data.fx_adapter import YFinanceFxRates
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData
from app.adapters.persistence.repositories import SqlAssetRepo, SqlMarketDataRepo, SqlPortfolioRepo
from app.adapters.persistence.session import SessionLocal
from app.application.build_snapshots import BuildSnapshotsUseCase
from app.application.refresh_market_data import RefreshMarketDataUseCase
from app.config import settings
from app.domain.listings import ResolutionStatus

logger = logging.getLogger(__name__)


def _refresh_market_data_job() -> None:
    session = SessionLocal()
    try:
        use_case = RefreshMarketDataUseCase(
            YFinanceMarketData(), YFinanceFxRates(), SqlMarketDataRepo(session), SqlAssetRepo(session), settings.base_currency
        )
        result = use_case.refresh_all()
        session.commit()
        logger.info("Market data refresh: %s", result)
    except Exception:
        session.rollback()
        logger.exception("Market data refresh job failed")
    finally:
        session.close()


def _build_snapshots_job() -> None:
    session = SessionLocal()
    try:
        use_case = BuildSnapshotsUseCase(
            SqlPortfolioRepo(session), SqlMarketDataRepo(session), SqlAssetRepo(session), settings.base_currency
        )
        days = use_case.execute()
        session.commit()
        logger.info("Snapshot build: %s day(s) written", days)
    except Exception:
        session.rollback()
        logger.exception("Snapshot build job failed")
    finally:
        session.close()


def run_resolver_job() -> None:
    """Runs the security resolver (application/resolve_security.py) over
    every asset flagged `needs_mapping` that's due an attempt — see
    ResolutionRepo.list_assets_to_resolve. Commits once per asset (so one
    bad asset — a network hiccup, an unexpected exception — never loses
    progress already made on the others), then refreshes market data and
    rebuilds snapshots once at the end if anything was actually mapped, so
    a newly-priced asset shows up on the dashboard without waiting for the
    next scheduled refresh.

    Public (not `_`-prefixed) because it's also the target of the
    BackgroundTasks callback added by api/routes/imports.py and sync.py
    right after an import/sync creates fresh needs_mapping assets.
    Deliberately NOT run from main.py's startup lifespan — see the comment
    there for why (real network calls + real data mutation on every process
    boot, including every test's `TestClient(app)`).

    NEEDS_AGENT resolutions aren't picked up here — handing them to the
    local-LLM agent is a later phase (see
    plans/agentic_asset_mapping.md Phase 6); until then they simply wait in
    NEEDS_AGENT for a human to review (see agent_enabled's docstring in
    config.py, which controls whether ambiguous resolutions land there at
    all)."""
    if not settings.resolver_enabled:
        return
    session = SessionLocal()
    try:
        asset_ids = container.resolution_repo(session).list_assets_to_resolve()
        if not asset_ids:
            return
        use_case = container.build_resolve_security_use_case(session)
        auto_accepted = 0
        for asset_id in asset_ids:
            try:
                result = use_case.resolve_asset(asset_id)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Security resolver failed for asset_id=%s", asset_id)
                continue
            if result.status is ResolutionStatus.AUTO_ACCEPTED:
                auto_accepted += 1
        logger.info("Security resolver: %d/%d asset(s) auto-accepted", auto_accepted, len(asset_ids))

        if auto_accepted:
            refresh_result = container.build_refresh_market_data_use_case(session).refresh_all()
            session.commit()
            snapshot_days = container.build_snapshots_use_case(session).execute()
            session.commit()
            logger.info("Post-resolver refresh: %s, %d snapshot day(s) rebuilt", refresh_result, snapshot_days)
    except Exception:
        session.rollback()
        logger.exception("Security resolver job failed")
    finally:
        session.close()


def gap_fill_snapshots_on_startup() -> None:
    """Runs once at boot: the app is usually off, so this catches history up
    to today before anyone loads the dashboard."""
    session = SessionLocal()
    try:
        use_case = BuildSnapshotsUseCase(
            SqlPortfolioRepo(session), SqlMarketDataRepo(session), SqlAssetRepo(session), settings.base_currency
        )
        last = SqlPortfolioRepo(session).last_snapshot_date()
        from datetime import timedelta

        from_date = last + timedelta(days=1) if last else None
        days = use_case.execute(from_date=from_date)
        session.commit()
        logger.info("Startup snapshot gap-fill: %s day(s) written", days)
    except Exception:
        session.rollback()
        logger.exception("Startup snapshot gap-fill failed")
    finally:
        session.close()


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"max_instances": 1, "coalesce": True})
    scheduler.add_job(_refresh_market_data_job, "interval", minutes=settings.market_data_refresh_minutes, id="refresh_market_data")
    scheduler.add_job(_build_snapshots_job, "cron", hour=settings.snapshot_build_hour_utc, id="build_snapshots")
    scheduler.add_job(run_resolver_job, "interval", minutes=settings.resolver_interval_minutes, id="resolve_assets")
    return scheduler
