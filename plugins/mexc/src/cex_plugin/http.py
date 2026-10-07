"""MEXC/Gate error and JSON compatibility over the universal HTTP core."""
from dataclasses import dataclass
import json
from typing import Any, Protocol
from .http_pool import PersistentTransport, configure_wallet_proxy, close_transports, diagnostic_capture


@dataclass(slots=True)
class TransportError(Exception):
    message: str
    status: int | None = None
    payload: Any = None

    def __str__(self):
        suffix = f" (HTTP {self.status})" if self.status is not None else ""
        return f"{self.message}{suffix}"


class JsonTransport(Protocol):
    def request(self, **kwargs): ...


def _decode_payload(raw):
    if not raw: return None
    try: return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode('utf-8', errors='replace')


def _error(*, kind, status=None, raw=None):
    message = ('remote API rejected the request' if status is not None else
               'remote API is unreachable (timeout)' if kind == 'timeout' else
               'wallet route is not configured' if kind == 'route_unconfigured' else
               'remote API is unreachable ('+kind+')')
    return TransportError(message, status=status,
                          payload=_decode_payload(raw) if raw is not None else None)


class UrllibJsonTransport(PersistentTransport):
    def __init__(self):
        super().__init__(decode=_decode_payload, error=_error,
                         accepts=lambda status: status < 300,
                         total_by_default=False, error_body_required=True)
