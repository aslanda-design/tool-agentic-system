"""Seeds a synthetic, multi-currency demo portfolio so a fresh clone isn't
an empty app — nobody evaluating this repo has an Interactive Brokers
account. Goes through the real application-layer use cases via
app.container's build_* factories, exactly like a route would — no raw SQL,
no direct repo writes where a use case exists. See README.md "Quickstart".

Usage (from backend/, or via `docker compose exec backend`):
    python scripts/seed_demo_data.py --yes
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

from app import container
from app.adapters.persistence.session import SessionLocal
from app.config import settings
from app.domain.errors import DomainError, ListingNotFoundError
from app.domain.models import AccountSource, TransactionType

# (broker_key, external_id, display name, currency)
ACCOUNTS = [
    ("demo", "demo-eur", "Demo Broker (EUR)", "EUR"),
    ("demo", "demo-usd", "Demo Broker (USD)", "USD"),
]

# Every symbol below doubles as the yfinance ticker — all liquid, real,
# resolvable. Deliberately no `.L` (London) listings: yfinance quotes those
# in pence (GBp), and a demo row entered in major units would silently sit
# 100x off (see backend/AGENTS.md's "Known limitations").

NOTE_PREFIX = "demo-seed:"


def _redacted_db_url() -> str:
    url = settings.database_url
    if "@" in url:
        scheme_and_creds, host_part = url.rsplit("@", 1)
        scheme = scheme_and_creds.split("://", 1)[0]
        return f"{scheme}://***@{host_part}"
    return url


def _seed_transactions(db, existing_notes: set[str]) -> dict[str, int]:
    """Returns {symbol: asset_id} for every asset touched, so callers can
    resolve each one's market-data listing next."""
    manual = container.build_manual_entry_use_case(db)
    asset_ids: dict[str, int] = {}

    def _add(account_id: int, key: str, **kwargs) -> None:
        note = f"{NOTE_PREFIX}{key}"
        if note in existing_notes:
            return
        txn = manual.add_transaction(account_id=account_id, note=note, **kwargs)
        if txn.asset_id is not None and kwargs.get("symbol"):
            asset_ids[kwargs["symbol"]] = txn.asset_id

    portfolio_repo = container.portfolio_repo(db)
    eur_account = portfolio_repo.get_or_create_account(*ACCOUNTS[0], AccountSource.MANUAL)
    usd_account = portfolio_repo.get_or_create_account(*ACCOUNTS[1], AccountSource.MANUAL)

    # Opening cash deposits — build_snapshots.py's cash-flow convention.
    _add(
        eur_account.id,
        "deposit-eur",
        symbol=None,
        type_=TransactionType.DEPOSIT,
        quantity=Decimal(1),
        price=Decimal(40000),
        fees=Decimal(0),
        currency="EUR",
        executed_at=date(2023, 10, 2),
    )
    _add(
        usd_account.id,
        "deposit-usd",
        symbol=None,
        type_=TransactionType.DEPOSIT,
        quantity=Decimal(1),
        price=Decimal(20000),
        fees=Decimal(0),
        currency="USD",
        executed_at=date(2023, 10, 2),
    )

    # Staggered buys, spread over ~2 years, so value history/drawdown/quant
    # calibration all have something real to work with.
    eur_buys = [
        ("SAP.DE", date(2023, 10, 10), Decimal(30), Decimal("128.40")),
        ("SAP.DE", date(2024, 6, 12), Decimal(15), Decimal("165.20")),
        ("ASML.AS", date(2023, 11, 3), Decimal(8), Decimal("580.00")),
        ("ASML.AS", date(2024, 9, 20), Decimal(4), Decimal("890.50")),
        ("IWDA.AS", date(2023, 12, 1), Decimal(50), Decimal("82.30")),
        ("IWDA.AS", date(2024, 4, 15), Decimal(40), Decimal("88.10")),
        ("IWDA.AS", date(2025, 1, 20), Decimal(30), Decimal("95.60")),
    ]
    for i, (symbol, when, qty, price) in enumerate(eur_buys):
        _add(
            eur_account.id,
            f"buy-{symbol}-{i}",
            symbol=symbol,
            type_=TransactionType.BUY,
            quantity=qty,
            price=price,
            fees=Decimal("2.50"),
            currency="EUR",
            executed_at=when,
        )

    usd_buys = [
        ("AAPL", date(2023, 10, 18), Decimal(20), Decimal("178.40")),
        ("AAPL", date(2024, 7, 9), Decimal(10), Decimal("221.50")),
        ("MSFT", date(2023, 11, 22), Decimal(12), Decimal("370.10")),
        ("MSFT", date(2024, 5, 3), Decimal(6), Decimal("415.80")),
        ("VOO", date(2023, 12, 14), Decimal(15), Decimal("410.00")),
        ("VOO", date(2024, 8, 6), Decimal(10), Decimal("470.25")),
        ("VOO", date(2025, 2, 11), Decimal(8), Decimal("510.90")),
    ]
    for i, (symbol, when, qty, price) in enumerate(usd_buys):
        _add(
            usd_account.id,
            f"buy-{symbol}-{i}",
            symbol=symbol,
            type_=TransactionType.BUY,
            quantity=qty,
            price=price,
            fees=Decimal("1.00"),
            currency="USD",
            executed_at=when,
        )

    # One partial sell (realized P&L) — keeps the MSFT position open.
    _add(
        usd_account.id,
        "sell-MSFT-0",
        symbol="MSFT",
        type_=TransactionType.SELL,
        quantity=Decimal(5),
        price=Decimal("430.00"),
        fees=Decimal("1.00"),
        currency="USD",
        executed_at=date(2025, 3, 4),
    )

    # Two dividends.
    _add(
        usd_account.id,
        "dividend-AAPL-0",
        symbol="AAPL",
        type_=TransactionType.DIVIDEND,
        quantity=Decimal(1),
        price=Decimal("18.60"),
        fees=Decimal(0),
        currency="USD",
        executed_at=date(2024, 11, 14),
    )
    _add(
        eur_account.id,
        "dividend-IWDA-0",
        symbol="IWDA.AS",
        type_=TransactionType.DIVIDEND,
        quantity=Decimal(1),
        price=Decimal("22.10"),
        fees=Decimal(0),
        currency="EUR",
        executed_at=date(2024, 12, 20),
    )

    return asset_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--force", action="store_true", help="seed even if accounts already exist")
    args = parser.parse_args()

    print(f"This will write demo data to: {_redacted_db_url()}")
    if not args.yes:
        print("Re-run with --yes to proceed.")
        return 1

    db = SessionLocal()
    try:
        existing_accounts = container.portfolio_repo(db).list_accounts()
        if any(a.source == AccountSource.API for a in existing_accounts):
            print(
                "Refusing to seed: this database has real (source=api) broker "
                "accounts. Point DATABASE_URL at an empty/demo database instead."
            )
            return 1
        if existing_accounts and not args.force:
            print(
                f"{len(existing_accounts)} account(s) already exist. "
                "Re-run with --force to seed anyway (safe — seeding is "
                "idempotent by note marker)."
            )
            return 1

        existing_notes: set[str] = set()
        for account in existing_accounts:
            for txn in container.portfolio_repo(db).list_transactions(account_id=account.id):
                if txn.note and txn.note.startswith(NOTE_PREFIX):
                    existing_notes.add(txn.note)

        asset_ids = _seed_transactions(db, existing_notes)
        db.commit()
        print(f"Seeded transactions ({len(asset_ids)} distinct asset(s)).")

        map_asset = container.build_map_asset_use_case(db)
        mapped, failed = 0, []
        for symbol, asset_id in asset_ids.items():
            try:
                map_asset.resolve(asset_id, symbol)
                mapped += 1
            except ListingNotFoundError:
                failed.append(symbol)
        db.commit()

        if failed:
            print(
                f"Structure seeded, but {len(failed)} symbol(s) had no market "
                f"data (offline, or yfinance unreachable): {', '.join(failed)}. "
                "Re-run with --force when online to fill in prices."
            )
            return 1
        print(f"Mapped {mapped} asset(s) to their market-data listing.")

        refresh = container.build_refresh_market_data_use_case(db)
        history_assets = refresh.refresh_history()
        quotes = refresh.refresh_quotes()
        fx_pairs = refresh.refresh_fx()
        db.commit()
        print(
            f"Refreshed market data: history for {history_assets} asset(s), "
            f"{quotes} quote(s), {fx_pairs} FX pair(s)."
        )

        snapshots = container.build_snapshots_use_case(db).execute()
        db.commit()
        print(f"Built {snapshots} portfolio snapshot(s).")

        print(
            "Done — the Dashboard, Accounts, Asset detail, and Quant Lab "
            "pages now have real demo data."
        )
        return 0
    except DomainError as exc:
        db.rollback()
        print(f"Failed: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
