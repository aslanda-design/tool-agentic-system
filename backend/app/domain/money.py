"""Money value object. All monetary amounts in the domain are Decimal — never
float, which silently loses precision on financial arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        object.__setattr__(self, "currency", self.currency.upper())

    def __add__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")

    def rounded(self, places: Decimal = TWO_PLACES) -> "Money":
        return Money(self.amount.quantize(places, rounding=ROUND_HALF_UP), self.currency)

    def convert(self, target_currency: str, rate: Decimal) -> "Money":
        """rate = units of target_currency per 1 unit of self.currency."""
        target_currency = target_currency.upper()
        if self.currency == target_currency:
            return self
        return Money(self.amount * rate, target_currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(Decimal("0"), currency)
