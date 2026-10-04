# WhiteBIT cash Spot V4 — experimental, untested

No live/sandbox account or funded-trade acceptance has been performed.
Cash trading-account Spot LIMIT orders through USDT only. Key + secret require
trading-account balance/order/history/fee reads and order creation/cancellation
permissions for explicit live use. No transfers, collateral, margin or derivatives.
Use a unique API key for this app; external consumers cannot share its nonce lock.

- Base64 payload + HMAC-SHA512 signature, monotonic nonce persisted before HTTP;
  complete signed requests are serialized across processes for the same key.
  Timeouts/process termination cannot cause reuse of a previously issued nonce.
- Explicit tickSize/stepSize govern executable grids; moneyPrec is not treated
  as a price tick. Missing explicit grids fail closed. minAmount/minTotal/maxTotal
  are enforced without silently changing price or quantity.
- Balances come from the cash trading account, never collateral balance APIs.
  Native fee percentages are divided by 100 into engine ratios.
- Stable client-ID hashes and private attempt journal distinguish our cash
  orders from the combined cash/collateral open-order/history endpoints.
  `positionSide:BOTH` is permitted only for known cash identities; LONG/SHORT
  and unknown identities cannot become our hedge orders.
- Query tries open orders then client-filtered history; historical `id` and
  market-keyed envelopes are normalized correctly. Confirmed terminal snapshots
  are retained locally. History has six-month retention, and B2B cancellations
  may be absent; these account variants are not advertised as validated. Missing
  history remains unresolved for manual reconciliation, never an automatic retry.
- Open view capped at 100 rows; order-specific fill pages hitting their bound
  fail closed. Without an order ID the fill view includes only journaled cash
  orders, not unknown collateral trades.
- Safe local preflight (`request_sent:false`) is not a funded test or proof of
  write permission. Wallet routing is honored for every public/private call.

Official docs: [auth](https://docs.whitebit.com/api-reference/authentication),
[markets](https://docs.whitebit.com/api-reference/market-data/market-info),
[time](https://docs.whitebit.com/api-reference/market-data/server-time),
[book](https://docs.whitebit.com/api-reference/market-data/orderbook),
[balances](https://docs.whitebit.com/api-reference/trading/trading-balance),
[fees](https://docs.whitebit.com/api-reference/trading/query-market-fee-single),
[create](https://docs.whitebit.com/api-reference/trading/create-limit-order),
[open](https://docs.whitebit.com/api-reference/trading/query-unexecuted-orders),
[history](https://docs.whitebit.com/api-reference/trading/query-executed-orders),
[fills](https://docs.whitebit.com/api-reference/trading/query-executed-order-deals),
[cancel](https://docs.whitebit.com/api-reference/trading/cancel-order).
