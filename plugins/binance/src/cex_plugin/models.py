from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Sequence

ZERO = Decimal("0")


class DexSide(StrEnum):
    """Azione rispetto all'asset base configurato.

    I valori conservano il nome storico ARRR per non invalidare database,
    eventi firmati e installazioni esistenti. Il loro significato applicativo
    e SELL_BASE/BUY_BASE.
    """

    SELL_ARRR = "SELL_ARRR"
    BUY_ARRR = "BUY_ARRR"


class HedgeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.price <= ZERO:
            raise ValueError("price must be positive")
        if self.quantity <= ZERO:
            raise ValueError("quantity must be positive")


def _parse_levels(
    rows: Iterable[Sequence[str]], *, reverse: bool
) -> tuple[PriceLevel, ...]:
    levels = tuple(PriceLevel(Decimal(row[0]), Decimal(row[1])) for row in rows)
    return tuple(sorted(levels, key=lambda level: level.price, reverse=reverse))


@dataclass(frozen=True, slots=True)
class OrderBook:
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]
    observed_at_ms: int | None = None

    @classmethod
    def from_mexc(
        cls, payload: dict, *, observed_at_ms: int | None = None
    ) -> "OrderBook":
        return cls(
            bids=_parse_levels(payload.get("bids", ()), reverse=True),
            asks=_parse_levels(payload.get("asks", ()), reverse=False),
            observed_at_ms=observed_at_ms,
        )
