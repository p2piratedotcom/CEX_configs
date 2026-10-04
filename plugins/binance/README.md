# Binance.com cash Spot — experimental, untested

No live/sandbox account or funded-trade acceptance has been performed. This is
Binance.com Spot REST V3 with HMAC API key + secret; Binance.US, RSA/Ed25519 keys,
margin, futures, lending, transfers and SOR are not advertised. Only supported
USDT quote markets with online Spot LIMIT/GTC orders are eligible. Adding the
exchange does not make an unlisted coin/pair available.

- HMAC-SHA256 signs the exact URL-encoded parameters, millisecond timestamp and
  configured recvWindow; wallet Tor/direct routing is mandatory. Region, API/IP
  restrictions and Tor exit restrictions can still prevent exchange access.
- PRICE_FILTER and LOT_SIZE provide fixed executable grids. Native price/base
  quantity bounds and both MIN_NOTIONAL/NOTIONAL limits are checked. Dynamic
  percent-price, position and order-count filters are validated by the exchange's
  safe `/api/v3/order/test` preflight: it does not reach the matching engine or
  create an order. TRADE permission is required even for this validation.
- Account balances use the Spot free/locked fields and native canTrade flag.
  Reads require USER_DATA permission; explicit live use also needs TRADE.
  Never grant withdrawal/transfer permissions for this plugin.
- Account commission includes standard, special and tax rates. V1 has no
  per-side fee field: use the larger buyer/seller addition as a conservative
  bound. BNB discounts are not assumed. Fill accounting uses actual fee assets.
- Stable client IDs plus a durable attempt journal prevent automatic submit
  replay after timeout, restart or terminal fills. Creation uses RESULT rather
  than ACK. Queries validate the journaled native order ID and market.
- Cancellation can change Binance clientOrderId. Validate origClientOrderId,
  retain the exchange order ID, and cache confirmed FILLED/CANCELED snapshots.
  A lost cancel reply is reconciled by exchange ID rather than the old client ID.
- Unavailable negative historical cumulative quote amounts are not replaced by
  zero. Missing history, uncertain writes and incomplete order-fill pages fail
  closed for manual reconciliation. OrderId/time filters cannot be combined
  natively; time bounds are applied locally to order-specific fills.
- One total RPC budget, no write retries or direct fallback. Bodies are bounded
  at 1 MiB except public exchangeInfo metadata (8 MiB); the normalized protocol
  response remains bounded by the host at 1 MiB. Catalog requests disable
  unused permissionSets: the default response exceeded 8 MiB on the review date,
  while showPermissionSets=false returned 6,670,024 bytes. HTTP 418/429 honors Retry-After
  with at least 120 seconds local cooldown. Cooldown is process-local; API-key
  consumers outside this app must coordinate their limits independently.

Primary docs: [REST and authentication](https://developers.binance.com/docs/binance-spot-api-docs/rest-api),
[filters](https://developers.binance.com/docs/binance-spot-api-docs/filters),
[market data](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints),
[account, commissions and fills](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints),
[orders, safe test and cancellation](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints).

Acceptance still required: exchange-specific fixtures for signatures, filters,
commission components, canceled-client-ID changes, partial fills and lost replies;
disposable account/read-only Tor checks; explicitly authorized funded validation.
