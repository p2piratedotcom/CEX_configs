from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping
from .models import HedgeSide

class SpotOrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    LIMIT_MAKER = "LIMIT_MAKER"
    IMMEDIATE_OR_CANCEL = "IMMEDIATE_OR_CANCEL"
    FILL_OR_KILL = "FILL_OR_KILL"


class SpotError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: Any = None,
        execution_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.execution_unknown = execution_unknown


class LiveTradingDisabled(SpotError):
    pass


class LiveTransfersDisabled(SpotError):
    pass


@dataclass(frozen=True, slots=True)
class SymbolCheck:
    symbol: str
    listed: bool
    spot_trading_allowed: bool
    order_types: tuple[str, ...]
    problems: tuple[str, ...]
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TimeSync:
    server_time_ms: int
    local_midpoint_ms: int
    offset_ms: int
    round_trip_ms: int


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    quantity_step: Decimal
    price_step: Decimal
    min_quote_amount: Decimal
    max_quote_amount: Decimal | None
    order_types: tuple[str, ...]
    trade_side_type: int

    def allows(self, side: HedgeSide) -> bool:
        return self.trade_side_type == 1 or (
            self.trade_side_type == 2 and side is HedgeSide.BUY
        ) or (self.trade_side_type == 3 and side is HedgeSide.SELL)


