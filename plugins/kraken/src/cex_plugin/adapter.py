"""Experimental Kraken REST cash Spot adapter; not account-tested."""

import base64
import hashlib
import hmac
import re
import time
from decimal import Decimal
from urllib.parse import urlencode

from .metadata_http import MetadataTransport
from .support import (
    Base,
    bounded_client,
    client_id,
    decimal,
    order,
    precision_step,
    symbol as valid_symbol,
    text,
)
from .types import SpotError, SymbolRules


def native_id(local):
    client_id(local)  # Common identifier validation; no non-unique userref.
    return hashlib.sha256(local.encode()).hexdigest()[:32]


def normalized_id(native):
    return str(native).lower().replace("-", "")


def asset_alias(value):
    return {"XBT": "BTC", "XDG": "DOGE"}.get(value, value)


def objects(value):
    if not isinstance(value, dict) or any(
        not isinstance(r, dict) for r in value.values()
    ):
        raise SpotError("Invalid Kraken object response")
    return value


class Kraken(Base):
    def __init__(self, config, **kwargs):
        kwargs.setdefault("transport", MetadataTransport())
        super().__init__(config, **kwargs)
        self._pairs, self._aliases, self._assets, self._native_rules = {}, {}, {}, {}
        self._assets_until = 0.0
        self._blocked_until = 0.0

    def unwrap(self, raw, *, write=False):
        if not isinstance(raw, dict) or not isinstance(raw.get("error"), list):
            raise SpotError("Invalid Kraken envelope", execution_unknown=write)
        if raw["error"]:
            if any(isinstance(e, str) and "Rate limit" in e for e in raw["error"]):
                self._blocked_until = time.monotonic() + 60
            # Do not propagate arbitrary exchange error text or credential data.
            raise SpotError("Kraken rejected request", execution_unknown=write)
        if "result" not in raw:
            raise SpotError("Kraken response missing result", execution_unknown=write)
        return raw["result"]

    def signed(self, endpoint, params=None, *, write=False, **budgets):
        self.credentials()
        if time.monotonic() < self._blocked_until:
            raise SpotError("Kraken private rate-limit cooldown", status=429)
        try:
            secret = base64.b64decode(self.api_secret, validate=True)
        except (ValueError, TypeError):
            raise SpotError("Kraken base64 API secret required") from None
        if not secret:
            raise SpotError("Kraken base64 API secret required")
        path = "/0/private/" + endpoint
        # Serialize signing + HTTP per key. Persist before send to survive kills.
        with self.state.locked() as data:
            nonce = max(data["nonce"] + 1, self.clock_ms() * 1000)
            if not 0 < nonce < 2**63:
                raise SpotError("Kraken nonce bound exceeded")
            data["nonce"] = nonce
            self.state.save(data)
            payload = urlencode({"nonce": nonce, **(params or {})}).encode()
            digest = hashlib.sha256(str(nonce).encode() + payload).digest()
            signature = base64.b64encode(
                hmac.new(secret, path.encode() + digest, hashlib.sha512).digest()
            ).decode()
            return self.send(
                "POST",
                path,
                body=payload,
                headers={
                    "API-Key": self.api_key,
                    "API-Sign": signature,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                write=write,
                **budgets
            )

    def server_time(self, *, timeout=None):
        raw = self.send("GET", "/0/public/Time", timeout=timeout, total_timeout=timeout)
        return {"serverTime": int(decimal(raw["unixtime"], positive=True) * 1000)}

    @staticmethod
    def enabled(row):
        return (
            row.get("status") == "online"
            and row.get("aclass_base") == "currency"
            and row.get("aclass_quote") == "currency"
            and row.get("lot") == "unit"
            and decimal(row.get("lot_multiplier", 1)) == 1
            and isinstance(row.get("wsname"), str)
            and row["wsname"].endswith("/USDT")
        )

    def remember(self, raw):
        result = {}
        for key, row in objects(raw).items():
            if not self.enabled(row) or key.endswith(".d"):
                continue
            parts = row["wsname"].split("/")
            if len(parts) != 2:
                continue
            value = asset_alias(parts[0]) + "USDT"
            if not re.fullmatch(r"[A-Z0-9]{1,28}USDT", value):
                continue
            valid_symbol(value)
            if value in result:
                raise SpotError("Ambiguous Kraken cash pair")
            copied = {**row, "native_pair": key, "canonical_pair": value}
            result[value] = copied
            for alias in (key, row["altname"], row["wsname"], value):
                previous = self._aliases.get(alias)
                if previous not in (None, value):
                    raise SpotError("Ambiguous Kraken pair alias")
                self._aliases[alias] = value
            self._pairs[value] = copied
        return result

    def markets(self, *, timeout=None, total_timeout=None):
        raw = self.send(
            "GET", "/0/public/AssetPairs", timeout=timeout, total_timeout=total_timeout
        )
        return self.remember(raw)

    def market(self, value, *, timeout=None, total_timeout=None):
        valid_symbol(value)
        base = {"BTC": "XBT", "DOGE": "XDG"}.get(value[:-4], value[:-4])
        raw = self.send(
            "GET",
            "/0/public/AssetPairs",
            params={"pair": base + "USDT"},
            timeout=timeout,
            total_timeout=total_timeout,
        )
        found = self.remember(raw)
        if len(found) != 1 or value not in found:
            raise SpotError("Kraken cash market unavailable")
        return found[value]

    def symbol_rules(self, symbol, *, timeout=None, total_timeout=None):
        row = self.market(symbol, timeout=timeout, total_timeout=total_timeout)
        self._native_rules[symbol] = row
        return SymbolRules(
            valid_symbol(symbol),
            symbol[:-4],
            "USDT",
            precision_step(row["lot_decimals"]),
            decimal(row["tick_size"], positive=True),
            decimal(row["costmin"], positive=True),
            None,
            ("LIMIT",),
            1,
        )

    def validate_native(self, symbol, quantity, price, side):
        if quantity < decimal(self._native_rules[symbol]["ordermin"], positive=True):
            raise ValueError("Kraken minimum base quantity not met")

    def pair_result(self, raw, symbol):
        data = objects(raw)
        if len(data) != 1:
            raise SpotError("Kraken response pair bound exceeded")
        key, row = next(iter(data.items()))
        if self._aliases.get(key) != symbol:
            raise SpotError("Kraken pair identity mismatch")
        return row

    def order_book(self, symbol, *, limit=100, timeout=None, total_timeout=None):
        if not 1 <= limit <= 100:
            raise ValueError("Invalid Kraken depth limit")
        budget = self.budget(timeout, total_timeout)
        row = self._pairs.get(symbol) or self.market(symbol, total_timeout=budget())
        observed = self.clock_ms()
        raw = self.send(
            "GET",
            "/0/public/Depth",
            params={"pair": row["native_pair"], "count": limit},
            total_timeout=budget(),
        )
        data = self.pair_result(raw, symbol)
        return self.book(
            {key: [r[:2] for r in data[key]] for key in ("bids", "asks")}, observed
        )

    def ticker_24h(self, symbol, *, timeout=None, total_timeout=None):
        budget = self.budget(timeout, total_timeout)
        row = self._pairs.get(symbol) or self.market(symbol, total_timeout=budget())
        raw = self.send(
            "GET",
            "/0/public/Ticker",
            params={"pair": row["native_pair"]},
            total_timeout=budget(),
        )
        data = self.pair_result(raw, symbol)
        return {"volume": text(data["v"][1]), "last": text(data["c"][0], positive=True)}

    def assets(self):
        if time.monotonic() >= self._assets_until:
            raw = self.send("GET", "/0/public/Assets", params={"aclass": "currency"})
            found = {}
            for key, r in objects(raw).items():
                if r.get("aclass") != "currency":
                    continue
                alias = asset_alias(r["altname"])
                # Exclude Earn/staking/tokenized balances rather than treating
                # them as ordinary immediately available cash.
                if not re.fullmatch(r"[A-Z0-9]{1,32}", alias) or "." in key:
                    continue
                for name in (key, r["altname"], alias):
                    if name in found and found[name] != alias:
                        raise SpotError("Ambiguous Kraken asset alias")
                    found[name] = alias
            self._assets = found
            self._assets_until = time.monotonic() + 300
        return self._assets

    def account(self, *, timeout=None, total_timeout=None):
        budget = self.budget(timeout, total_timeout)
        aliases = self.assets()
        balances = objects(self.signed("BalanceEx", total_timeout=budget()))
        key_info = self.signed("GetApiKeyInfo", total_timeout=budget())
        permissions = key_info.get("permissions")
        if not isinstance(permissions, list):
            raise SpotError("Kraken key permissions unavailable")
        result = {}
        for native, r in balances.items():
            if decimal(r.get("credit_used", "0")):
                raise SpotError(
                    "Kraken margin credit in use; cash-only account required"
                )
            if native not in aliases:
                continue
            asset = aliases[native]
            balance, hold = decimal(r["balance"]), decimal(r["hold_trade"])
            free = decimal(balance - hold)  # Credit is never added to buying power.
            previous = result.setdefault(
                asset, {"asset": asset, "free": "0", "locked": "0"}
            )
            previous["free"] = text(decimal(previous["free"]) + free)
            previous["locked"] = text(decimal(previous["locked"]) + hold)
        return {
            "accountType": "SPOT",
            "canTrade": all(
                p in permissions for p in ("modify-trades", "close-trades")
            ),
            "balances": list(result.values()),
        }

    def trade_fee(self, symbol):
        row = self._pairs.get(symbol) or self.market(symbol)
        data = self.signed(
            "TradeVolume", {"pair": row["native_pair"], "fee-info": "true"}
        )
        result = {"symbol": symbol}
        taker = self.pair_result(data["fees"], symbol)
        maker = self.pair_result(data.get("fees_maker", data["fees"]), symbol)
        for kind, fee in (("maker", maker), ("taker", taker)):
            rate = decimal(fee["fee"], signed=kind == "maker") / 100
            if rate >= 1:
                raise SpotError("Unsupported Kraken commission")
            result[kind + "Commission"] = text(rate, signed=kind == "maker")
        return result

    def owners(self):
        with self.state.locked() as data:
            result = {}
            for record in data["orders"].values():
                key = native_id(record["local"])
                if key in result:
                    raise SpotError("Kraken client ID conflict")
                result[key] = dict(record)
            return result

    def normalize(self, oid, row, *, local=None):
        descr = row["descr"]
        market = self._aliases.get(descr["pair"])
        if (
            market is None
            or descr.get("ordertype") != "limit"
            or descr.get("leverage") not in ("none", "0", 0)
        ):
            raise SpotError("Kraken unknown/non-cash order rejected")
        if local is not None and normalized_id(row.get("cl_ord_id")) != native_id(
            local
        ):
            raise SpotError("Kraken client order identity mismatch")
        if row.get("status") == "pending":
            state = "UNKNOWN"  # Do not invent confirmed acceptance/finality.
        else:
            state = {
                "open": "NEW",
                "closed": "FILLED",
                "canceled": "CANCELED",
                "expired": "EXPIRED",
            }.get(row.get("status"), "UNKNOWN")
        if decimal(row["vol_exec"]) and not decimal(row["cost"]):
            raise SpotError("Kraken executed quote amount unavailable")
        return order(
            row,
            oid=oid,
            cid=local or row.get("cl_ord_id", ""),
            market=market,
            side=descr["type"].upper(),
            state=state,
            quantity=row["vol"],
            executed=row["vol_exec"],
            quote=row["cost"],
            price=descr["price"],
        )

    def open_orders(self, symbol=None):
        if symbol:
            valid_symbol(symbol)
            self.market(symbol)
        else:
            self.markets()
        raw = self.signed(
            "OpenOrders",
            {"with_cursor": "true", "limit": 100, "consolidate_taker": "false"},
        )
        data = objects(raw["open"])
        if raw.get("cursor", {}).get("next") or len(data) > 100:
            raise SpotError("Kraken open-order bound exceeded")
        owners = self.owners()
        result = []
        for oid, r in data.items():
            record = owners.get(normalized_id(r.get("cl_ord_id")))
            if record is None or (symbol is not None and record["symbol"] != symbol):
                continue
            normalized = self.normalize(oid, r, local=record["local"])
            if normalized["symbol"] != record["symbol"] or record.get(
                "order_id"
            ) not in (None, str(oid)):
                raise SpotError("Kraken open-order identity mismatch")
            self.state.bind(record["local"], record["symbol"], oid)
            result.append(normalized)
        return result

    def query_raw(self, symbol, local, *, trades=False):
        valid_symbol(symbol)
        record = self.state.get(local, symbol)
        if not record:
            raise SpotError(
                "Kraken order not journaled; manual reconciliation required"
            )
        self._pairs.get(symbol) or self.market(symbol)
        oid = record.get("order_id")
        if oid:
            data = objects(
                self.signed(
                    "QueryOrders",
                    {"txid": oid, "trades": "true" if trades else "false"},
                )
            )
            if set(data) != {oid}:
                raise SpotError("Kraken queried order identity mismatch")
            raw = data[oid]
        else:
            found = {}
            for endpoint, key in (("OpenOrders", "open"), ("ClosedOrders", "closed")):
                response = self.signed(
                    endpoint,
                    {
                        "cl_ord_id": native_id(local),
                        "with_cursor": "true",
                        "consolidate_taker": "false",
                        "trades": "true" if trades else "false",
                    },
                )
                if response.get("cursor", {}).get("next"):
                    raise SpotError("Kraken client lookup bound exceeded")
                for candidate, row in objects(response[key]).items():
                    if normalized_id(row.get("cl_ord_id")) == native_id(local):
                        found[candidate] = row
            if len(found) != 1:
                raise SpotError(
                    "Kraken order unresolved/ambiguous; manual reconciliation required"
                )
            oid, raw = next(iter(found.items()))
        normalized = self.normalize(oid, raw, local=local)
        if normalized["symbol"] != symbol:
            raise SpotError("Kraken queried order market mismatch")
        self.state.bind(local, symbol, oid)
        self.state.final(local, symbol, normalized)
        return oid, raw, normalized

    def query_order(self, *, symbol, client_order_id):
        record = self.state.get(client_order_id, symbol)
        if record and record.get("final"):
            return record["final"]
        return self.query_raw(symbol, client_order_id)[2]

    def commissions(self, trades, aliases):
        ids = []
        for row in trades.values():
            if decimal(row["fee"], signed=True) == 0:
                continue
            related = row.get("ledgers")
            if (
                not isinstance(related, list)
                or not related
                or any(not isinstance(v, str) for v in related)
            ):
                raise SpotError("Kraken actual fee ledger unavailable")
            ids.extend(related)
        ids = list(dict.fromkeys(ids))
        if len(ids) > 400:
            raise SpotError("Kraken fee-ledger bound exceeded")
        ledgers = {}
        for start in range(0, len(ids), 20):
            batch = ids[start : start + 20]
            raw = objects(self.signed("QueryLedgers", {"id": ",".join(batch)}))
            if set(raw) != set(batch):
                raise SpotError("Kraken fee ledger incomplete")
            ledgers.update(raw)
        result = {}
        for tid, row in trades.items():
            if decimal(row["fee"], signed=True) == 0:
                result[tid] = (Decimal(0), "USDT")
                continue
            paid = {}
            for lid in row["ledgers"]:
                ledger = ledgers[lid]
                if (
                    ledger.get("refid") != tid
                    or ledger.get("type") != "trade"
                    or ledger.get("aclass") != "currency"
                ):
                    raise SpotError("Kraken trade/ledger identity mismatch")
                value = decimal(ledger["fee"], signed=True)
                if not value:
                    continue
                asset = aliases.get(ledger["asset"])
                if asset is None:
                    raise SpotError("Kraken actual fee currency unavailable")
                paid[asset] = paid.get(asset, Decimal(0)) + value
            if len(paid) != 1:
                raise SpotError("Kraken fee cannot be represented by Spot v1")
            asset, value = next(iter(paid.items()))
            result[tid] = (value, asset)
        return result

    def account_trades(
        self, *, symbol, order_id=None, start_time_ms=None, end_time_ms=None, limit=100
    ):
        valid_symbol(symbol)
        if not 1 <= limit <= 100:
            raise ValueError("Invalid Kraken fill limit")
        row = self._pairs.get(symbol) or self.market(symbol)
        owners = self.owners()
        known = {
            r["order_id"]: r
            for r in owners.values()
            if r["symbol"] == symbol and r.get("order_id")
        }
        if order_id is not None:
            oid = str(order_id)
            if oid not in known:
                raise SpotError("Kraken non-journaled fill query rejected")
            _, raw, normalized = self.query_raw(
                symbol, known[oid]["local"], trades=True
            )
            tids = raw.get("trades", [])
            if (
                not isinstance(tids, list)
                or len(tids) > limit
                or len(tids) != len(set(tids))
            ):
                raise SpotError("Kraken order-fill bound/identity exceeded")
            if (
                normalized["executedQty"] != "0"
                and decimal(normalized["executedQty"])
                and not tids
            ):
                raise SpotError("Kraken executed order trades unavailable")
            trades = {}
            for start in range(0, len(tids), 20):
                batch = tids[start : start + 20]
                data = objects(
                    self.signed(
                        "QueryTrades", {"txid": ",".join(batch), "ledgers": "true"}
                    )
                )
                if set(data) != set(batch):
                    raise SpotError("Kraken order fills incomplete")
                trades.update(data)
            if sum(
                (decimal(r["vol"], positive=True) for r in trades.values()), Decimal(0)
            ) != decimal(normalized["executedQty"]):
                raise SpotError("Kraken fills do not reconcile executed quantity")
        else:
            params = {
                "type": "no position",
                "pair": row["native_pair"],
                "limit": limit,
                "with_cursor": "true",
                "consolidate_taker": "false",
                "ledgers": "true",
            }
            if start_time_ms is not None:
                params["start"] = int(start_time_ms) // 1000 - 1
            if end_time_ms is not None:
                params["end"] = (int(end_time_ms) + 999) // 1000
            trades = objects(self.signed("TradesHistory", params)["trades"])
            trades = {
                tid: r for tid, r in trades.items() if str(r.get("ordertxid")) in known
            }
        aliases = self.assets()
        commissions = self.commissions(trades, aliases)
        result = []
        for tid, r in trades.items():
            if (
                r.get("ordertype") != "limit"
                or decimal(r["margin"]) != 0
                or self._aliases.get(r["pair"]) != symbol
            ):
                raise SpotError("Kraken non-cash/unmatched fill rejected")
            if str(r["ordertxid"]) not in known or (
                order_id is not None and str(r["ordertxid"]) != str(order_id)
            ):
                raise SpotError("Kraken fill order identity mismatch")
            stamp = int(decimal(r["time"], positive=True) * 1000)
            if (start_time_ms is not None and stamp < start_time_ms) or (
                end_time_ms is not None and stamp > end_time_ms
            ):
                continue
            fee, asset = commissions[tid]
            result.append(
                dict(
                    id=tid,
                    orderId=str(r["ordertxid"]),
                    symbol=symbol,
                    price=text(r["price"], positive=True),
                    qty=text(r["vol"], positive=True),
                    quoteQty=text(r["cost"]),
                    commission=text(fee, signed=True),
                    commissionAsset=asset,
                    time=stamp,
                )
            )
        return result

    def parameters(self, symbol, side, quantity, price, local):
        return {
            "pair": self._pairs[symbol]["native_pair"],
            "ordertype": "limit",
            "type": side.value.lower(),
            "volume": text(quantity, positive=True),
            "price": text(price, positive=True),
            "cl_ord_id": native_id(local),
            "timeinforce": "GTC",
            "oflags": "fciq",
        }

    def test_limit_order(self, **kwargs):
        super().test_limit_order(**kwargs)
        params = self.parameters(
            kwargs["symbol"],
            kwargs["side"],
            kwargs["quantity"],
            kwargs["price"],
            kwargs["client_order_id"],
        )
        params["validate"] = "true"
        raw = self.signed("AddOrder", params)
        if not isinstance(raw, dict) or raw.get("txid"):
            raise SpotError("Unexpected Kraken no-order validation response")
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
        self.state.begin(client_order_id, symbol)
        raw = self.signed(
            "AddOrder",
            self.parameters(symbol, side, quantity, price, client_order_id),
            write=True,
        )
        ids = raw.get("txid")
        if (
            not isinstance(ids, list)
            or len(ids) != 1
            or not isinstance(ids[0], str)
            or not ids[0]
        ):
            raise SpotError(
                "Kraken submitted order identity unavailable", execution_unknown=True
            )
        self.state.bind(client_order_id, symbol, ids[0])
        return self.query_order(symbol=symbol, client_order_id=client_order_id)

    def cancel_order(self, *, symbol, client_order_id):
        self.write_gate()
        current = self.query_order(symbol=symbol, client_order_id=client_order_id)
        if current["status"] in ("FILLED", "CANCELED", "EXPIRED"):
            return current
        raw = self.signed("CancelOrder", {"txid": current["orderId"]}, write=True)
        if type(raw.get("count")) is not int or raw["count"] != 1:
            raise SpotError("Kraken cancellation unconfirmed", execution_unknown=True)
        # A count/pending acknowledgement has no authoritative final fills.
        result = self.query_order(symbol=symbol, client_order_id=client_order_id)
        if result["status"] not in ("FILLED", "CANCELED", "EXPIRED"):
            raise SpotError("Kraken cancellation still pending", execution_unknown=True)
        return result


def create_client(config, *, state_dir=None, **kwargs):
    return bounded_client(Kraken(config, state_dir=state_dir, **kwargs))
