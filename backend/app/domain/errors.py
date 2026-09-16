class DomainError(Exception):
    """Base class for errors raised by the domain/application layers."""


class AssetNotFoundError(DomainError):
    pass


class AssetConflictError(DomainError):
    """Raised when editing an asset's symbol/ISIN would collide with another
    asset's unique symbol/ISIN, or when apply_listing's target Yahoo symbol
    already belongs to a different asset (see ports/repositories.py)."""


class ListingNotFoundError(DomainError):
    """Raised when a candidate market-data symbol doesn't exist, or exists
    but has no currency we can price it in (see MarketDataPort.get_listing_info)."""

    def __init__(self, symbol: str) -> None:
        super().__init__(f"{symbol!r} isn't a valid, priceable listing")
        self.symbol = symbol


class ResolutionNotFoundError(DomainError):
    """Raised when an asset_resolutions id doesn't exist (see
    application/resolve_security.py)."""


class AccountNotFoundError(DomainError):
    pass


class MissingFxRateError(DomainError):
    def __init__(self, date, base: str, quote: str) -> None:
        super().__init__(f"No FX rate for {base}->{quote} on {date}")
        self.date = date
        self.base = base
        self.quote = quote


class BrokerConnectionError(DomainError):
    """Raised by broker adapters when the upstream broker can't be reached
    (e.g. TWS/IB Gateway not running, API access not enabled)."""


class StatementParseError(DomainError):
    """Raised when a statement file (CSV/XLSX) can't be parsed at all —
    distinct from a per-row warning, which the row survives."""


class UnsupportedStatementFileError(StatementParseError):
    """Raised when the uploaded bytes aren't a format we can read (e.g. a
    legacy .xls BIFF file, or an HTML table saved with an .xls extension —
    both common from Spanish bank exports). The message always names the
    detected format and the fix, never a stack trace."""


class QuantModelNotFoundError(DomainError):
    """Raised when a quant model_key isn't in domain.quant.registry.MODEL_REGISTRY
    (see plans/quant_lab.md)."""


class QuantRunNotFoundError(DomainError):
    """Raised when a quant_runs id doesn't exist."""


class InsufficientQuantHistoryError(DomainError):
    """Raised when there isn't enough persisted history before a run's
    split date to calibrate the chosen model, even after an attempted
    backfill (see application/backfill_quant_history.py)."""
