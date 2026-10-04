"""Experimental Poloniex Spot REST adapter, HMAC API keys only."""

import base64
import hashlib
import hmac
import json
from urllib.parse import quote, urlencode

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


class Poloniex(Base):
    def signed(self, method, path, params=None, *, body=None, write=False, **budgets):
        self.credentials()
        timestamp = str(self.clock_ms() + self.offset)
        payload = (
            json.dumps(body, separators=(",", ":")).encode()
            if body is not None
            else None
        )
        signing = {"signTimestamp": timestamp}
        if payload is not None:
            signing["requestBody"] = payload.decode()
        else:
            signing.update(params or {})
        # JSON bodies are signed verbatim, as the official signature example specifies.
        signing_text = (
            ("requestBody=" + payload.decode() + "&signTimestamp=" + timestamp)
            if payload is not None
            else urlencode(sorted(signing.items()), quote_via=quote)
        )
        canonical_request = method + "\n" + path + "\n" + signing_text
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode(), canonical_request.encode(), hashlib.sha256
            ).digest()
        ).decode()
        headers = {
            "key": self.api_key,
            "signTimestamp": timestamp,
            "signature": signature,
            "signatureMethod": "HmacSHA256",
            "signatureVersion": "1",
            "recvWindow": str(self.config["recv_window_ms"]),
            "Content-Type": "application/json",
        }
        return self.send(
            method,
            path,
            params=params,
            body=payload,
            headers=headers,
            write=write,
            **budgets
        )

    def server_time(self, *, timeout=None):
        row = self.send("GET", "/timestamp", timeout=timeout, total_timeout=timeout)
        return {"serverTime": int(decimal(row["serverTime"], positive=True))}

    @staticmethod
    def enabled(row):
        return row.get("state") == "NORMAL" and row.get("quoteCurrencyName") == "USDT"

    def markets(self, *, timeout=None, total_timeout=None):
        return {
            canonical(r["symbol"]): r
            for r in rows(
                self.send(
                    "GET", "/markets", timeout=timeout, total_timeout=total_timeout
                )
            )
            if self.enabled(r)
        }

    def market(self, value, *, timeout=None, total_timeout=None):
        raw = self.send(
            "GET",
            "/markets/" + pair(value),
            timeout=timeout,
            total_timeout=total_timeout,
        )
        if isinstance(raw, list):
            raw = next((r for r in rows(raw) if r.get("symbol") == pair(value)), None)
        if raw is not None and (
            not isinstance(raw, dict) or raw.get("symbol") != pair(value)
        ):
            raise SpotError("Poloniex market identity mismatch")
        return raw

    def symbol_rules(self, symbol, *, timeout=None, total_timeout=None):
        r = self.market(symbol, timeout=timeout, total_timeout=total_timeout)
        if (
            r is None
            or not self.enabled(r)
            or r.get("baseCurrencyName") + "USDT" != symbol
        ):
            raise SpotError("Poloniex market unavailable")
        rules = r["symbolTradeLimit"]
        self._native_rules = {symbol: rules}
        max_amount = decimal(rules["maxAmount"])
        return SymbolRules(
            validate_symbol(symbol),
            r["baseCurrencyName"],
            "USDT",
            precision_step(rules["quantityScale"]),
            precision_step(rules["priceScale"]),
            decimal(rules["minAmount"], positive=True),
            max_amount or None,
            ("LIMIT",),
            1,
        )

    def validate_native(self, value, quantity, price, side):
        r = self._native_rules[value]
        maximum = decimal(r["maxQuantity"])
        if quantity < decimal(r["minQuantity"]) or (maximum and quantity > maximum):
            raise ValueError("Poloniex quantity bound exceeded")
        if (
            side.value == "BUY"
            and decimal(r.get("highestBid", 0))
            and price > decimal(r["highestBid"])
        ):
            raise ValueError("Poloniex price bound exceeded")
        if (
            side.value == "SELL"
            and decimal(r.get("lowestAsk", 0))
            and price < decimal(r["lowestAsk"])
        ):
            raise ValueError("Poloniex price bound exceeded")

    def order_book(self, symbol, *, limit=100, timeout=None, total_timeout=None):
        if not 1 <= limit <= 150:
            raise ValueError("Invalid Poloniex depth limit")
        observed = self.clock_ms()
        raw = self.send(
            "GET",
            "/markets/" + pair(symbol) + "/orderBook",
            params={"limit": next(v for v in (5, 10, 20, 50, 100, 150) if v >= limit)},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        data = {}
        for side in ("bids", "asks"):
            values = raw[side]
            if not isinstance(values, list) or len(values) % 2:
                raise SpotError("Invalid Poloniex depth response")
            data[side] = list(zip(values[::2], values[1::2]))
        return self.book(data, observed)

    def ticker_24h(self, symbol, *, timeout=None, total_timeout=None):
        raw = self.send(
            "GET",
            "/markets/" + pair(symbol) + "/ticker24h",
            timeout=timeout,
            total_timeout=total_timeout,
        )
        if raw.get("symbol") != pair(symbol):
            raise SpotError("Poloniex ticker identity mismatch")
        return {
            "volume": text(raw["quantity"]),
            "last": text(raw["close"], positive=True),
        }

    def account(self, *, timeout=None, total_timeout=None):
        budget = self.budget(timeout, total_timeout)
        info = rows(self.signed("GET", "/accounts", total_timeout=budget()))
        available = {
            str(r["accountId"])
            for r in info
            if r.get("accountType") == "SPOT" and r.get("accountState") == "NORMAL"
        }
        accounts = rows(
            self.signed(
                "GET",
                "/accounts/balances",
                {"accountType": "SPOT"},
                total_timeout=budget(),
            )
        )
        balances = []
        for account in accounts:
            if (
                account.get("accountType") != "SPOT"
                or str(account["accountId"]) not in available
            ):
                continue
            for r in rows(account["balances"]):
                balances.append(
                    {
                        "asset": r["currency"],
                        "free": text(r["available"]),
                        "locked": text(r["hold"]),
                    }
                )
        return {
            "accountType": "SPOT",
            "canTrade": bool(available),
            "balances": balances,
        }

    def trade_fee(self, symbol):
        validate_symbol(symbol)
        r = self.signed("GET", "/feeinfo")
        match = next(
            (
                v
                for v in rows(r.get("specialFeeRates", []))
                if v.get("symbol") == pair(symbol)
            ),
            r,
        )
        return {
            "symbol": symbol,
            "makerCommission": text(match["makerRate"], signed=True),
            "takerCommission": text(match["takerRate"]),
        }

    def normalize(self, r, *, local=None):
        if (
            r.get("accountType") != "SPOT"
            or r.get("loan") is not False
            or r.get("type") not in ("LIMIT", "LIMIT_MAKER")
        ):
            raise SpotError("Non-cash Spot order rejected")
        market = canonical(r["symbol"])
        native = r.get("clientOrderId", "")
        if local is not None and native != client_id(local):
            raise SpotError("Poloniex order identity mismatch")
        state = {"PARTIALLY_CANCELED": "CANCELED", "FAILED": "REJECTED"}.get(
            r["state"], r["state"]
        )
        return order(
            r,
            oid=r["id"],
            cid=local or self.state.local(native, market),
            market=market,
            side=r["side"],
            state=state,
            quantity=r["quantity"],
            executed=r["filledQuantity"],
            quote=r["filledAmount"],
            price=r["price"],
        )

    def open_orders(self, symbol=None):
        params = {"limit": 2000}
        if symbol:
            params["symbol"] = pair(symbol)
        data = rows(self.signed("GET", "/orders", params))
        if len(data) >= 2000:
            raise SpotError("Poloniex open-order bound exceeded")
        return [
            self.normalize(r)
            for r in data
            if r.get("accountType") == "SPOT"
            and r.get("loan") is False
            and r.get("type") in ("LIMIT", "LIMIT_MAKER")
            and str(r.get("symbol", "")).endswith("_USDT")
        ]

    def account_trades(
        self, *, symbol, order_id=None, start_time_ms=None, end_time_ms=None, limit=100
    ):
        validate_symbol(symbol)
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid fill limit")
        if order_id is not None:
            if not str(order_id).isdigit():
                raise ValueError("Invalid exchange order id")
            data = rows(self.signed("GET", "/orders/" + str(order_id) + "/trades"))
            if len(data) > limit:
                raise SpotError("Poloniex order fill bound exceeded")
        else:
            params = {"symbols": pair(symbol), "limit": limit}
            if start_time_ms is not None:
                params["startTime"] = start_time_ms
            if end_time_ms is not None:
                params["endTime"] = end_time_ms
            data = rows(self.signed("GET", "/trades", params))
        return [
            dict(
                id=str(r["id"]),
                orderId=str(r["orderId"]),
                symbol=symbol,
                price=text(r["price"], positive=True),
                qty=text(r["quantity"], positive=True),
                quoteQty=text(r["amount"]),
                commission=text(r["feeAmount"], signed=True),
                commissionAsset=r["feeCurrency"],
                time=int(r["createTime"]),
            )
            for r in data
            if r.get("accountType") == "SPOT"
            and r.get("symbol") == pair(symbol)
            and (order_id is None or str(r["orderId"]) == str(order_id))
            and (start_time_ms is None or int(r["createTime"]) >= start_time_ms)
            and (end_time_ms is None or int(r["createTime"]) <= end_time_ms)
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
        r = self.signed(
            "POST",
            "/orders",
            body={
                "symbol": pair(symbol),
                "side": side.value,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "accountType": "SPOT",
                "allowBorrow": False,
                "quantity": text(quantity, positive=True),
                "price": text(price, positive=True),
                "clientOrderId": native,
            },
            write=True,
        )
        if r.get("clientOrderId") != native:
            raise SpotError("Poloniex order identity mismatch", execution_unknown=True)
        self.state.bind(client_order_id, symbol, r["id"])
        return self.query_order(symbol=symbol, client_order_id=client_order_id)

    def query_order(self, *, symbol, client_order_id):
        r = self.signed("GET", "/orders/cid:" + client_id(client_order_id))
        result = self.normalize(r, local=client_order_id)
        if result["symbol"] != symbol:
            raise SpotError("Poloniex order symbol mismatch")
        self.state.bind(client_order_id, symbol, r["id"])
        return result

    def cancel_order(self, *, symbol, client_order_id):
        self.write_gate()
        # Check the symbol before canceling; the cancel acknowledgment is only
        # PENDING_CANCEL, never evidence of a terminal result or zero execution.
        self.query_order(symbol=symbol, client_order_id=client_order_id)
        self.signed("DELETE", "/orders/cid:" + client_id(client_order_id), write=True)
        return self.query_order(symbol=symbol, client_order_id=client_order_id)


def create_client(config, *, state_dir=None, **kwargs):
    return bounded_client(Poloniex(config, state_dir=state_dir, **kwargs))
