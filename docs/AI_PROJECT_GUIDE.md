# CEX configs/plugins: AI and contributor project guide

Read [AGENTS.md](../AGENTS.md) first. Reviewed on 2026-10-06 against its recorded
source revision. Despite its name, this repository ships **executable exchange
adapters**, not only configuration data.

## Role in the application

[Wallet](https://github.com/p2piratedotcom/P2Pirate-ALPHA/blob/cheetahdex/AGENTS.md)
owns installation/consent and presents venues.
[MM_Engine](https://github.com/p2piratedotcom/MM_Engine/blob/main/AGENTS.md)
verifies/hosts the plugins and owns strategies, pricing, shared reservations,
hedging and rebalance policy. [SDK](https://github.com/p2piratedotcom/komodo-defi-sdk-flutter/blob/cheetahdex/AGENTS.md)
wraps KDF; its price-data package is not this Spot trading interface.
[Assets](https://github.com/p2piratedotcom/Assets/blob/main/AGENTS.md)
contains wallet coin configuration, not exchange credentials.

This repo owns native endpoints/signing, aliases, precision/permission filters,
rate/deadline handling, stable client/native IDs and normalized Spot replies.
API credentials are private runtime bootstrap data, never catalog/config entries.

## Authoritative and generated files

| Path | Role and editing rule |
| --- | --- |
| `PROTOCOL.md` | Versioned Spot v1 contract; read before adapter work |
| `plugins/<venue>/config.json` | Public settings, venue/version, time budgets and credential field *names* |
| `plugins/<venue>/src/cex_plugin/` | Standalone reviewable runtime source; selected shared copies are generated |
| `shared/cex_plugin/` | Authoritative helpers copied into the experimental plugin families by the builder |
| `plugins/<venue>/adapter.zip` | Deterministic executable bundle, generated from that source |
| `catalog.json` | Protocol/version inventory and LICENSE/config/ZIP SHA-256 |
| `tools/build_catalog.py` | Copies shared helpers and rebuilds bundles/catalog |
| `tests/test_adapters.py` | Network-free fixture contracts; not funded acceptance |
| `docs/EXCHANGE_ASSESSMENT.md` | Exchange/product decisions, references and outstanding acceptance |
| Venue READMEs | Venue-specific semantics and limits |

The builder's shared-helper set currently includes Binance, CoinEx, Kraken,
Poloniex and WhiteBIT; MEXC/Gate have their preserved independent source. Inspect
the actual builder before editing a generated copy. A shared source change needs
version bumps for every already-published affected adapter, copied source, ZIPs
and catalog regeneration together. Docs outside runtime files do not change ZIPs
and should not regenerate artifacts merely to update instructions.

## Protocol in brief

Detailed source of truth is [PROTOCOL.md](../PROTOCOL.md): protocol 1 / public
`p2pirate-spot-v1`. One child process hosts an adapter instance. Parent bootstrap
via stdin supplies config, private state directory, credentials and explicit
routing; stdout is reserved for bounded newline-JSON protocol frames. The host
checks package integrity and a capability handshake, then exchanges correlated
requests/results/errors. No secret arguments/env dumps or debug prints on stdout.

`create_client` must implement time synchronization, symbol rules/permissions,
book/ticker reads, authorized symbols, Spot balances, fees, open orders/fills,
validation, LIMIT placement, query and cancellation. Decimal values are finite
strings. KDF IDs and exchange aliases are different namespaces: normalize native
Kraken/Gate forms inside the adapter, not in strategy math. Preserve canonical
status semantics, original IDs and the historical `cummulativeQuoteQty` spelling.
Unknown status/data must not become a completed fill or fabricated balance.

Time/precision/deadline results must retain their meaning. Book observations are
dated at request start; returning a delayed snapshot does not make it fresh.
Per-subrequest budgets cannot each consume the original total timeout. Missing
quantity/price grids, unsupported sides or account products fail closed.

All remote calls, including public time/market reads, use the host's selected
Tor/direct transport. No environment override or silent direct fallback. Never
send credentials through redirects or log signed URLs/headers/bodies.

## Orders, identity and uncertain outcomes

A private write timeout/disconnection may have executed. Mark execution unknown
and reconcile using the **same** client/native identity. Do not map unavailable
lookup to order absence, retry blindly, clear attempt/nonce files or change an ID
because a call timed out. Native mappings, nonces and confirmed terminal snapshots
can be persisted under the private host-provided state directory; keys/secrets
cannot. Atomic writes and cross-process synchronization matter because multiple
public/private clients are intentional. A key's external consumers do not join
this application's nonce lock; follow venue guidance on dedicated credentials.

Validation is never a funded place-then-cancel test. When no real test endpoint
exists, use safe local validation with `request_sent:false`. That result does not
prove exchange write permission. Transfers, derivatives/margin and arbitrary
non-USDT hedge anchors are outside v1 and cannot be enabled by a new config flag.
A non-fitting exchange needs an explicit versioned protocol/core review.

## Venue acceptance status

MEXC Spot V3 and Gate Spot V4 are the original extracted implementations. Their
fixture/catalog history is not proof of all live products/accounts. Binance,
CoinEx, Kraken, Poloniex and WhiteBIT are explicitly experimental and untested
against live/sandbox accounts and funded trades at this source. Keep those labels
until acceptance is documented; showing them in the catalog/UI does not change
that status. Read the relevant `plugins/<venue>/README.md` where supplied and the
assessment for native account/product exclusions.

## Build, fixtures and publication

Python >=3.11. The packaging tool uses the standard library; runtime adapters may
use only the standard library and engine-provided aiohttp unless the host contract
is explicitly extended. Do not assume an external SDK/system Python is installed
for an engine-hosted adapter. Shared helpers currently use Linux `fcntl`.

For an approved runtime-source change:

```sh
python3 tools/build_catalog.py
python3 -m unittest discover -s tests -v
git diff --exit-code -- catalog.json plugins
```

The last command is the **clean consistency check after generation is committed**,
not a claim that a new source change should produce no diff. CI rebuilds in a
checkout and rejects mismatches. ZIP entry order, permissions and timestamps are
fixed; compressed byte identity assumes the same Python/zlib toolchain. Commit
source and generated copies/ZIP/catalog together, with applicable version bumps.

Current CI runs on pushes/PRs and performs that generation/fixture check. It does
not contact live accounts, certify authentication or place funded orders. Any
read-only account acceptance or funded trial needs a separate explicit plan and
a disposable/limited setup; never import operator credentials to satisfy a fixture.

Wallet downloads pin one `main` commit and verify repository identity, paths,
protocol, sizes and hashes; engine verifies again. Hashes bind contents to the
selected snapshot, not independent trust in the publisher. Python subprocesses
contain protocol failures but are not OS security sandboxes. Install only reviewed
source. Updates are explicit and stop/reconcile the old trading session before
reconnecting; no hot-loaded code or automatic live permission. A docs-only commit
may advance the repository snapshot without changing runtime bundle hashes.

The development override `P2PIRATE_CEX_PLUGIN_DIR` selects a local checkout; it is
not normal release/update behavior and does not make candidate code trusted.
Keep it separate from funded profiles and remove it before validating normal
installation behavior.

## Change routing and further reading

If an issue is native signing/nonce/filter/status behavior, work here. If it is
funding allocation, maker pricing, coverage or hedge orchestration, work in
MM_Engine. Installation consent/version UI belongs to the wallet. A valid v1
adapter normally needs no app/core registry patch; a contract extension does.

- [Spot v1 protocol](../PROTOCOL.md)
- [Exchange assessment](EXCHANGE_ASSESSMENT.md)
- [Shared helper source policy](../shared/README.md)
- [Binance](../plugins/binance/README.md), [Kraken](../plugins/kraken/README.md)
- [CoinEx](../plugins/coinex/README.md), [WhiteBIT](../plugins/whitebit/README.md), [Poloniex](../plugins/poloniex/README.md)
- [Repository README](../README.md), [LICENSE](../LICENSE)

## A safe starting prompt for an AI contributor

```text
Read AGENTS.md and docs/AI_PROJECT_GUIDE.md at this checkout's revision.
My task is: [describe the requested change].
Identify the component boundary, relevant source/contracts, current limitations,
validation appropriate to this scope, and whether these guides need updating.
Use disposable fixtures; do not start a real wallet/service or submit funded
operations without the operator's explicit authorization.
Report facts separately from assumptions and checks performed from checks not run.
```

## Maintenance and PR handoff

Recheck this guide and `AGENTS.md` in the same PR when architecture, public
contracts, ownership, safety, persistence, routing, supported platforms,
dependencies, setup/tests, generated outputs, provenance or acceptance limits
change. Update linked specifications too when their contract changed. The PR
maintenance checklist requires either the corresponding edits or an explicit
no-update reason; a checkbox alone does not make an old statement true.

Keep version claims dated and tied to source/release evidence. Do not copy a local
runtime path, user account, balance, API credential or private monitoring result
into public guidance. Prefer links to manifests/constants over repeated moving
pins or exhaustive API copies. A cross-repository change needs companion PRs and
compatibility notes; do not assume that merging one repo deploys the whole system.

A useful AI handoff states: repository and commit, requested scope, relevant
modules/contracts, proposed change, risks, exact checks actually performed,
checks not run, companion repositories affected, and guide sections updated.
Implementation, fixture tests, a compatible release, installation, startup,
read-only account validation and funded acceptance are separate milestones.
