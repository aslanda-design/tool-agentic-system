from datetime import date, timedelta
from decimal import Decimal

from app.application.backfill_quant_history import BackfillQuantHistoryUseCase
from app.domain.models import AssetClass
from app.ports.market_data import BarData
from tests.fakes import FakeAssetRepo, FakeMarketDataRepo


class _FakeBarSource:
    def __init__(self, bars: list[BarData] | None) -> None:
        self.bars = bars
        self.calls: list[tuple] = []

    def get_daily_history(self, symbol, mic, start, end, expected_currency=None):
        self.calls.append((symbol, mic, start, end, expected_currency))
        return self.bars


def _some_bars(n: int, end: date) -> list[dict]:
    return [
        {
            "date": end - timedelta(days=i),
            "open": Decimal(10),
            "high": Decimal(11),
            "low": Decimal(9),
            "close": Decimal(10),
            "adj_close": Decimal(10),
            "volume": Decimal(100),
        }
        for i in range(n)
    ]


def test_skips_backfill_when_enough_history_already_exists():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("EUNL.DE", "iShares Core MSCI World", AssetClass.ETF, "EUR")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(asset.id, _some_bars(30, date.today()), source="yfinance")
    bar_source = _FakeBarSource(bars=[])

    BackfillQuantHistoryUseCase(asset_repo, market_data_repo, bar_source).ensure_history(
        asset.id, min_days=10
    )

    assert bar_source.calls == []


def test_backfills_when_history_is_short():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create(
        "EUNL.DE", "iShares Core MSCI World", AssetClass.ETF, "EUR", exchange="XETR"
    )
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(asset.id, _some_bars(5, date.today()), source="yfinance")
    fresh_bars = [
        BarData(
            date=date.today() - timedelta(days=i),
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            adj_close=Decimal(1),
            volume=Decimal(1),
        )
        for i in range(50)
    ]
    bar_source = _FakeBarSource(bars=fresh_bars)

    BackfillQuantHistoryUseCase(asset_repo, market_data_repo, bar_source).ensure_history(
        asset.id, min_days=30
    )

    assert len(bar_source.calls) == 1
    symbol, mic, _start, _end, currency = bar_source.calls[0]
    assert symbol == "EUNL.DE"
    assert mic == "XETR"
    assert currency == "EUR"
    persisted = market_data_repo.get_bars(
        asset.id, date.today() - timedelta(days=365), date.today()
    )
    assert len(persisted) >= 50


def test_leaves_state_untouched_when_fallback_also_fails():
    asset_repo = FakeAssetRepo()
    asset = asset_repo.create("OBSCURE", "Obscure Fund", AssetClass.FUND, "EUR")
    market_data_repo = FakeMarketDataRepo()
    market_data_repo.upsert_bars(asset.id, _some_bars(3, date.today()), source="yfinance")
    bar_source = _FakeBarSource(bars=None)

    BackfillQuantHistoryUseCase(asset_repo, market_data_repo, bar_source).ensure_history(
        asset.id, min_days=30
    )

    persisted = market_data_repo.get_bars(
        asset.id, date.today() - timedelta(days=365), date.today()
    )
    assert len(persisted) == 3


def test_no_op_for_unknown_asset():
    asset_repo = FakeAssetRepo()
    market_data_repo = FakeMarketDataRepo()
    bar_source = _FakeBarSource(bars=[])

    # Should not raise even though asset 999 doesn't exist.
    BackfillQuantHistoryUseCase(asset_repo, market_data_repo, bar_source).ensure_history(
        999, min_days=30
    )
    assert bar_source.calls == []
