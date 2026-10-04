# CoinEx Spot V2 — experimental, untested

No live/sandbox account or funded-trade acceptance has been performed.
Cash Spot LIMIT orders through USDT only; API key + secret, with account/order
read permissions and Spot trading permission for explicit live use. No transfers.

- HMAC-SHA256 signs the exact method, `/v2` path/query, body bytes and timestamp.
- Native market precision maps to fixed engine grids; min_amount is a base
  quantity, checked separately before any write. The common quote minimum is
  derived conservatively from a fresh ask. No price/quantity rounding is hidden
  from the engine.
- Native client IDs are deterministic 32-character hashes; original IDs and
  exchange IDs are durably journaled before submission. Creation replies are
  followed by status queries, not assumed to be final.
- An interrupted submit without an exchange ID searches at most five pages each
  of pending/finished orders within the total time budget. Missing results remain
  unresolved. Confirmed terminal snapshots survive restart; zero-fill canceled
  orders are removed by CoinEx, so a lost cancellation reply can still require
  manual reconciliation. Order writes are never replayed automatically.
- Open-order view is bounded at 100 records; full pages fail rather than silently
  omit orders. Order-specific fills fail if another page exists. History expiry
  and unavailable pages cannot authorize new writes.
- Validation endpoint is local/read-only (`request_sent:false`), not an exchange
  test-order endpoint or a test of API trading permission. All calls use the
  wallet's supplied Tor/direct route; no direct fallback or authenticated redirects.

Official docs: [auth](https://docs.coinex.com/api/v2/authorization),
[time](https://docs.coinex.com/api/v2/common/http/time),
[markets](https://docs.coinex.com/api/v2/spot/market/http/list-market),
[depth](https://docs.coinex.com/api/v2/spot/market/http/list-market-depth),
[balances](https://docs.coinex.com/api/v2/assets/balance/http/get-spot-balance),
[fees](https://docs.coinex.com/api/v2/account/fees/http/get-account-trade-fees),
[create](https://docs.coinex.com/api/v2/spot/order/http/put-order),
[status](https://docs.coinex.com/api/v2/spot/order/http/get-order-status),
[cancel](https://docs.coinex.com/api/v2/spot/order/http/cancel-order),
[fills](https://docs.coinex.com/api/v2/spot/deal/http/list-user-order-deals).
