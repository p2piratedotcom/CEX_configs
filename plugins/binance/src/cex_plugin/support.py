"""Plugin-local Spot helpers. No strategy math, credentials on disk or retries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time

from .http import JsonTransport, TransportError, UrllibJsonTransport
from .models import HedgeSide, OrderBook
from .types import LiveTradingDisabled, SpotError, SymbolCheck, SymbolRules, TimeSync

_deadline = ContextVar("spot_deadline", default=None)


def remaining():
    deadline = _deadline.get()
    value = deadline - time.monotonic() if deadline is not None else 10.0
    if value <= 0:
        raise SpotError("Spot command budget exceeded (timeout)")
    return value


def bounded_client(client):
    # One budget for the entire RPC, including nested reads and reconciliation.
    methods = (
        "server_time",
        "synchronize_time",
        "check_symbol",
        "symbol_rules",
        "order_book",
        "ticker_24h",
        "self_symbols",
        "account",
        "trade_fee",
        "open_orders",
        "account_trades",
        "test_limit_order",
        "place_limit_order",
        "query_order",
        "cancel_order",
    )

    def wrap(method, name):
        @wraps(method)
        def call(*args, **kwargs):
            limits = [client.timeout]
            limits.extend(
                float(kwargs[k])
                for k in ("timeout", "total_timeout")
                if kwargs.get(k) is not None
            )
            if name == "synchronize_time":
                limits.append(float(kwargs.get("max_round_trip_ms", 2000)) / 1000)
            duration = min(limits)
            if not 0 < duration <= 10:
                raise ValueError("Invalid command budget")
            parent = _deadline.get()
            deadline = time.monotonic() + duration
            token = _deadline.set(
                min(parent, deadline) if parent is not None else deadline
            )
            try:
                remaining()
                try:
                    return method(*args, **kwargs)
                except SpotError as exc:
                    if name in ("place_limit_order", "cancel_order") and not isinstance(
                        exc, LiveTradingDisabled
                    ):
                        exc.execution_unknown = True
                    raise
            finally:
                _deadline.reset(token)

        return call

    for name in methods:
        setattr(client, name, wrap(getattr(client, name), name))
    return client


def precision_step(value):
    precision = int(value)
    if precision != decimal(value) or not 0 <= precision <= 18:
        raise SpotError("Unsupported precision")
    return Decimal(1).scaleb(-precision)


def decimal(value, *, positive=False, signed=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SpotError("Invalid decimal response") from exc
    if (
        not result.is_finite()
        or (positive and result <= 0)
        or (not signed and result < 0)
    ):
        raise SpotError("Invalid decimal response")
    return result


def text(value, **kwargs):
    return format(decimal(value, **kwargs), "f")


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{1,28}USDT", value):
        raise ValueError("Only canonical USDT Spot routes are supported")
    return value


validate_symbol = symbol


def pair(value):
    value = symbol(value)
    return value[:-4] + "_USDT"


def canonical(value):
    return symbol(value.replace("_", ""))


def client_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("Invalid client order identifier")
    return "p2p" + hashlib.sha256(value.encode()).hexdigest()[:29]


def rows(value):
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise SpotError("Invalid list response")
    return value


def order(raw, *, oid, cid, market, side, state, quantity, executed, quote, price):
    q, done = decimal(quantity, positive=True), decimal(executed)
    if done > q or side not in ("BUY", "SELL") or not str(oid):
        raise SpotError("Invalid order response")
    status = (
        state
        if state
        in ("NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED")
        else "UNKNOWN"
    )
    if status == "FILLED" and done != q:
        raise SpotError("Inconsistent filled order response")
    if status == "NEW" and done:
        status = "PARTIALLY_FILLED"
    return dict(
        orderId=str(oid),
        clientOrderId=cid,
        symbol=symbol(market),
        side=side,
        status=status,
        origQty=str(q),
        executedQty=str(done),
        cummulativeQuoteQty=text(quote),
        price=text(price, positive=True),
    )


class State:
    """Cross-process durable attempts and nonces, separated by API-key identity."""

    def __init__(self, directory, api_key):
        self.directory = Path(directory) if directory else None
        self.name = (
            "spot-" + hashlib.sha256((api_key or "public").encode()).hexdigest()[:32]
        )

    def save(self, data):
        payload = json.dumps(data, separators=(",", ":")).encode()
        if len(payload) > 8 * 1024 * 1024:
            raise SpotError("Private plugin state bound exceeded")
        fd, tmp = tempfile.mkstemp(prefix=".spot-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, self.directory / (self.name + ".json"))
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @contextmanager
    def locked(self):
        if self.directory is None:
            raise SpotError("Private plugin state directory is required")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(self.directory / (self.name + ".lock"), "a") as lock:
            os.chmod(lock.name, 0o600)
            while True:
                remaining()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.02, remaining()))
            path = self.directory / (self.name + ".json")
            if path.exists() and path.stat().st_size > 8 * 1024 * 1024:
                raise SpotError("Private plugin state bound exceeded")
            data = (
                json.loads(path.read_text())
                if path.exists()
                else {"nonce": 0, "orders": {}}
            )
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("orders"), dict)
                or type(data.get("nonce")) is not int
            ):
                raise SpotError("Invalid private plugin state")
            try:
                yield data
            finally:
                # Persist mutations even when a signed HTTP request times out.
                self.save(data)

    def final(self, local, market, snapshot):
        if snapshot["status"] not in ("FILLED", "CANCELED"):
            return
        with self.locked() as data:
            record = data["orders"].get(client_id(local))
            if (
                record is None
                or record["local"] != local
                or record["symbol"] != symbol(market)
            ):
                raise SpotError("Order identity conflict", execution_unknown=True)
            if record.get("order_id") != snapshot["orderId"]:
                raise SpotError("Order identity conflict", execution_unknown=True)
            record["final"] = snapshot

    def begin(self, local, market):
        native = client_id(local)
        with self.locked() as data:
            if native in data["orders"]:
                raise SpotError(
                    "Existing order attempt must be reconciled, never replayed",
                    execution_unknown=True,
                )
            if len(data["orders"]) >= 10000:
                raise SpotError("Private order journal capacity reached")
            data["orders"][native] = {
                "local": local,
                "symbol": symbol(market),
                "created_ms": time.time_ns() // 1_000_000,
            }
        return native

    def bind(self, local, market, oid):
        native = client_id(local)
        with self.locked() as data:
            record = data["orders"].get(native)
            if record and (
                record["local"] != local or record["symbol"] != symbol(market)
            ):
                raise SpotError("Order identity conflict", execution_unknown=True)
            if record:
                if record.get("order_id") not in (None, str(oid)):
                    raise SpotError("Order identity conflict", execution_unknown=True)
                record["order_id"] = str(oid)

    def get(self, local, market):
        with self.locked() as data:
            record = data["orders"].get(client_id(local))
            if record and (
                record["local"] != local or record["symbol"] != symbol(market)
            ):
                raise SpotError("Order identity conflict")
            return dict(record) if record else None

    def local(self, native, market):
        with self.locked() as data:
            record = data["orders"].get(native)
            if record and record["symbol"] == market:
                return record["local"]
        return native


class Base:
    supports_read_deadlines = True

    def __init__(
        self,
        config,
        *,
        state_dir=None,
        api_key=None,
        api_secret=None,
        trading_enabled=False,
        timeout=10,
        transport=None,
        clock_ms=None,
    ):
        self.config, self.api_key, self.api_secret = config, api_key, api_secret
        self.base_url = config["base_url"].rstrip("/")
        self.trading_enabled, self.timeout = trading_enabled, min(float(timeout), 10)
        if not 0 < self.timeout <= 10:
            raise ValueError("Invalid timeout")
        self.transport = transport or UrllibJsonTransport()
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.offset = 0
        self.state = State(state_dir, api_key)

    def credentials(self):
        if not self.api_key or not self.api_secret:
            raise SpotError("API key and secret are required")

    def write_gate(self):
        if not self.trading_enabled:
            raise LiveTradingDisabled("Live Spot trading is disabled")

    def send(
        self,
        method,
        path,
        *,
        params=None,
        headers=None,
        body=None,
        timeout=None,
        total_timeout=None,
        write=False,
    ):
        from urllib.parse import urlencode

        query = urlencode(params or {}, doseq=True)
        url = self.base_url + path + ("?" + query if query else "")
        remaining_budget = min(
            remaining(),
            self.timeout,
            timeout if timeout is not None else self.timeout,
            total_timeout if total_timeout is not None else self.timeout,
        )
        try:
            result = self.transport.request(
                method=method,
                url=url,
                headers=headers or {},
                body=body,
                timeout=remaining_budget,
                total_timeout=remaining_budget,
            )
        except TransportError as exc:
            raise SpotError(
                (
                    "Spot request failed (timeout)"
                    if exc.timeout
                    else "Spot request failed"
                ),
                status=exc.status,
                execution_unknown=write
                and (exc.status is None or exc.status >= 500 or exc.status == 408),
            ) from None
        except Exception:
            raise SpotError(
                "Spot response unavailable", execution_unknown=write
            ) from None
        return self.unwrap(result, write=write)

    def unwrap(self, raw, *, write=False):
        if isinstance(raw, dict) and raw.get("code") not in (None, 0, 200, "0", "200"):
            code = raw.get("code")
            raise SpotError(
                "Spot API rejected request",
                payload={"code": code},
                execution_unknown=write,
            )
        return raw

    def date_time(self, path, *, timeout=None):
        self.send("GET", path, timeout=timeout, total_timeout=timeout)
        date = getattr(self.transport, "last_headers", {}).get("Date")
        if not date:
            raise SpotError("Server time header unavailable")
        try:
            stamp = int(parsedate_to_datetime(date).timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            raise SpotError("Invalid server time header") from None
        return {"serverTime": stamp}

    def synchronize_time(self, *, max_round_trip_ms=2000):
        start = self.clock_ms()
        remote = int(
            self.server_time(timeout=min(self.timeout, max_round_trip_ms / 1000))[
                "serverTime"
            ]
        )
        end = self.clock_ms()
        if not 0 <= end - start <= max_round_trip_ms:
            raise SpotError("Time synchronization budget exceeded")
        midpoint = start + (end - start) // 2
        self.offset = remote - midpoint
        return TimeSync(remote, midpoint, self.offset, end - start)

    def budget(self, timeout=None, total_timeout=None):
        value = min(
            self.timeout,
            remaining(),
            timeout if timeout is not None else self.timeout,
            total_timeout if total_timeout is not None else self.timeout,
        )
        if value <= 0:
            raise ValueError("Invalid read budget")
        end = time.monotonic() + value

        def remaining():
            value = end - time.monotonic()
            if value <= 0:
                raise SpotError("Spot read budget exceeded (timeout)")
            return value

        return remaining

    def check_symbol(self, symbol, *, timeout=None, total_timeout=None):
        value = validate_symbol(symbol)
        row = self.market(value, timeout=timeout, total_timeout=total_timeout)
        allowed = row is not None and self.enabled(row)
        return SymbolCheck(
            value,
            row is not None,
            allowed,
            ("LIMIT",) if allowed else (),
            () if allowed else ("Spot market unavailable",),
            row,
        )

    def self_symbols(self, *, timeout=None, total_timeout=None):
        return {
            "data": sorted(self.markets(timeout=timeout, total_timeout=total_timeout))
        }

    def book(self, raw, observed):
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("bids"), list)
            or not isinstance(raw.get("asks"), list)
        ):
            raise SpotError("Invalid order book")
        normalized = {}
        for key in ("bids", "asks"):
            normalized[key] = [
                [text(p, positive=True), text(q, positive=True)]
                for p, q in raw[key]
                if decimal(q) > 0
            ]
        return OrderBook.from_mexc(normalized, observed_at_ms=observed)

    def test_limit_order(self, *, symbol, side, quantity, price, client_order_id):
        decimal(quantity, positive=True)
        decimal(price, positive=True)
        client_id(client_order_id)
        rules = self.symbol_rules(symbol)
        if side not in (HedgeSide.BUY, HedgeSide.SELL) or not rules.allows(side):
            raise ValueError("Invalid Spot order side")
        if (
            quantity % rules.quantity_step
            or price % rules.price_step
            or quantity * price < rules.min_quote_amount
            or (
                rules.max_quote_amount is not None
                and quantity * price > rules.max_quote_amount
            )
        ):
            raise ValueError("Order violates Spot precision or notional limits")
        self.validate_native(symbol, quantity, price, side)
        return {
            "validated": True,
            "request_sent": False,
            "experimental": True,
            "live_account_tested": False,
        }

    def validate_native(self, symbol, quantity, price, side):
        pass
