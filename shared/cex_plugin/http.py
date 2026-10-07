"""Spot transport compatibility policy over the universal pooled HTTP core."""
from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Protocol
from .http_pool import PersistentTransport, configure_wallet_proxy, close_transports, diagnostic_capture


@dataclass
class TransportError(Exception):
    status: int | None = None
    timeout: bool = False


class JsonTransport(Protocol):
    def request(self, **kwargs): ...


def _error(*, kind, status=None, raw=None):
    return TransportError(status=status, timeout=kind == 'timeout')


class UrllibJsonTransport(PersistentTransport):
    def __init__(self):
        super().__init__(decode=lambda raw: json.loads(raw.decode(), parse_float=Decimal),
                         error=_error, accepts=lambda status: status == 200,
                         max_body=1024*1024, total_by_default=True)
