"""Bounded large public metadata; same wallet route, no redirects or retries."""

import asyncio
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import time
from urllib.parse import urlparse

from . import http
from .http import TransportError, UrllibJsonTransport


class MetadataTransport(UrllibJsonTransport):
    # Only catalog responses may exceed the usual 1 MiB native-body bound.
    LARGE_PATHS = frozenset(("/api/v3/exchangeInfo", "/0/public/AssetPairs"))
    blocked_until = 0.0

    async def _request(self, method, url, headers, body, budget):
        import aiohttp

        if time.monotonic() < self.blocked_until:
            raise TransportError(status=429)
        limit = (8 if urlparse(url).path in self.LARGE_PATHS else 1) * 1024 * 1024
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=budget), trust_env=False
            ) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    data=body,
                    proxy=http._wallet_proxy_url,
                    allow_redirects=False,
                ) as response:
                    self.last_headers = {"Date": response.headers.get("Date")}
                    if response.status in (418, 429):
                        retry = response.headers.get("Retry-After")
                        wait = 120.0
                        if retry:
                            try:
                                wait = max(wait, float(retry))
                            except ValueError:
                                try:
                                    wait = max(
                                        wait,
                                        parsedate_to_datetime(retry).timestamp()
                                        - time.time(),
                                    )
                                except (ValueError, TypeError, OverflowError):
                                    pass
                        # Never retry in this request or change the wallet route.
                        self.blocked_until = time.monotonic() + wait
                    if response.status != 200:
                        raise TransportError(status=response.status)
                    payload = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        payload.extend(chunk)
                        if len(payload) > limit:
                            raise TransportError()
                    return json.loads(payload.decode(), parse_float=Decimal)
        except asyncio.TimeoutError:
            raise TransportError(timeout=True) from None
        except (aiohttp.ClientError, ValueError, UnicodeError):
            raise TransportError() from None
