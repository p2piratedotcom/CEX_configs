"""Bounded, payload-free HTTP phase measurements. No logging or network policy."""
from contextlib import contextmanager
import socket
import ssl
import threading
import time

_local = threading.local()


@contextmanager
def diagnostic_capture(progress=None):
    previous = getattr(_local, 'records', None)
    previous_progress = getattr(_local, 'progress', None)
    _local.progress = progress
    records = []
    _local.records = records
    try:
        yield records
    finally:
        _local.records = previous
        _local.progress = previous_progress


class RequestTrace:
    def __init__(self):
        self.started = self.phase_started = time.monotonic()
        self.phase = 'connect_or_headers'
        self.fields = {}
        self.marks = {}
        self.resolver_measurement = None
        self.cpu_started = time.thread_time()
        self._progress()

    def _progress(self):
        callback = getattr(_local, 'progress', None)
        if callback is not None:
            try:
                callback(self.phase)
            except Exception:
                pass  # A diagnostic pipe failure cannot change an HTTP outcome.

    def enter(self, phase):
        now = time.monotonic()
        key = self.phase + '_ms'
        self.fields[key] = self.fields.get(key, 0) + (now-self.phase_started)*1000
        self.phase, self.phase_started = phase, now
        self._progress()

    def error(self, exc):
        cause = (getattr(exc, 'reason', None) or getattr(exc, 'os_error', None)
                 or getattr(exc, 'certificate_error', None) or exc)
        self.fields['transport_failure'] = (
            'timeout' if isinstance(cause, TimeoutError) else
            'dns_error' if isinstance(cause, socket.gaierror) else
            'tls_error' if isinstance(cause, ssl.SSLError) else
            'connection_refused' if isinstance(cause, ConnectionRefusedError) else
            'connection_reset' if isinstance(cause, ConnectionResetError) else
            'connection_error' if isinstance(cause, OSError) else 'client_error')
        errno = getattr(cause, 'errno', None)
        if type(errno) is int:
            self.fields['transport_errno'] = errno

    def finish(self):
        now = time.monotonic()
        key = self.phase + '_ms'
        self.fields[key] = self.fields.get(key, 0) + (now-self.phase_started)*1000
        self.fields.update(transport_phase=self.phase,
                           http_elapsed_ms=(now-self.started)*1000,
                           http_thread_cpu_ms=(time.thread_time()-self.cpu_started)*1000)
        if self.resolver_measurement is not None:
            self.fields.update(self.resolver_measurement.snapshot(now))
        record = {k: round(v, 2) if type(v) is float else v for k, v in self.fields.items()}
        records = getattr(_local, 'records', None)
        if records is not None and len(records) < 8:
            records.append(record)
        return record

    def aiohttp_config(self):
        return request_trace_config(default_trace=self)


def request_trace_config(default_trace=None):
    import aiohttp
    config = aiohttp.TraceConfig()
    # Persistent sessions must select the current request's trace, never the
    # first request's callback/identity. Params may contain secrets: ignore them.
    def trace(context):
        return getattr(context, 'trace_request_ctx', None) or default_trace
    async def connect_start(_session, context, _params):
        t = trace(context)
        if t is not None:
            t.marks['connect'] = time.monotonic()
            t.enter('connect_tls')
    async def connect_end(_session, context, _params):
        t = trace(context)
        if t is not None:
            t.fields['connection_total_ms'] = (time.monotonic()-t.marks['connect'])*1000
            t.enter('request_send')
    async def dns_start(_session, context, _params):
        t = trace(context)
        if t is not None:
            t.fields['dns_cache_hit'] = False
            t.enter('dns')
    async def dns_end(_session, context, _params):
        t = trace(context)
        if t is not None: t.enter('connect_tls')
    async def cache_hit(_session, context, _params):
        t = trace(context)
        if t is not None: t.fields['dns_cache_hit'] = True
    async def headers_sent(_session, context, _params):
        t = trace(context)
        if t is not None: t.enter('headers_wait')
    async def reuse(_session, context, _params):
        t = trace(context)
        if t is not None:
            t.fields['connection_reused'] = True
            t.enter('request_send')
    async def queued_start(_session, context, _params):
        t = trace(context)
        if t is not None: t.enter('connection_queue')
    async def queued_end(_session, context, _params):
        t = trace(context)
        if t is not None: t.enter('connect_or_headers')
    for name, callback in [('on_connection_create_start', connect_start),
                           ('on_connection_create_end', connect_end),
                           ('on_dns_resolvehost_start', dns_start),
                           ('on_dns_resolvehost_end', dns_end),
                           ('on_dns_cache_hit', cache_hit),
                           ('on_request_headers_sent', headers_sent),
                           ('on_connection_reuseconn', reuse),
                           ('on_connection_queued_start', queued_start),
                           ('on_connection_queued_end', queued_end)]:
        getattr(config, name).append(callback)
    return config
