"""Experimental Binance.com HMAC cash Spot adapter; not account-tested."""

import hashlib
import hmac
from decimal import Decimal
from urllib.parse import urlencode

from .metadata_http import MetadataTransport
from .support import (
    Base,
    bounded_client,
    client_id,
    decimal,
    order,
    rows,
    symbol as valid_symbol,
    text,
)
from .types import SpotError, SymbolRules


class Binance(Base):
    def __init__(self, config, **kwargs):
        kwargs.setdefault("transport", MetadataTransport())
        super().__init__(config, **kwargs)
        self._native_rules = {}

    def signed(self, method, path, params=None, *, write=False, **budgets):
        self.credentials()
        payload = dict(params or {})
        payload.update(
            timestamp=self.clock_ms() + self.offset,
            recvWindow=self.config["recv_window_ms"],
        )
        encoded = urlencode(payload)
        payload["signature"] = hmac.new(
            self.api_secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        return self.send(
            method,
            path,
            params=payload,
            headers={"X-MBX-APIKEY": self.api_key},
            write=write,
            **budgets
        )

    def server_time(self, *, timeout=None):
        data = self.send("GET", "/api/v3/time", timeout=timeout, total_timeout=timeout)
        return {"serverTime": int(decimal(data["serverTime"], positive=True))}

    @staticmethod
    def enabled(row):
        return (
            row.get("status") == "TRADING"
            and row.get("isSpotTradingAllowed") is True
            and row.get("quoteAsset") == "USDT"
            and "LIMIT" in row.get("orderTypes", ())
        )

    def markets(self, *, timeout=None, total_timeout=None):
        data = self.send(
            "GET",
            "/api/v3/exchangeInfo",
            params={"permissions": "SPOT"},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        return {
            valid_symbol(r["symbol"]): r
            for r in rows(data["symbols"])
            if self.enabled(r)
        }

    def market(self, value, *, timeout=None, total_timeout=None):
        valid_symbol(value)
        data = self.send(
            "GET",
            "/api/v3/exchangeInfo",
            params={"symbol": value},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        found = rows(data["symbols"])
        if len(found) != 1 or found[0].get("symbol") != value:
            raise SpotError("Binance market identity mismatch")
        return found[0]

    def symbol_rules(self, symbol, *, timeout=None, total_timeout=None):
        row = self.market(symbol, timeout=timeout, total_timeout=total_timeout)
        if not self.enabled(row) or row["baseAsset"] + "USDT" != symbol:
            raise SpotError("Binance cash Spot market unavailable")
        filters = {v["filterType"]: v for v in rows(row["filters"])}
        price, lot = filters["PRICE_FILTER"], filters["LOT_SIZE"]
        floors = [
            decimal(filters[k]["minNotional"])
            for k in ("MIN_NOTIONAL", "NOTIONAL")
            if k in filters
        ]
        ceiling = decimal(filters.get("NOTIONAL", {}).get("maxNotional", "0"))
        minimum = max(floors, default=Decimal(0))
        if minimum <= 0:
            raise SpotError("Binance positive notional rule required by Spot v1")
        self._native_rules[symbol] = filters
        return SymbolRules(
            valid_symbol(symbol),
            row["baseAsset"],
            "USDT",
            decimal(lot["stepSize"], positive=True),
            decimal(price["tickSize"], positive=True),
            minimum,
            ceiling if ceiling else None,
            ("LIMIT",),
            1,
        )

    def validate_native(self, symbol, quantity, price, side):
        filters = self._native_rules[symbol]
        for value, limits, keys in (
            (quantity, filters["LOT_SIZE"], ("minQty", "maxQty")),
            (price, filters["PRICE_FILTER"], ("minPrice", "maxPrice")),
        ):
            low, high = (decimal(limits[k]) for k in keys)
            if (low and value < low) or (high and value > high):
                raise ValueError("Binance native quantity/price bound violated")

    def order_book(self, symbol, *, limit=100, timeout=None, total_timeout=None):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid Binance book limit")
        observed = self.clock_ms()
        data = self.send(
            "GET",
            "/api/v3/depth",
            params={"symbol": valid_symbol(symbol), "limit": limit},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        return self.book(data, observed)

    def ticker_24h(self, symbol, *, timeout=None, total_timeout=None):
        row = self.send(
            "GET",
            "/api/v3/ticker/24hr",
            params={"symbol": valid_symbol(symbol)},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        if row.get("symbol") != symbol:
            raise SpotError("Binance ticker identity mismatch")
        return {
            "volume": text(row["volume"]),
            "last": text(row["lastPrice"], positive=True),
        }

    def account(self, *, timeout=None, total_timeout=None):
        row = self.signed(
            "GET", "/api/v3/account", timeout=timeout, total_timeout=total_timeout
        )
        if row.get("accountType") != "SPOT" or type(row.get("canTrade")) is not bool:
            raise SpotError("Binance cash Spot account required")
        return {
            "accountType": "SPOT",
            "canTrade": row["canTrade"],
            "balances": [
                {
                    "asset": r["asset"],
                    "free": text(r["free"]),
                    "locked": text(r["locked"]),
                }
                for r in rows(row["balances"])
            ],
        }

    def trade_fee(self, symbol):
        row = self.signed(
            "GET", "/api/v3/account/commission", {"symbol": valid_symbol(symbol)}
        )
        if row.get("symbol") != symbol:
            raise SpotError("Binance commission identity mismatch")
        result = {"symbol": symbol}
        # Common v1 has no per-side commission. Use the worst buy/sell rate,
        # including special and tax components. Do not assume a BNB discount.
        for kind in ("maker", "taker"):
            rate = Decimal(0)
            for key in ("standardCommission", "specialCommission", "taxCommission"):
                part = row[key]
                rate += decimal(part[kind]) + max(
                    decimal(part["buyer"]), decimal(part["seller"])
                )
            if rate >= 1:
                raise SpotError("Unsupported Binance commission")
            result[kind + "Commission"] = text(rate)
        return result

    def normalize(self, row, *, local=None, native=None, oid=None):
        if row.get("type") != "LIMIT" or row.get("timeInForce") != "GTC":
            raise SpotError("Unsupported Binance Spot order")
        market = valid_symbol(row["symbol"])
        if local is not None:
            if oid is not None:
                if str(row["orderId"]) != str(oid):
                    raise SpotError("Binance exchange order identity mismatch")
            elif row.get("clientOrderId") != (native or client_id(local)):
                raise SpotError("Binance client order identity mismatch")
        result = order(
            row,
            oid=row["orderId"],
            cid=local or self.state.local(row["clientOrderId"], market),
            market=market,
            side=row["side"],
            state={"EXPIRED_IN_MATCH": "EXPIRED"}.get(row["status"], row["status"]),
            quantity=row["origQty"],
            executed=row["executedQty"],
            quote=row["cummulativeQuoteQty"],
            price=row["price"],
        )
        return result

    def open_orders(self, symbol=None):
        params = {"symbol": valid_symbol(symbol)} if symbol else {}
        data = rows(self.signed("GET", "/api/v3/openOrders", params))
        if len(data) > 1000:
            raise SpotError("Binance open-order bound exceeded")
        return [
            self.normalize(r)
            for r in data
            if r.get("type") == "LIMIT"
            and r.get("timeInForce") == "GTC"
            and str(r.get("symbol", "")).endswith("USDT")
        ]

    def account_trades(
        self, *, symbol, order_id=None, start_time_ms=None, end_time_ms=None, limit=100
    ):
        valid_symbol(symbol)
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid Binance fill limit")
        params = {"symbol": symbol, "limit": limit}
        if order_id is not None:
            params["orderId"] = int(order_id)
        else:
            for key, value in (("startTime", start_time_ms), ("endTime", end_time_ms)):
                if value is not None:
                    params[key] = int(value)
            if (
                start_time_ms is not None
                and end_time_ms is not None
                and not 0 <= end_time_ms - start_time_ms <= 86400000
            ):
                raise ValueError("Binance trade window must be at most 24 hours")
        data = rows(self.signed("GET", "/api/v3/myTrades", params))
        # OrderId cannot be combined with a time window. Filter locally instead.
        # A full page is not evidence that every fill of that order was returned.
        if order_id is not None and len(data) >= limit:
            raise SpotError("Binance order fill bound exceeded")
        result = []
        for r in data:
            if r.get("symbol") != symbol or (
                order_id is not None and str(r["orderId"]) != str(order_id)
            ):
                raise SpotError("Binance fill identity mismatch")
            stamp = int(r["time"])
            if (start_time_ms is not None and stamp < start_time_ms) or (
                end_time_ms is not None and stamp > end_time_ms
            ):
                continue
            result.append(
                dict(
                    id=str(r["id"]),
                    orderId=str(r["orderId"]),
                    symbol=symbol,
                    price=text(r["price"], positive=True),
                    qty=text(r["qty"], positive=True),
                    quoteQty=text(r["quoteQty"]),
                    commission=text(r["commission"]),
                    commissionAsset=r["commissionAsset"],
                    time=stamp,
                )
            )
        return result

    @staticmethod
    def parameters(symbol, side, quantity, price, local):
        return {
            "symbol": valid_symbol(symbol),
            "side": side.value,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": text(quantity, positive=True),
            "price": text(price, positive=True),
            "newClientOrderId": client_id(local),
        }

    def test_limit_order(self, **kwargs):
        super().test_limit_order(**kwargs)
        row = self.signed(
            "POST",
            "/api/v3/order/test",
            self.parameters(
                kwargs["symbol"],
                kwargs["side"],
                kwargs["quantity"],
                kwargs["price"],
                kwargs["client_order_id"],
            ),
        )
        if row != {}:
            raise SpotError("Unexpected Binance validation response")
        return {
            "validated": True,
            "request_sent": True,
            "funded_order": False,
            "experimental": True,
            "live_account_tested": False,
        }

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
        params = self.parameters(symbol, side, quantity, price, client_order_id)
        params["newOrderRespType"] = "RESULT"
        row = self.signed("POST", "/api/v3/order", params, write=True)
        result = self.normalize(row, local=client_order_id, native=native)
        if result["symbol"] != symbol:
            raise SpotError("Binance order market mismatch", execution_unknown=True)
        self.state.bind(client_order_id, symbol, result["orderId"])
        self.state.final(client_order_id, symbol, result)
        return result

    def query_order(self, *, symbol, client_order_id):
        valid_symbol(symbol)
        record = self.state.get(client_order_id, symbol)
        if not record:
            raise SpotError(
                "Binance order not journaled; manual reconciliation required"
            )
        if record.get("final"):
            return record["final"]
        params = {"symbol": symbol}
        oid = record.get("order_id")
        params["orderId" if oid else "origClientOrderId"] = (
            int(oid) if oid else client_id(client_order_id)
        )
        row = self.signed("GET", "/api/v3/order", params)
        result = self.normalize(row, local=client_order_id, oid=oid)
        if result["symbol"] != symbol:
            raise SpotError("Binance queried market mismatch")
        self.state.bind(client_order_id, symbol, result["orderId"])
        self.state.final(client_order_id, symbol, result)
        return result

    def cancel_order(self, *, symbol, client_order_id):
        self.write_gate()
        current = self.query_order(symbol=symbol, client_order_id=client_order_id)
        if current["status"] in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
            return current
        row = self.signed(
            "DELETE",
            "/api/v3/order",
            {"symbol": symbol, "orderId": int(current["orderId"])},
            write=True,
        )
        # Cancellation may replace clientOrderId. The bound exchange ID remains
        # authoritative, including on recovery after a lost cancel response.
        if row.get("origClientOrderId") != client_id(client_order_id):
            raise SpotError(
                "Binance cancellation client identity mismatch", execution_unknown=True
            )
        result = self.normalize(row, local=client_order_id, oid=current["orderId"])
        if result["symbol"] != symbol:
            raise SpotError(
                "Binance cancellation market mismatch", execution_unknown=True
            )
        self.state.final(client_order_id, symbol, result)
        return result


def create_client(config, *, state_dir=None, **kwargs):
    return bounded_client(Binance(config, state_dir=state_dir, **kwargs))
