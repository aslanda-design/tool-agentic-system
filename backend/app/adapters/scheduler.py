"""Background refresh jobs. The app is off most of the time, so this is
convenience on top of the startup gap-fill in main.py — never the sole
source of truth for snapshot history. Each job opens its own DB session
(APScheduler runs jobs on a background thread, so it can't share a
request-scoped session) and commits once, same as an API request would.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.adapters.market_data.fx_adapter import YFinanceFxRates
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData
from app.adapters.persistence.repositories import SqlAssetRepo, SqlMarketDataRepo, SqlPortfolioRepo
from app.adapters.persistence.session import SessionLocal
from app.application.build_snapshots import BuildSnapshotsUseCase
from app.application.refresh_market_data import RefreshMarketDataUseCase
from app.config import settings

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
    return scheduler
