"""Composition root — the only module that wires adapters into use cases.
Routes depend on `get_db` for a session and call the `build_*` factories
here; nothing else in the app decides which adapter implementation to use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.adapters.brokers.ibkr import IBKRAdapter
from app.adapters.brokers.ibkr_flex import IBKRFlexAdapter
from app.adapters.market_data.fx_adapter import YFinanceFxRates
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData
from app.adapters.persistence.repositories import (
    SqlAssetRepo,
    SqlChatRepo,
    SqlMarketDataRepo,
    SqlNoteRepo,
    SqlPortfolioRepo,
    SqlResolutionRepo,
)
from app.adapters.security_master.openfigi_adapter import OpenFigiSecurityMaster
from app.application.asset_chart import GetAssetChartUseCase
from app.application.build_snapshots import BuildSnapshotsUseCase
from app.application.import_transactions import CsvImportUseCase, ImportStatementUseCase
from app.application.manual_entry import ManualEntryUseCase, MapAssetUseCase
from app.application.opening_balance import SuggestOpeningBalanceUseCase
from app.application.query_analytics import QueryAnalyticsUseCase
from app.application.query_asset import QueryAssetUseCase
from app.application.query_market_data import QueryMarketDataUseCase
from app.application.query_portfolio import QueryPortfolioUseCase
from app.application.refresh_market_data import RefreshMarketDataUseCase
from app.application.resolve_security import ResolveSecurityUseCase
from app.application.search_assets import SearchAssetsUseCase
from app.application.sync_broker import SyncBrokerUseCase
from app.config import settings

if TYPE_CHECKING:
    # Only for the annotations below — see build_security_resolver_agent's
    # docstring for why the real import is deferred to inside each function.
    from ai.agents.import_reviewer.agent import ImportReviewerAgent
    from ai.agents.portfolio_assistant.agent import PortfolioAssistantAgent
    from ai.agents.security_resolver.agent import SecurityResolverAgent

BROKER_ADAPTERS = {
    "interactive_brokers": lambda: IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id),
}


def asset_repo(db: Session) -> SqlAssetRepo:
    return SqlAssetRepo(db)


def portfolio_repo(db: Session) -> SqlPortfolioRepo:
    return SqlPortfolioRepo(db)


def market_data_repo(db: Session) -> SqlMarketDataRepo:
    return SqlMarketDataRepo(db)


def resolution_repo(db: Session) -> SqlResolutionRepo:
    return SqlResolutionRepo(db)


def note_repo(db: Session) -> SqlNoteRepo:
    return SqlNoteRepo(db)


def chat_repo(db: Session) -> SqlChatRepo:
    return SqlChatRepo(db)


def build_sync_broker_use_case(db: Session, broker_key: str) -> SyncBrokerUseCase:
    factory = BROKER_ADAPTERS.get(broker_key)
    if factory is None:
        raise ValueError(f"Unknown broker: {broker_key!r}. Available: {list(BROKER_ADAPTERS)}")
    return SyncBrokerUseCase(factory(), asset_repo(db), portfolio_repo(db))


def build_flex_import_use_case(db: Session, account_id: int, account_external_id: str) -> ImportStatementUseCase:
    flex = IBKRFlexAdapter(settings.ibkr_flex_token, settings.ibkr_flex_query_id, account_external_id)
    return ImportStatementUseCase(flex, account_id, asset_repo(db), portfolio_repo(db))


def build_csv_import_use_case(db: Session) -> CsvImportUseCase:
    return CsvImportUseCase(asset_repo(db), portfolio_repo(db))


def build_manual_entry_use_case(db: Session) -> ManualEntryUseCase:
    return ManualEntryUseCase(asset_repo(db), portfolio_repo(db))


def build_map_asset_use_case(db: Session) -> MapAssetUseCase:
    return MapAssetUseCase(asset_repo(db), YFinanceMarketData(), resolution_repo(db))


def build_suggest_opening_balance_use_case(db: Session) -> SuggestOpeningBalanceUseCase:
    return SuggestOpeningBalanceUseCase(portfolio_repo(db))


def build_refresh_market_data_use_case(db: Session) -> RefreshMarketDataUseCase:
    return RefreshMarketDataUseCase(
        YFinanceMarketData(), YFinanceFxRates(), market_data_repo(db), asset_repo(db), settings.base_currency
    )


def build_snapshots_use_case(db: Session) -> BuildSnapshotsUseCase:
    return BuildSnapshotsUseCase(portfolio_repo(db), market_data_repo(db), asset_repo(db), settings.base_currency)


def build_query_portfolio_use_case(db: Session) -> QueryPortfolioUseCase:
    return QueryPortfolioUseCase(portfolio_repo(db), market_data_repo(db), asset_repo(db), settings.base_currency)


def build_query_asset_use_case(db: Session) -> QueryAssetUseCase:
    return QueryAssetUseCase(asset_repo(db), portfolio_repo(db), market_data_repo(db), settings.base_currency)


def build_query_market_data_use_case(db: Session) -> QueryMarketDataUseCase:
    return QueryMarketDataUseCase(asset_repo(db), portfolio_repo(db), market_data_repo(db))


def build_query_analytics_use_case(db: Session) -> QueryAnalyticsUseCase:
    return QueryAnalyticsUseCase(
        build_query_portfolio_use_case(db), asset_repo(db), portfolio_repo(db), market_data_repo(db)
    )


def build_search_assets_use_case(db: Session) -> SearchAssetsUseCase:
    return SearchAssetsUseCase(asset_repo(db), YFinanceMarketData())


def build_asset_chart_use_case(db: Session) -> GetAssetChartUseCase:
    return GetAssetChartUseCase(asset_repo(db), YFinanceMarketData())


def build_resolve_security_use_case(db: Session) -> ResolveSecurityUseCase:
    return ResolveSecurityUseCase(
        asset_repo(db),
        portfolio_repo(db),
        resolution_repo(db),
        OpenFigiSecurityMaster(settings.openfigi_api_key),
        YFinanceMarketData(),
        settings.agent_enabled,
    )


def build_security_resolver_agent() -> SecurityResolverAgent:
    """Stateless — opens its own DB sessions and MCP subprocess per run
    (see ai/agents/security_resolver/agent.py), so no db/Session argument.
    Imported lazily (not at module level, like every other adapter here):
    ai/agents/security_resolver/agent.py itself imports from this module
    (`build_resolve_security_use_case`, `resolution_repo`), so a top-level
    import here would be circular — see backend/ai/AGENTS.md rule 1."""
    from ai.agents.security_resolver.agent import SecurityResolverAgent

    return SecurityResolverAgent()


def build_import_reviewer_agent() -> ImportReviewerAgent:
    """Stateless, same reasoning as build_security_resolver_agent above."""
    from ai.agents.import_reviewer.agent import ImportReviewerAgent

    return ImportReviewerAgent()


def build_portfolio_assistant_agent() -> PortfolioAssistantAgent:
    """Stateless, same reasoning as build_security_resolver_agent above."""
    from ai.agents.portfolio_assistant.agent import PortfolioAssistantAgent

    return PortfolioAssistantAgent()
