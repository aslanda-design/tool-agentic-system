"""Read-only portfolio analytics — see application/query_analytics.py and
plans/agentic_asset_mapping_phase7_8.md Phase 8b. Wraps the exact same
QueryAnalyticsUseCase the `analytics` MCP server's tools use
(ai/mcp_servers/analytics/), so a human on the Dashboard and an agent see
identical numbers."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import container
from app.adapters.persistence.session import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/returns")
def get_returns(scope: str = "portfolio", periods: str | None = None, db: Session = Depends(get_db)):
    period_list = [p.strip() for p in periods.split(",") if p.strip()] if periods else None
    return container.build_query_analytics_use_case(db).get_returns(scope, period_list)


@router.get("/concentration")
def get_concentration(top_n: int = 10, db: Session = Depends(get_db)):
    return container.build_query_analytics_use_case(db).get_concentration(top_n)


@router.get("/currency-exposure")
def get_currency_exposure(db: Session = Depends(get_db)):
    return container.build_query_analytics_use_case(db).get_currency_exposure()


@router.get("/drawdown")
def get_drawdown(scope: str = "portfolio", range: str = "1Y", db: Session = Depends(get_db)):
    return container.build_query_analytics_use_case(db).get_drawdown(scope, range)


@router.get("/check-import-prices")
def check_import_prices(account_id: int | None = None, since: date | None = None, db: Session = Depends(get_db)):
    return container.build_query_analytics_use_case(db).check_import_prices(account_id, since)
