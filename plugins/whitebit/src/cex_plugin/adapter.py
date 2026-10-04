"""Experimental WhiteBIT V4 cash Spot adapter; no collateral products."""

import base64
import hashlib
import hmac
import json

from .support import (
    Base,
    bounded_client,
    precision_step,
    canonical,
    client_id,
    decimal,
    order,
    pair,
    rows,
    symbol as validate_symbol,
    text,
)
from .types import SpotError, SymbolRules


class WhiteBIT(Base):
    def unwrap(self, raw, *, write=False):
        super().unwrap(raw, write=write)
        if isinstance(raw, dict) and raw.get("error"):
            raise SpotError("WhiteBIT rejected request", execution_unknown=write)
        return raw

    def signed(self, path, params=None, *, write=False, **budgets):
        self.credentials()
        # Serialize the complete signed request across plugin processes using
        # the same key, so arrival order cannot reverse increasing nonces.
        with self.state.locked() as data:
            nonce = max(int(data["nonce"]) + 1, self.clock_ms() + self.offset)
            if nonce > 9007199254740991:
                raise SpotError("WhiteBIT nonce bound exceeded")
            data["nonce"] = nonce
            # Persist BEFORE sending: a killed process must not reuse this nonce.
            self.state.save(data)
            payload = json.dumps(
                {**(params or {}), "request": path, "nonce": nonce},
                separators=(",", ":"),
            ).encode()
            encoded = base64.b64encode(payload).decode()
            signature = hmac.new(
                self.api_secret.encode(), encoded.encode(), hashlib.sha512
            ).hexdigest()
            return self.send(
                "POST",
                path,
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-TXC-APIKEY": self.api_key,
                    "X-TXC-PAYLOAD": encoded,
                    "X-TXC-SIGNATURE": signature,
                },
                write=write,
                **budgets
            )

    def server_time(self, *, timeout=None):
        raw = self.send(
            "GET", "/api/v4/public/time", timeout=timeout, total_timeout=timeout
        )
        return {"serverTime": int(decimal(raw["time"], positive=True) * 1000)}

    @staticmethod
    def enabled(r):
        return (
            r.get("type") == "spot"
            and r.get("tradesEnabled") is True
            and r.get("money") == "USDT"
        )

    def markets(self, *, timeout=None, total_timeout=None):
        return {
            canonical(r["name"]): r
            for r in rows(
                self.send(
                    "GET",
                    "/api/v4/public/markets",
                    timeout=timeout,
                    total_timeout=total_timeout,
                )
            )
            if self.enabled(r)
        }

    def market(self, value, *, timeout=None, total_timeout=None):
        return self.markets(timeout=timeout, total_timeout=total_timeout).get(
            validate_symbol(value)
        )

    def symbol_rules(self, symbol, *, timeout=None, total_timeout=None):
        r = self.market(symbol, timeout=timeout, total_timeout=total_timeout)
        if r is not None and r.get("stock") + "USDT" != symbol:
            raise SpotError("WhiteBIT market identity mismatch")
        if r is None:
            raise SpotError("WhiteBIT Spot market unavailable")
        # Do not infer a price grid from quote-currency accounting precision.
        # Since September 2026 the explicit native grids govern order acceptance.
        self._native_rules = {symbol: r}
        maximum = decimal(r["maxTotal"])
        return SymbolRules(
            validate_symbol(symbol),
            r["stock"],
            "USDT",
            decimal(r["stepSize"], positive=True),
            decimal(r["tickSize"], positive=True),
            decimal(r["minTotal"], positive=True),
            maximum or None,
            ("LIMIT",),
            1,
        )

    def validate_native(self, value, quantity, price, side):
        if quantity < decimal(self._native_rules[value]["minAmount"], positive=True):
            raise ValueError("WhiteBIT minimum base quantity not met")

    def order_book(self, symbol, *, limit=100, timeout=None, total_timeout=None):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid WhiteBIT depth limit")
        observed = self.clock_ms()
        raw = self.send(
            "GET",
            "/api/v4/public/orderbook/" + pair(symbol),
            params={"limit": limit},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        return self.book(raw, observed)

    def ticker_24h(self, symbol, *, timeout=None, total_timeout=None):
        data = self.send(
            "GET", "/api/v4/public/ticker", timeout=timeout, total_timeout=total_timeout
        )
        r = data.get(pair(symbol))
        if not isinstance(r, dict):
            raise SpotError("WhiteBIT ticker unavailable")
        return {
            "volume": text(r["base_volume"]),
            "last": text(r["last_price"], positive=True),
        }

    def account(self, *, timeout=None, total_timeout=None):
        data = self.signed(
            "/api/v4/trade-account/balance",
            timeout=timeout,
            total_timeout=total_timeout,
        )
        if not isinstance(data, dict):
            raise SpotError("Invalid WhiteBIT balance response")
        return {
            "accountType": "SPOT",
            "canTrade": True,
            "balances": [
                {
                    "asset": asset,
                    "free": text(r["available"]),
                    "locked": text(r["freeze"]),
                }
                for asset, r in data.items()
            ],
        }

    def trade_fee(self, symbol):
        r = self.signed("/api/v4/market/fee/single", {"market": pair(symbol)})
        return {
            "symbol": symbol,
            "makerCommission": text(
                decimal(r["maker"], signed=True) / 100, signed=True
            ),
            "takerCommission": text(decimal(r["taker"]) / 100),
        }

    def normalize(self, r, *, local=None):
        market = canonical(r["market"])
        native = r.get("clientOrderId", "")
        if r.get("type") != "limit" or r.get("positionSide") not in (None, "BOTH"):
            raise SpotError("Non-cash Spot order rejected")
        if local is not None and native != client_id(local):
            raise SpotError("WhiteBIT order identity mismatch")
        status = r.get("status", "UNKNOWN")
        if status == "PENDING":
            status = "NEW"
        if status in ("CANCELED_STP", "CANCELED_TAKER_BAND", "MAINTENANCE"):
            status = "CANCELED"
        return order(
            r,
            oid=r["orderId"],
            cid=local or self.state.local(native, market),
            market=market,
            side=r["side"].upper(),
            state=status,
            quantity=r["amount"],
            executed=r["dealStock"],
            quote=r["dealMoney"],
            price=r["price"],
        )

    def open_orders(self, symbol=None):
        params = {"limit": 100, "offset": 0}
        if symbol:
            params["market"] = pair(symbol)
        data = rows(self.signed("/api/v4/orders", params))
        if len(data) >= 100:
            raise SpotError("WhiteBIT open-order bound exceeded")
        # Same native list also contains collateral orders; only locally
        # journaled cash Spot orders belong to this adapter's order view.
        result = []
        for r in data:
            if (
                not str(r.get("market", "")).endswith("_USDT")
                or r.get("positionSide") not in (None, "BOTH")
                or r.get("type") != "limit"
            ):
                continue
            local = self.state.local(r.get("clientOrderId", ""), canonical(r["market"]))
            if local != r.get("clientOrderId", ""):
                result.append(self.normalize(r, local=local))
        return result

    def account_trades(
        self, *, symbol, order_id=None, start_time_ms=None, end_time_ms=None, limit=100
    ):
        validate_symbol(symbol)
        if not 1 <= limit <= 500:
            raise ValueError("Invalid fill limit")
        if order_id is not None:
            params = {"orderId": int(order_id), "limit": limit, "offset": 0}
            data = rows(self.signed("/api/v4/trade-account/order", params)["records"])
            if len(data) >= limit:
                raise SpotError("WhiteBIT order fill bound exceeded")
        else:
            params = {"market": pair(symbol), "limit": limit, "offset": 0}
            if start_time_ms is not None:
                params["startDate"] = start_time_ms // 1000
            if end_time_ms is not None:
                params["endDate"] = (end_time_ms + 999) // 1000
            data = rows(self.signed("/api/v4/trade-account/executed-history", params))
        result = []
        for r in data:
            stamp = int(decimal(r["time"]) * 1000)
            if (start_time_ms is not None and stamp < start_time_ms) or (
                end_time_ms is not None and stamp > end_time_ms
            ):
                continue
            # Unfiltered cash/collateral history shares a route. Only our
            # journaled cash orders qualify without a specific exchange id.
            if order_id is None and self.state.local(
                r.get("clientOrderId", ""), symbol
            ) == r.get("clientOrderId", ""):
                continue
            result.append(
                dict(
                    id=str(r["id"]),
                    orderId=str(order_id if order_id is not None else r["orderId"]),
                    symbol=symbol,
                    price=text(r["price"], positive=True),
                    qty=text(r["amount"], positive=True),
                    quoteQty=text(
                        r.get("deal", decimal(r["amount"]) * decimal(r["price"]))
                    ),
                    commission=text(r["fee"], signed=True),
                    commissionAsset=r["feeAsset"],
                    time=stamp,
                )
            )
        return result

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
        r = self.signed(
            "/api/v4/order/new",
            {
                "market": pair(symbol),
                "side": side.value.lower(),
                "amount": text(quantity, positive=True),
                "price": text(price, positive=True),
                "clientOrderId": native,
                "postOnly": False,
                "ioc": False,
            },
            write=True,
        )
        if r.get("clientOrderId") != native or r.get("market") != pair(symbol):
            raise SpotError("WhiteBIT order identity mismatch", execution_unknown=True)
        self.state.bind(client_order_id, symbol, r["orderId"])
        return self.normalize(r, local=client_order_id)

    def query_order(self, *, symbol, client_order_id):
        record = self.state.get(client_order_id, symbol)
        if record and record.get("final"):
            return record["final"]
        if record is None:
            raise SpotError("WhiteBIT cash order was not journaled")
        params = {
            "market": pair(symbol),
            "clientOrderId": client_id(client_order_id),
            "limit": 100,
            "offset": 0,
        }
        budget = self.budget()
        data = rows(self.signed("/api/v4/orders", params, total_timeout=budget()))
        matches = [
            r
            for r in data
            if r.get("clientOrderId") == params["clientOrderId"]
            and r.get("market") == params["market"]
        ]
        if not matches:
            history = self.signed(
                "/api/v4/trade-account/order/history", params, total_timeout=budget()
            )
            if not isinstance(history, dict):
                raise SpotError("Invalid WhiteBIT history response")
            matches = [
                {**r, "market": params["market"], "orderId": r["id"]}
                for r in rows(history.get(params["market"], []))
                if r.get("clientOrderId") == params["clientOrderId"]
            ]
        if len(matches) != 1:
            raise SpotError("WhiteBIT order unresolved; manual reconciliation required")
        r = matches[0]
        self.state.bind(client_order_id, symbol, r["orderId"])
        result = self.normalize(r, local=client_order_id)
        self.state.final(client_order_id, symbol, result)
        return result

    def cancel_order(self, *, symbol, client_order_id):
        self.write_gate()
        current = self.query_order(symbol=symbol, client_order_id=client_order_id)
        if current["status"] in ("FILLED", "CANCELED"):
            return current
        r = self.signed(
            "/api/v4/order/cancel",
            {"market": pair(symbol), "clientOrderId": client_id(client_order_id)},
            write=True,
        )
        result = self.normalize(r, local=client_order_id)
        if result["symbol"] != symbol or result["orderId"] != current["orderId"]:
            raise SpotError(
                "WhiteBIT cancellation identity mismatch", execution_unknown=True
            )
        self.state.final(client_order_id, symbol, result)
        return result


def create_client(config, *, state_dir=None, **kwargs):
    return bounded_client(WhiteBIT(config, state_dir=state_dir, **kwargs))
