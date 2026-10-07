"""Common persistent HTTP runtime for every Spot adapter; no venue policy."""
from __future__ import annotations
import asyncio
import atexit
import threading
import weakref
from urllib.parse import urlparse
from .http_diagnostics import RequestTrace, diagnostic_capture, request_trace_config
from .http_resolver import MeasuredResolver, active_trace

_route_revision = 0
_configured = False
_proxy = None
_transports = weakref.WeakSet()


def configure_wallet_proxy(proxy_url):
    global _configured, _proxy, _route_revision
    if proxy_url is not None:
        u = urlparse(proxy_url)
        if (u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port or
            u.username or u.password or u.path not in ('', '/') or u.query or u.fragment):
            raise ValueError('Invalid wallet HTTP proxy')
    if (_configured, _proxy) != (True, proxy_url): _route_revision += 1
    _configured, _proxy = True, proxy_url


class PersistentTransport:
    def __init__(self, *, decode, error, accepts, max_body=None, total_by_default=True, error_body_required=False):
        self.decode, self.error, self.accepts = decode, error, accepts
        self.max_body, self.total_by_default = max_body, total_by_default
        self.error_body_required = error_body_required
        self._local = threading.local()
        self.last_headers = {}
        _transports.add(self)

    def close(self):
        runtime = getattr(self._local, 'runtime', None)
        if runtime is not None:
            self._local.runtime = None
            runtime.close()

    def _runtime(self):
        runtime = getattr(self._local, 'runtime', None)
        if runtime is not None and runtime.revision != _route_revision:
            self.close()
            runtime = None
        if runtime is None:
            runtime = HttpRuntime(_route_revision)
            self._local.runtime = runtime
        return runtime

    def request(self, *, method, url, headers=None, body=None, timeout=10, total_timeout=None):
        if not _configured: raise self.error(kind='route_unconfigured')
        if timeout <= 0 or (total_timeout is not None and total_timeout <= 0):
            raise ValueError('HTTP timeouts must be positive')
        total = (min(timeout, total_timeout if total_timeout is not None else timeout)
                 if total_timeout is not None or self.total_by_default else None)
        # Capture the explicit route once; no environment/direct fallback.
        return self._runtime().runner.run(self._request(
            method, url, headers, body, timeout, total, _proxy))

    async def _request(self, method, url, headers, body, socket_timeout, total, proxy):
        import aiohttp
        trace = RequestTrace()
        try:
            deadline = (aiohttp.ClientTimeout(total=total) if total is not None else
                        aiohttp.ClientTimeout(total=None, sock_connect=socket_timeout,
                                              sock_read=socket_timeout))
            runtime = self._runtime()
            session = await runtime.session()
            trace.fields['session_reused'] = runtime.requests > 0
            runtime.requests += 1
            token = active_trace.set(trace)
            try:
                async with session.request(method, url, headers=dict(headers or {}), data=body,
                    allow_redirects=False, timeout=deadline, trace_request_ctx=trace,
                    proxy=proxy) as response:
                    trace.fields['transport_http_status'] = response.status
                    trace.enter('response_body')
                    self.last_headers = {'Date': response.headers.get('Date')}
                    if not self.accepts(response.status) and not self.error_body_required:
                        raise self.error(kind='http_rejected', status=response.status)
                    if self.max_body is None:
                        raw = await response.read()
                    else:
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            raw.extend(chunk)
                            if len(raw) > self.max_body:
                                trace.fields['transport_failure'] = 'response_limit'
                                raise self.error(kind='response_limit')
                        raw = bytes(raw)
                    trace.enter('response_decode')
                    if not self.accepts(response.status):
                        raise self.error(kind='http_rejected', status=response.status, raw=raw)
                    return self.decode(raw)
            finally:
                active_trace.reset(token)
        except asyncio.TimeoutError as exc:
            trace.error(exc)
            raise self.error(kind='timeout') from None
        except aiohttp.ClientError as exc:
            trace.error(exc)
            raise self.error(kind='connection_error') from None
        except (ValueError, UnicodeError) as exc:
            trace.error(exc)
            raise self.error(kind='invalid_data') from None
        finally:
            trace.finish()


class HttpRuntime:
    def __init__(self, revision):
        self.revision = revision
        self.runner = asyncio.Runner()
        self._session = self._resolver = None
        self.requests = 0

    async def session(self):
        import aiohttp
        if self._session is None:
            self._resolver = MeasuredResolver()
            connector = aiohttp.TCPConnector(resolver=self._resolver,
                limit=4, limit_per_host=4, ttl_dns_cache=10)
            self._session = aiohttp.ClientSession(connector=connector,
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                trace_configs=[request_trace_config()])
            # aiohttp 3.x's private switch: review on dependency upgrades.
            # No hidden retry of reads or writes on a stale persistent socket.
            self._session._retry_connection = False
        return self._session

    async def _close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._resolver is not None:
            await self._resolver.close()
            self._resolver = None

    def close(self):
        try: self.runner.run(self._close())
        finally: self.runner.close()


def close_transports():
    for transport in list(_transports):
        try: transport.close()
        except Exception: pass


atexit.register(close_transports)
