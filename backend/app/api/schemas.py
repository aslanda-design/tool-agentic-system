"""Pydantic request models. Responses are the application layer's DTOs
(plain dataclasses) returned directly — FastAPI's jsonable_encoder already
serializes dataclasses and Decimal correctly, so a parallel set of response
schemas would just duplicate the DTOs for no benefit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ManualHoldingRequest(BaseModel):
    account_id: int
    symbol: str
    name: str = ""
    currency: str = "EUR"
    quantity: Decimal
    avg_cost_price: Decimal
    isin: str | None = None


class ManualTransactionRequest(BaseModel):
    account_id: int
    symbol: str | None = None
    type: str
    quantity: Decimal
    price: Decimal
    fees: Decimal = Decimal("0")
    currency: str = "EUR"
    executed_at: date
    note: str = ""


class ManualTransactionUpdateRequest(BaseModel):
    quantity: Decimal | None = None
    price: Decimal | None = None
    fees: Decimal | None = None
    note: str | None = None


class MapAssetRequest(BaseModel):
    yfinance_symbol: str


class AssetUpdateRequest(BaseModel):
    symbol: str
    name: str
    isin: str | None = None


class ManualAccountRequest(BaseModel):
    broker_key: str
    name: str
    currency: str = "EUR"
