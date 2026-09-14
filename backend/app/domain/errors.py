class DomainError(Exception):
    """Base class for errors raised by the domain/application layers."""


class AssetNotFoundError(DomainError):
    pass


class AssetConflictError(DomainError):
    """Raised when editing an asset's symbol/ISIN would collide with another
    asset's unique symbol/ISIN."""


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
