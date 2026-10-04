# Exchange onboarding assessment

Reviewed 2026-10-04 against the released Linux engine v0.2.0, Spot plugin
protocol v1. Decisions are deliberately conservative: cash Spot LIMIT orders,
USDT hedge routes, API key + secret, fixed executable price/quantity grids,
bounded reconciliation and wallet-supplied network routing. No engine or wallet
changes are made by this batch. Deferred does not mean an exchange can never be
supported; a narrower account variant or a future versioned protocol may allow it.

## Requested exchanges

| Exchange | Decision | Reason / boundary | Official references |
|---|---|---|---|
| CoinEx | Experimental plugin | Spot V2; key + secret, decimal grids, native client IDs mapped durably. Canceled zero-fill orders disappear remotely: preserve confirmed cancellation locally; missing/uncertain results require manual reconciliation. | [Authentication](https://docs.coinex.com/api/v2/authorization), [markets](https://docs.coinex.com/api/v2/spot/market/http/list-market), [order status](https://docs.coinex.com/api/v2/spot/order/http/get-order-status) |
| WhiteBIT | Experimental plugin | V4 cash trading account only, explicit tickSize/stepSize. Persist/serialize nonces and cash-order IDs. Collateral/margin/futures and B2B-specific history are not advertised. | [Authentication](https://docs.whitebit.com/api-reference/authentication), [market info](https://docs.whitebit.com/api-reference/market-data/market-info), [history](https://docs.whitebit.com/api-reference/trading/query-executed-orders) |
| Poloniex | Experimental plugin | Modern Spot REST HMAC credentials, fixed grids, cash orders with allowBorrow=false; client-ID queries and confirmed cancellation. Legacy REST, Ed25519 keys and margin are excluded. | [Authentication](https://api-docs.poloniex.com/spot/api/), [market rules](https://api-docs.poloniex.com/spot/api/public/reference-data), [orders](https://api-docs.poloniex.com/spot/api/private/order) |
| Bitfinex | Deferred; no plugin published | Native prices use five significant digits. V1 has one fixed price_step rather than a price-dependent significant-digit policy. Silently allowing native price truncation or changing the hedge price after engine sizing is unsuitable. Native nonces/cid are adapterable and are not the reason for exclusion. | [Precision rules](https://docs.bitfinex.com/docs/introduction), [pair rules](https://docs.bitfinex.com/reference/rest-public-conf), [orders](https://docs.bitfinex.com/reference/rest-auth-submit-order) |
| Upbit | Deferred; no plugin published | USDT order ticks change at price bands. Full fidelity needs executable-price-dependent rules instead of a single fixed grid. JWT signing and identifier lookup can be adapted; credentials themselves are not a blocker. Mandatory API IP restrictions also need operational consideration with Tor. | [USDT tick policy](https://docs.upbit.com/kr/docs/usdt-market-info), [authentication](https://docs.upbit.com/kr/reference/auth), [order lookup](https://docs.upbit.com/kr/reference/get-order) |
| OKX | Deferred; no plugin published | Mandatory API passphrase is a third credential. Current wallet secret entry/host contract accepts only key + secret. Putting it in public settings or concatenating secrets would bypass that boundary. | [Official API FAQ](https://www.okx.com/en-us/help/api-faq) |
| KuCoin | Deferred; no plugin published | Mandatory KC-API-PASSPHRASE; requires an explicit credential contract/wallet extension. | [Authentication](https://www.kucoin.com/docs-new/authentication) |
| Bitget | Deferred; no plugin published | Mandatory ACCESS-PASSPHRASE; requires an explicit credential contract/wallet extension. | [REST authentication](https://www.bitget.com/docs/classic/rest-api) |
| Bybit | Deferred; no plugin published | Current unified accounts have liabilities, borrowing and cross/portfolio collateral. Deprecated Spot free/locked fields cannot stand in for cash available to trade. USD collateral or transferable balances are not the same as per-asset Spot trading balances; support needs an explicit account/collateral policy or a separately validated cash-only subset. | [Wallet balance](https://bybit-exchange.github.io/docs/v5/account/wallet-balance), [transferable balances](https://bybit-exchange.github.io/docs/v5/asset/balance/all-balance), [orders](https://bybit-exchange.github.io/docs/v5/order/create-order) |
| Bithumb | Deferred; no plugin published | The public market list on the review date contained KRW and BTC quote markets, no USDT quote route. Using another hedge anchor requires an engine policy/protocol extension. JWT is adapterable and not a blocker. | [Market discovery](https://apidocs.bithumb.com/reference/거래-대상-목록-조회), [public market list](https://api.bithumb.com/v1/market/all) |

Bithumb discovery was unauthenticated: 482 KRW and 13 BTC markets on 2026-10-04.
This records a discovery observation, not an invariant about future listings.

## Status and acceptance limits

**The three new plugins are experimental and have not been tested against live
exchange accounts, exchange sandboxes or funded trades.** Their display names
include `experimental; untested`, and public settings record this status.
Documentation review, syntax/package checks and the existing catalog CI are not
exchange acceptance tests. Existing MEXC/Gate fixtures do not validate these APIs.
New venue fixtures and disposable-account read-only/Tor checks remain necessary
before considering them validated for production; funded acceptance needs
separate explicit authorization.

- API permissions: account/balance/order/trade reads and Spot order creation and
  cancellation when live trading is explicitly enabled. A successful balance
  read is not proof that the key has trading permission. No withdrawal or
  transfer permissions/endpoints are used.
- Preflight is local, read-only validation of native precision/minimums; it is
  not proof of exchange acceptance or API write permission. It never places a
  funded order to simulate a test.
- Stable native IDs, atomic private journals and no automatic write replay.
  Unknown/expired/missing remote history fails closed for manual reconciliation.
- HTTP responses, total RPC time, source bundles and lookup pages are bounded.
  Rate-limit failures are surfaced without retrying order writes or bypassing Tor.
- The adapter maps exchange wire formats; pricing, hedging, premium, sizing,
  strategies, reservations and engine journals remain in MM_Engine.
- New adapter/signing code is original implementation of documented API facts,
  under this repository's Unlicense. Common wire dataclasses derive from the
  existing Unlicense Gate plugin. No exchange SDK implementation is copied.
