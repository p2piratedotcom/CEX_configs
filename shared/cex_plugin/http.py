"""Bounded HTTP transport using only the route supplied by the wallet."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Protocol
from urllib.parse import urlparse

_wallet_proxy_configured = False
_wallet_proxy_url = None


def configure_wallet_proxy(proxy_url):
    global _wallet_proxy_configured, _wallet_proxy_url
    if proxy_url is not None:
        u = urlparse(proxy_url)
        if (
            u.scheme != "http"
            or u.hostname != "127.0.0.1"
            or not u.port
            or u.username
            or u.password
            or u.path not in ("", "/")
            or u.query
            or u.fragment
        ):
            raise ValueError("Invalid wallet HTTP proxy")
    _wallet_proxy_url, _wallet_proxy_configured = proxy_url, True


@dataclass
class TransportError(Exception):
    status: int | None = None
    timeout: bool = False


class JsonTransport(Protocol):
    def request(self, **kwargs): ...


class UrllibJsonTransport:
    def request(
        self, *, method, url, headers=None, body=None, timeout=10, total_timeout=None
    ):
        if not _wallet_proxy_configured:
            raise TransportError()
        budget = min(timeout, total_timeout if total_timeout is not None else timeout)
        if budget <= 0:
            raise ValueError("Invalid HTTP budget")
        return asyncio.run(self._request(method, url, headers, body, budget))

    async def _request(self, method, url, headers, body, budget):
        import aiohttp

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=budget)
            ) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    data=body,
                    proxy=_wallet_proxy_url,
                    allow_redirects=False,
                ) as response:
                    self.last_headers = {"Date": response.headers.get("Date")}
                    if response.status != 200:
                        raise TransportError(status=response.status)
                    payload = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        payload.extend(chunk)
                        if len(payload) > 1024 * 1024:
                            raise TransportError()
                    return json.loads(payload.decode(), parse_float=Decimal)
        except asyncio.TimeoutError:
            raise TransportError(timeout=True) from None
        except (aiohttp.ClientError, ValueError, UnicodeError):
            raise TransportError() from None
