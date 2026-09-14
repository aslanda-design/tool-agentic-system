"""Composition root — the only module that wires adapters into use cases.
Routes depend on `get_db` for a session and call the `build_*` factories
here; nothing else in the app decides which adapter implementation to use.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.brokers.ibkr import IBKRAdapter
from app.adapters.brokers.ibkr_flex import IBKRFlexAdapter
from app.adapters.market_data.fx_adapter import YFinanceFxRates
from app.adapters.market_data.yfinance_adapter import YFinanceMarketData
from app.adapters.persistence.repositories import SqlAssetRepo, SqlMarketDataRepo, SqlPortfolioRepo
from app.application.asset_chart import GetAssetChartUseCase
from app.application.build_snapshots import BuildSnapshotsUseCase
from app.application.import_transactions import CsvImportUseCase, ImportStatementUseCase
from app.application.manual_entry import ManualEntryUseCase, MapAssetUseCase
from app.application.opening_balance import SuggestOpeningBalanceUseCase
from app.application.query_asset import QueryAssetUseCase
from app.application.query_portfolio import QueryPortfolioUseCase
from app.application.refresh_market_data import RefreshMarketDataUseCase
from app.application.search_assets import SearchAssetsUseCase
from app.application.sync_broker import SyncBrokerUseCase
from app.config import settings

BROKER_ADAPTERS = {
    "interactive_brokers": lambda: IBKRAdapter(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id),
}


def asset_repo(db: Session) -> SqlAssetRepo:
    return SqlAssetRepo(db)


def portfolio_repo(db: Session) -> SqlPortfolioRepo:
    return SqlPortfolioRepo(db)


def market_data_repo(db: Session) -> SqlMarketDataRepo:
    return SqlMarketDataRepo(db)


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
    return MapAssetUseCase(asset_repo(db), YFinanceMarketData())


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


def build_search_assets_use_case(db: Session) -> SearchAssetsUseCase:
    return SearchAssetsUseCase(asset_repo(db), YFinanceMarketData())


def build_asset_chart_use_case(db: Session) -> GetAssetChartUseCase:
    return GetAssetChartUseCase(asset_repo(db), YFinanceMarketData())
