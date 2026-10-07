"""Observe executor queue vs OS name resolution; no DNS overrides/probes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import socket
import threading
import time

active_trace = ContextVar('cex_http_trace', default=None)


class ResolverMeasurement:
    def __init__(self):
        self.queued = time.monotonic()
        self.started = self.finished = None
        self._lock = threading.Lock()

    def mark(self, name):
        with self._lock: setattr(self, name, time.monotonic())

    def snapshot(self, now):
        with self._lock:
            started, finished = self.started, self.finished
        return {'resolver_queue_ms': ((started or now)-self.queued)*1000,
                'resolver_inflight': finished is None,
                **({'resolver_call_ms': ((finished or now)-started)*1000}
                   if started is not None else {})}


class ResolverLoop:
    def __init__(self, loop, executor):
        self.loop, self.executor = loop, executor

    def __getattr__(self, name): return getattr(self.loop, name)

    async def getaddrinfo(self, host, port, **kwargs):
        measurement = ResolverMeasurement()
        trace = active_trace.get()
        if trace is not None: trace.resolver_measurement = measurement
        def resolve():
            measurement.mark('started')
            try: return socket.getaddrinfo(host, port, **kwargs)
            finally: measurement.mark('finished')
        return await self.loop.run_in_executor(self.executor, resolve)


class MeasuredResolver:
    def __init__(self):
        # Keep adapter import/configuration independent of the runtime-only
        # aiohttp dependency; construct it only for an actual HTTP session.
        from aiohttp.resolver import ThreadedResolver
        loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='cex-resolver')
        # ThreadedResolver still owns normalization, flags and IPv6 handling.
        self._resolver = ThreadedResolver(loop=ResolverLoop(loop, self.executor))

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return await self._resolver.resolve(host, port, family)

    async def close(self):
        await self._resolver.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
