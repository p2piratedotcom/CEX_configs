# P2Pirate Spot protocol v1

## Boundary

The common engine owns maker pricing, premium, sizing, budgets, reservations,
hedging, durable journals and KDF reconciliation. Plugins own native endpoints,
API signing, symbol aliases, precision filters, rate-limit handling and response
normalization. The GUI consumes the engine's catalog-derived venue list.

Scope: Spot limit orders with durable client identifiers and reconciliation,
hedging configured assets through USDT Spot routes. Derivatives, margin, account
transfers and arbitrary non-USDT hedge anchors are outside v1.

## Package and public configuration

A plugin ZIP contains only `cex_plugin/<module>.py` and
`cex_plugin/__init__.py`; no native extensions, symlinks, duplicate paths or
arbitrary directory hierarchy. Maximum 64 files, 1 MiB compressed and 3 MiB of
source. `adapter.py` implements the factory; `http.py` implements
`configure_wallet_proxy(proxy_url)`; `models.py` supplies `HedgeSide(BUY, SELL)`.

`create_client(config, *, state_dir=None, api_key=None, api_secret=None,
trading_enabled=False, timeout=10)` returns a client implementing all methods
below. The host runs inside the Linux engine executable, so no separate Python
installation is required. Standard library and the engine's bundled `aiohttp`
are available; other runtime dependencies are not implicitly installed.

Configuration has exactly these fields (see MEXC/Gate examples):
`schema`, `protocol`, `version`, `venue`, `display_name`, `base_url`, `legacy_keys`,
`private_read_timeout`, `time_sync_budget_ms`, `recv_window_ms`,
`timestamp_error_codes`, `symbols_error_codes`, `taker_fee`,
`credential_fields`, `settings`.

`protocol` is `p2pirate-spot-v1`; version is `major.minor.patch`; venue is an
uppercase identifier. The API base is HTTPS. Read timeout <=10 seconds, clock
sync budget <=12000 ms, receive window <=60000 ms. `credential_fields` is
`["api_key", "api_secret"]`. `settings` contains exchange-specific public options;
credentials, passphrases and access tokens are forbidden. `legacy_keys` is true
only for MEXC, preserving its historical key/journal namespace.

## Transport and routing

One child process hosts one adapter client. The parent sends a bounded JSON
bootstrap on stdin, including immutable catalog directory, venue, private
per-wallet/per-venue `state_dir`, credentials and explicit Tor/direct routing.
The child verifies the catalog and responds with `{protocol:1, venue, version,
methods:[...]}`. Subsequent newline JSON requests have integer `id`, `method`,
`args` and `kwargs`; replies have the same `id` and `result` or `error`.
The host reserves stdout for protocol frames; use no logging there. Requests are
<=64 KiB, replies <=1 MiB, with bounded waits. Clients are reused; changing keys
closes obsolete clients. Shutdown reaps children.

Use the supplied route for **all** remote requests, including time sync and
public market reads. No environment-proxy override or direct fallback when Tor
fails. Do not forward credentials across redirects. Keys travel via stdin only,
never command-line arguments, signed URL logs or public files.

`state_dir` may contain private nonces and durable client-ID mappings. Writes
must be atomic and synchronized across the public/private client processes.
Do not store API secrets there. The adapter owns native nonce/client-ID constraints
without changing strategy math. Protect persisted mappings across restarts and
never derive a different native identifier when looking up an uncertain order.

## Methods and normalized results

Read methods accept `timeout`/`total_timeout` where present in the supplied
adapters. Preserve total budgets rather than assigning the full budget to each
subrequest. Methods raising errors are permitted for unsupported markets; silently
returning fabricated data is not.

