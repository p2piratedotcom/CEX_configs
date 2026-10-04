# Poloniex modern cash Spot REST — experimental, untested

No live/sandbox account or funded-trade acceptance has been performed.
Cash Spot LIMIT/GTC orders, USDT routes, HMAC API key + secret only. Require
Spot account/balance/order/fee/trade reads and Spot trading permissions for
explicit live use. Legacy API, Ed25519, borrowed/margin orders and transfers
are outside this plugin.

- HMAC-SHA256 + Base64: GET parameters are ASCII sorted and percent encoded;
  JSON request bodies are signed verbatim as the official signature example
  specifies. Timestamp-only DELETE is supported. No SDK code is copied.
- Fixed priceScale/quantityScale grids and native min/max quantities/notionals;
  highestBid and lowestAsk apply only to their respective sides. No hidden
  rounding after engine sizing.
- Only NORMAL Spot accounts with finite nonnegative available/hold balances;
  order creation explicitly sends allowBorrow=false. Orders with loan=true or
  missing loan/account identity fail closed; do not use this for margin accounts.
- Durable native client-ID mapping, lookup through `/orders/cid:...`; creation
  acknowledgment alone is not a fill. Cancellation acknowledgment
  PENDING_CANCEL remains uncertain until an authoritative order query resolves
  it. No uncertain order write is replayed.
- Specific-order fills use `/orders/{id}/trades`; generic history uses the native
  plural `symbols` filter. History older than 180 days may be unavailable.
  Open-order pages at the 2,000-row bound fail rather than silently omit rows.
- Safe local preflight (`request_sent:false`), never a funded test. Account
  state/read success is not proof of API trading permission. Every HTTP call
  uses the wallet-supplied route and bounded command budget.

Official docs: [auth](https://api-docs.poloniex.com/spot/api/),
[signature reference](https://github.com/poloniex/polo-spot-sdk/blob/BRANCH_SANDBOX/signature_demo/signature_python_demo.md),
[market rules/time](https://api-docs.poloniex.com/spot/api/public/reference-data),
[market data](https://api-docs.poloniex.com/spot/api/public/market-data),
[accounts/fees](https://api-docs.poloniex.com/spot/api/private/account),
[orders](https://api-docs.poloniex.com/spot/api/private/order),
[fills](https://api-docs.poloniex.com/spot/api/private/trade).
