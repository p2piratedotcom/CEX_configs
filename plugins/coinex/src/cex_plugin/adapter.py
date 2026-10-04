"""Experimental CoinEx V2 Spot adapter; not validated against a live account."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

from .support import (
    Base,
    bounded_client,
    precision_step,
    client_id,
    decimal,
    order,
    rows,
    symbol as validate_symbol,
    text,
)
from .types import SpotError, SymbolRules


class CoinEx(Base):
    def unwrap(self, raw, *, write=False):
        if not isinstance(raw, dict) or type(raw.get("code")) is not int:
            raise SpotError("Invalid CoinEx response", execution_unknown=write)
        super().unwrap(raw, write=write)
        if raw["code"] != 0 or "data" not in raw:
            raise SpotError(
                "CoinEx rejected request",
                payload={"code": raw["code"]},
                execution_unknown=write,
            )
        self._has_next = raw.get("pagination", {}).get("has_next") is True
        return raw["data"]

    def signed(self, method, path, params=None, *, body=None, write=False, **budgets):
        self.credentials()
        query = urlencode(params or {})
        payload = (
            json.dumps(body, separators=(",", ":")).encode()
            if body is not None
            else b""
        )
        timestamp = str(self.clock_ms() + self.offset)
        request_path = "/v2" + path + ("?" + query if query else "")
        signature = hmac.new(
            self.api_secret.encode(),
            method.encode() + request_path.encode() + payload + timestamp.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "X-COINEX-KEY": self.api_key,
            "X-COINEX-SIGN": signature,
            "X-COINEX-TIMESTAMP": timestamp,
            "X-COINEX-WINDOWTIME": str(self.config["recv_window_ms"]),
            "Content-Type": "application/json",
        }
        return self.send(
            method,
            path,
            params=params,
            body=payload or None,
            headers=headers,
            write=write,
            **budgets
        )

    def server_time(self, *, timeout=None):
        raw = self.send("GET", "/time", timeout=timeout, total_timeout=timeout)
        return {"serverTime": int(decimal(raw["timestamp"], positive=True))}

    @staticmethod
    def enabled(row):
        return (
            row.get("status") == "online"
            and row.get("is_api_trading_available") is True
            and row.get("quote_ccy") == "USDT"
        )

    def markets(self, *, timeout=None, total_timeout=None):
        data = rows(
            self.send(
                "GET", "/spot/market", timeout=timeout, total_timeout=total_timeout
            )
        )
        return {validate_symbol(v["market"]): v for v in data if self.enabled(v)}

    def market(self, value, *, timeout=None, total_timeout=None):
        data = rows(
            self.send(
                "GET",
                "/spot/market",
                params={"market": validate_symbol(value)},
                timeout=timeout,
                total_timeout=total_timeout,
            )
        )
        return next((r for r in data if r.get("market") == value), None)

    def symbol_rules(self, symbol, *, timeout=None, total_timeout=None):
        budget = self.budget(timeout, total_timeout)
        row = self.market(symbol, total_timeout=budget())
        if (
            row is None
            or not self.enabled(row)
            or row.get("base_ccy") + "USDT" != symbol
        ):
            raise SpotError("CoinEx Spot market unavailable")
        book = self.order_book(symbol, total_timeout=budget())
        if not book.asks:
            raise SpotError("CoinEx market has no ask price")
        self._native_rules = {symbol: row}
        return SymbolRules(
            validate_symbol(symbol),
            row["base_ccy"],
            "USDT",
            precision_step(row["base_ccy_precision"]),
            precision_step(row["quote_ccy_precision"]),
            decimal(row["min_amount"], positive=True) * book.asks[0].price,
            None,
            ("LIMIT",),
            1,
        )

    def validate_native(self, value, quantity, price, side):
        if quantity < decimal(self._native_rules[value]["min_amount"], positive=True):
            raise ValueError("CoinEx minimum base quantity not met")

    def order_book(self, symbol, *, limit=100, timeout=None, total_timeout=None):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid CoinEx depth limit")
        observed = self.clock_ms()
        data = self.send(
            "GET",
            "/spot/depth",
            params={
                "market": validate_symbol(symbol),
                "limit": next(v for v in (5, 10, 20, 50, 100) if v >= limit),
                "interval": "0",
            },
            timeout=timeout,
            total_timeout=total_timeout,
        )
        return self.book(data["depth"], observed)

    def ticker_24h(self, symbol, *, timeout=None, total_timeout=None):
        data = rows(
            self.send(
                "GET",
                "/spot/ticker",
                params={"market": validate_symbol(symbol)},
                timeout=timeout,
                total_timeout=total_timeout,
            )
        )
        row = next((r for r in data if r.get("market") == symbol), None)
        if row is None:
            raise SpotError("CoinEx ticker unavailable")
        return {"volume": text(row["volume"]), "last": text(row["last"], positive=True)}

    def account(self, *, timeout=None, total_timeout=None):
        data = rows(
            self.signed(
                "GET",
                "/assets/spot/balance",
                timeout=timeout,
                total_timeout=total_timeout,
            )
        )
        return {
            "accountType": "SPOT",
            "canTrade": True,
            "balances": [
                {
                    "asset": r["ccy"],
                    "free": text(r["available"]),
                    "locked": text(r["frozen"]),
                }
                for r in data
            ],
        }

    def trade_fee(self, symbol):
        row = self.signed(
            "GET",
            "/account/trade-fee-rate",
            {"market_type": "SPOT", "market": validate_symbol(symbol)},
        )
        return {
            "symbol": symbol,
            "makerCommission": text(row["maker_rate"], signed=True),
            "takerCommission": text(row["taker_rate"]),
        }

    def normalize(self, row, *, local=None):
        if row.get("market_type") != "SPOT" or row.get("type") != "limit":
            raise SpotError("Non-Spot CoinEx order rejected")
        market = validate_symbol(row["market"])
        native = row.get("client_id", "")
        if local is not None and native != client_id(local):
            raise SpotError("CoinEx order identity mismatch")
        state = {
            "open": "NEW",
            "unfilled": "NEW",
            "part_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "part_canceled": "CANCELED",
        }.get(row.get("status"), "UNKNOWN")
        return order(
            row,
            oid=row["order_id"],
            cid=local or self.state.local(native, market),
            market=market,
            side=row["side"].upper(),
            state=state,
            quantity=row["amount"],
            executed=row["filled_amount"],
            quote=row["filled_value"],
            price=row["price"],
        )

    def open_orders(self, symbol=None):
        params = {"market_type": "SPOT", "limit": 100, "page": 1}
        if symbol:
            params["market"] = validate_symbol(symbol)
        # Fail rather than silently omit open orders beyond the supported bound.
        data = rows(self.signed("GET", "/spot/pending-order", params))
        if len(data) >= 100:
            raise SpotError("CoinEx open-order bound exceeded")
        return [
            self.normalize(
                {
                    **r,
                    "status": (
                        "part_filled" if decimal(r["filled_amount"]) else "unfilled"
                    ),
                }
            )
            for r in data
            if r.get("market_type") == "SPOT"
            and r.get("type") == "limit"
            and str(r.get("market", "")).endswith("USDT")
        ]

    def account_trades(
        self, *, symbol, order_id=None, start_time_ms=None, end_time_ms=None, limit=100
    ):
        validate_symbol(symbol)
        if not 1 <= limit <= 100:
            raise ValueError("Invalid fill limit")
        params = {
            "market": symbol,
            "market_type": "SPOT",
            "limit": min(limit, 100),
            "page": 1,
        }
        if start_time_ms is not None:
            params["start_time"] = start_time_ms
        if end_time_ms is not None:
            params["end_time"] = end_time_ms
        if order_id:
            params["order_id"] = int(order_id)
            path = "/spot/order-deals"
        else:
            path = "/spot/user-deals"
        data = rows(self.signed("GET", path, params))
        if order_id and self._has_next:
            raise SpotError("CoinEx order fill bound exceeded")
        return [
            dict(
                id=str(r["deal_id"]),
                orderId=str(r["order_id"]),
                symbol=symbol,
                price=text(r["price"], positive=True),
                qty=text(r["amount"], positive=True),
                quoteQty=text(decimal(r["amount"]) * decimal(r["price"])),
                commission=text(r["fee"], signed=True),
                commissionAsset=r["fee_ccy"],
                time=int(r["created_at"]),
            )
            for r in data
            if r.get("market") == symbol
            and not r.get("margin_market")
            and (not order_id or str(r["order_id"]) == str(order_id))
        ]

    def place_limit_order(self, *, symbol, side, quantity, price, client_order_id):
        self.write_gate()
        self.test_limit_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            client_order_id=client_order_id,
        )
        native = self.state.begin(client_order_id, symbol)
        row = self.signed(
            "POST",
            "/spot/order",
            body={
                "market": symbol,
                "market_type": "SPOT",
                "type": "limit",
                "side": side.value.lower(),
                "amount": text(quantity, positive=True),
                "price": text(price, positive=True),
                "client_id": native,
            },
            write=True,
        )
        if row.get("client_id") != native or row.get("market") != symbol:
            raise SpotError("CoinEx order identity mismatch", execution_unknown=True)
        self.state.bind(client_order_id, symbol, row["order_id"])
        # Creation reply does not carry a final status; never invent NEW/FILLED.
        return self.query_order(symbol=symbol, client_order_id=client_order_id)

    def query_order(self, *, symbol, client_order_id):
        validate_symbol(symbol)
        record = self.state.get(client_order_id, symbol)
        if record and record.get("final"):
            return record["final"]
        if record and record.get("order_id"):
            row = self.signed(
                "GET",
                "/spot/order-status",
                {"market": symbol, "order_id": int(record["order_id"])},
            )
            result = self.normalize(row, local=client_order_id)
            if result["symbol"] != symbol or result["orderId"] != record["order_id"]:
                raise SpotError("CoinEx order identity mismatch")
            self.state.final(client_order_id, symbol, result)
            return result
        # An uncertain submit has no exchange id. Search both open and finished
        # records, retaining UNKNOWN when a bounded lookup cannot locate it.
        budget = self.budget()
        for path in ("/spot/pending-order", "/spot/finished-order"):
            for page in range(1, 6):
                data = rows(
                    self.signed(
                        "GET",
                        path,
                        {
                            "market": symbol,
                            "market_type": "SPOT",
                            "page": page,
                            "limit": 100,
                        },
                        total_timeout=budget(),
                    )
                )
                found = [
                    r for r in data if r.get("client_id") == client_id(client_order_id)
                ]
                if len(found) > 1:
                    raise SpotError("CoinEx ambiguous client identifier")
                if found:
                    self.state.bind(client_order_id, symbol, found[0]["order_id"])
                    row = self.signed(
                        "GET",
                        "/spot/order-status",
                        {"market": symbol, "order_id": int(found[0]["order_id"])},
                        total_timeout=budget(),
                    )
                    return self.normalize(row, local=client_order_id)
                if len(data) < 100:
                    break
        raise SpotError("CoinEx order unresolved; manual reconciliation required")

    def cancel_order(self, *, symbol, client_order_id):
        self.write_gate()
        current = self.query_order(symbol=symbol, client_order_id=client_order_id)
        if current["status"] in ("FILLED", "CANCELED"):
            return current
        row = self.signed(
            "POST",
            "/spot/cancel-order",
            body={
                "market": symbol,
                "market_type": "SPOT",
                "order_id": int(current["orderId"]),
            },
            write=True,
        )
        # CoinEx deletes canceled orders with no fills; cancellation's confirmed
        # response is authoritative, including final quantities.
        result = self.normalize(
            {
                **row,
                "status": (
                    "filled"
                    if decimal(row["filled_amount"]) == decimal(row["amount"])
                    else "canceled"
                ),
            },
            local=client_order_id,
        )
        if result["symbol"] != symbol or result["orderId"] != current["orderId"]:
            raise SpotError(
                "CoinEx cancellation identity mismatch", execution_unknown=True
            )
        self.state.final(client_order_id, symbol, result)
        return result


def create_client(config, *, state_dir=None, **kwargs):
    return bounded_client(CoinEx(config, state_dir=state_dir, **kwargs))