| Method | Arguments | Normalized result |
|---|---|---|
| `server_time` | `timeout` | `{serverTime: integer ms}` |
| `synchronize_time` | `max_round_trip_ms` | `TimeSync` |
| `check_symbol` | symbol, timeout, total_timeout | `SymbolCheck` |
| `symbol_rules` | symbol, timeout, total_timeout | `SymbolRules` |
| `order_book` | symbol, limit, timeout, total_timeout | `OrderBook` |
| `ticker_24h` | symbol, timeout, total_timeout | map with volume (24h base quantity string); optional last/lastPrice |
| `self_symbols` | timeout, total_timeout | `{data: [canonical authorized symbols]}` |
| `account` | timeout, total_timeout | `{accountType:"SPOT", canTrade:bool, balances:[{asset,free,locked}]}` |
| `trade_fee` | symbol | `{symbol,makerCommission,takerCommission}` |
| `open_orders` | optional symbol | list of normalized order maps |
| `account_trades` | symbol, order_id, start_time_ms, end_time_ms, limit | normalized fills |
| `test_limit_order` | symbol, side, quantity, price, client_order_id | validation only; never a real order |
| `place_limit_order` | same order arguments | normalized order map; live permission required |
| `query_order` | symbol, client_order_id | normalized order map |
| `cancel_order` | symbol, client_order_id | normalized order map; live permission required |

Tagged results use `{type:<name>,value:<fields>}` from dataclasses named:

- `TimeSync`: server_time_ms, local_midpoint_ms, offset_ms, round_trip_ms.
- `SymbolCheck`: symbol, listed, spot_trading_allowed, order_types, problems, raw.
- `SymbolRules`: symbol, base_asset, quote_asset, quantity_step, price_step,
  min_quote_amount, max_quote_amount, order_types, trade_side_type (1 both,
  2 buy, 3 sell).
- `PriceLevel`: price, quantity.
- `OrderBook`: bids/asks as tagged PriceLevels, observed_at_ms dated at local
  request start, never completion or a future server timestamp.

Decimal values are finite strings, not floating-point approximations. Symbols
and assets use the common ASCII uppercase namespace, e.g. ARRRUSDT/USDT; map
native Kraken aliases or Gate separators inside the adapter. Orders use orderId,
clientOrderId, symbol, side, price, origQty, executedQty and cummulativeQuoteQty
(the historical spelling is intentionally preserved). Common statuses are NEW,
PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED; do not map unknown native
statuses to a completed fill. Fills use id, orderId, symbol, price, qty, quoteQty,
commission, commissionAsset, time (milliseconds).

Errors expose status, optional numeric code, execution_unknown and a timeout
flag. Raw remote bodies/messages, signatures and keys are never sent to the GUI.
An adapter exception can define these via the provided SpotError. A write timeout
or disconnection may mean execution succeeded: set execution_unknown=True. Generic
host/encoding failures after a write are also treated as uncertain. The engine
never transparently replays the write; it reconciles using the same client ID.
An unavailable lookup must remain UNKNOWN/review-required, not authorize a retry.

A venue without a real test endpoint must implement safe local validation and
return `request_sent:false`, as Gate does. Never simulate a test by placing then
canceling a funded order.

## Onboarding gate and future exchanges

1. Confirm Spot semantics fit v1 and document API capabilities/permissions.
2. Implement signing, native aliases, filters, normalization, clock/nonce handling
   and stable client IDs in the plugin. Unsupported API variants fail closed.
3. Add network-free fixtures for reads, precision, both sides, permissions,
   partial fills/cancels, ID round-trips, malformed responses, rate limits,
   timeouts and ambiguous writes. Run common engine tests with the catalog.
4. Review source/licenses, regenerate ZIP/catalog, merge the plugin PR.
5. Validate read-only against a disposable account through Tor. Any funded live
   acceptance requires separate explicit approval and strict small limits.

