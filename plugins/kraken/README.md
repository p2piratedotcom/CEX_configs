# Kraken cash Spot REST — experimental, untested

No live/sandbox account or funded-trade acceptance has been performed. Only
ordinary cash Spot USDT LIMIT/GTC routes with fixed tick_size/lot_decimals are
eligible. Margin, credit buying power, futures, xStocks, Earn/staking balances,
subaccounts and OTP-protected API keys are not advertised. A plugin does not
make an unlisted coin/pair available.

Use a dedicated key with Query Funds, Query Open Orders & Trades, Query Closed
Orders & Trades and Query Ledger Entries; explicit live use also requires Create
& Modify Orders and Cancel & Close Orders. No withdrawal or transfer endpoints
are used. Do not share this key with other software: its nonce lock can only
coordinate this application's plugin processes.

- API-Sign is HMAC-SHA512 over path + SHA256(nonce + exact form payload), using
  the base64-decoded secret. Monotonic int64 nonces are persisted before sending;
  the complete signed call is locked per key across plugin processes.
- AssetPairs metadata supplies fixed ticks, lot grids, costmin and ordermin.
  Native pair/asset names use metadata aliases, including XBT/BTC and XDG/DOGE;
  X/Z prefixes are never blindly stripped. Unsupported/ambiguous pairs fail.
- BalanceEx cash available is balance minus hold_trade. Unused credit is never
  counted, used credit rejects the cash-only account, and ambiguous Earn/staking
  extensions are excluded rather than combined with ordinary cash. GetApiKeyInfo
  supplies create/cancel permissions for canTrade; balance success is not treated
  as proof of write permission. Public asset aliases are cached for five minutes.
- TradeVolume maker/taker percentages become ratios. AddOrder requests prefer
  fee in quote currency, but that preference is not treated as a guarantee.
  QueryTrades/TradesHistory request ledger IDs; QueryLedgers supplies the actual
  fee amount and currency for each fill. Missing, mismatched or multiple fee
  currencies that cannot fit v1 fail closed; no fabricated USDT commission.
- Original IDs map to deterministic short UUID cl_ord_id, not non-unique userref.
  Durable attempts prevent replay after timeout or restart. Known orders query
  by native txid; uncertain submissions query OpenOrders and ClosedOrders with
  the exact client ID. Missing or ambiguous history requires reconciliation.
- Open orders and fills expose only this plugin's journaled cash identities.
  Foreign/margin identities cannot become engine hedge orders. Order-specific
  fills must reconcile executed quantity. Native fills/ledger reads are paged
  in bounded batches under one total deadline (100 fills, 400 ledger entries).
- AddOrder validate=true is safe native preflight: no real order is created,
  even when live permission is off. A successful live submit binds its txid and
  reads back status; it never guesses FILLED or NEW from an acknowledgement.
  CancelOrder's count/pending acknowledgement is followed by QueryOrders; only
  confirmed terminal status completes cancellation.
- Every request uses the wallet route without redirects/direct fallback.
  Native bodies: 1 MiB, public AssetPairs metadata: 8 MiB; host normalized output:
  1 MiB. Total commands, private lock waits and lookups are bounded. HTTP 418/429
  honors Retry-After with at least 120 seconds process-local cooldown; private
  API rate-limit errors have a 60-second cooldown. No order write is retried.
  Tor exits, regional restrictions, IP allowlists and missing permissions can
  prevent account access; long Tor/ledger reads may exhaust the deadline safely.

Primary docs: [authentication](https://docs.kraken.com/exchange/guides/rest/authentication),
[pairs](https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs),
[assets](https://docs.kraken.com/api-reference/market-data/get-asset-info),
[balances](https://docs.kraken.com/api-reference/account-data/get-extended-balance),
[key permissions](https://docs.kraken.com/api-reference/account-data/get-api-key-info),
[fees](https://docs.kraken.com/api-reference/account-data/get-trade-volume),
[submit and validation](https://docs.kraken.com/api-reference/trading/add-order),
[query](https://docs.kraken.com/api-reference/account-data/query-orders-info),
[open](https://docs.kraken.com/api-reference/account-data/get-open-orders),
[closed](https://docs.kraken.com/api-reference/account-data/get-closed-orders),
[fills](https://docs.kraken.com/api-reference/account-data/query-trades-info),
[ledger fees](https://docs.kraken.com/api-reference/account-data/query-ledgers),
[cancel](https://docs.kraken.com/api-reference/trading/cancel-order).

Acceptance still required: exchange-specific fixtures for signing, concurrent
nonce persistence, aliases, partial fills, actual ledger fee currency, and lost
submit/cancel replies; disposable account/read-only Tor checks; explicitly
authorized funded validation. Catalog CI/host checks do not certify this API.