**Binance:** map exchangeInfo filters and order responses to this contract; use
its test-order endpoint, client IDs and appropriate Spot API permissions.
References: [official Spot REST documentation](https://developers.binance.com/docs/binance-spot-api-docs/rest-api),
[trading endpoints](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints).

**Kraken:** map asset/pair aliases, signing, monotonic private nonces and native
client identifiers inside the plugin. Persist mappings/nonces in state_dir and
cover concurrent clients and restart recovery. AddOrder validation must not
publish a real order.
References: [AddOrder](https://docs.kraken.com/api/docs/rest-api/add-order/),
[client order identifiers](https://docs.kraken.com/api/blog/cl-ord-id/).

An exchange outside this contract is deliberately excluded until an explicit
versioned protocol extension is justified. Adding a v1-compatible plugin requires
only changes in CEX_configs, not a wallet/core release.


## Optional HTTP diagnostics (MEXC source 0.1.1)

The MEXC HTTP module optionally exposes `diagnostic_capture(progress=None)` to
an updated engine host. It collects at most eight payload-free timing records
per adapter method. A supplied callback receives fixed phase names only; it
never receives URLs, hostnames, headers, bodies, financial quantities or IDs.
The host bounds phase notifications and owns their protocol correlation and
logging. Older hosts ignore this hook and older adapters work without it.

DNS, connection/TLS/proxy, headers wait, body and decode are coarse client-side
phases, not evidence that Tor or the exchange caused a delay. urllib remains
`connect_or_headers` plus body; no deadline/retry/routing change is made. aiohttp
callbacks use its documented TraceConfig signals, without inspecting params.
The hook adds no external dependency, trading capability or live permission.


## Persistent MEXC transport and resolver attribution (source 0.1.2)

The historical `UrllibJsonTransport` class now keeps one aiohttp session and
`asyncio.Runner` per transport/owning thread, for both read and signed calls.
It is isolated per helper/client and discarded on an explicit route revision.
Requests carry their own freshly signed headers; no session-level credentials,
shared cookie jar, environment proxies or automatic direct fallback are used.
The connector is bounded to four connections with the library's 10-second DNS
cache. DNS cache expiry is independent of market book/volume TTL; reuse never
extends a market observation or permits an uncovered maker.

Total-deadline reads retain the original minimum of socket/request deadlines.
Legacy socket-only calls retain connect/read socket limits and the engine host's
unchanged reply budget. aiohttp's internal stale-connection retry is explicitly
disabled through its 3.x `_retry_connection` switch: do not retry a write or
silently replay a read. Review this internal switch on aiohttp upgrades. All
redirects are rejected. Normal host EOF closes transport/session; thread owners
must close their own transport when using it outside the sequential host.

Trace callbacks select the current request's identity; persistent sessions must
never retain the first request's trace. `session_reused`, `connection_reused`
and `dns_cache_hit` make reuse observable. The DNS cache signal can also indicate
joining an already pending DNS resolution; it does not prove an address was
immediately available. `resolver_queue_ms` measures delay before an executor
worker starts; `resolver_call_ms` measures actual OS `getaddrinfo` wall time.
`resolver_inflight` identifies incomplete measurements at a request timeout.
The dedicated executor has at most two workers; no DNS server override, address
pinning, independent probe or new retry is introduced. Resolver result
normalization/flags remain aiohttp ThreadedResolver's responsibility.

An OS resolver call includes NSS, local stub/cache and upstream DNS waiting.
It does **not** identify router/provider processing time. Companion engine
passive network metadata can show interface errors, carrier changes and available
Wi-Fi counters, resolver-front-end class and configured DNS scope. It records
no IP addresses, DNS names, MAC addresses, SSIDs or packet/request contents.
Full router-vs-provider attribution requires independent measurements or router/
resolver telemetry and cannot be inferred from one client's timing alone.
This source candidate is separate from packaging, installation and live proof.


## Universal HTTP core (source candidate, 2026-10-07)

This supersedes the MEXC-only scope of the preceding 0.1.2 source note.
`shared/cex_plugin/http_pool.py`, `http_diagnostics.py` and `http_resolver.py`
are now the single authoritative session/pool/route/measurement implementation
for every generated adapter: Binance, CoinEx, Gate, Kraken, MEXC, Poloniex and
WhiteBIT, and future venues produced by `tools/build_catalog.py`.

The two thin `http.py` policy shims preserve existing wire contracts: MEXC/Gate
keep their error payload/string decoder and socket-only default limits; other
adapters keep Decimal JSON, 1 MiB bodies, exact HTTP 200 and total deadlines.
The standard shim rejects non-200 immediately without waiting for the body;
the legacy shim reads its error payload as before. Per-client/thread/route pools,
no environment proxies/cookies/hidden retries, per-request signing headers,
10-second DNS cache, explicit close and resolver measurements apply universally.
No venue signing, nonce, order identity, rate-limit or experimental acceptance
policy is replaced by the common transport.

Version numbers identify each plugin's source/bundle revision, not a MEXC-only
feature switch. MEXC advances to 0.1.3 and the other published adapters to 0.1.1.
Future adapters should reuse the standard generated transport rather than
introduce a second HTTP stack. Universal implementation is not proof of funded
acceptance at every venue or of immunity to all network failures.
