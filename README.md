# P2Pirate CEX plugins

Public configuration **and executable Spot adapters** for P2Pirate Trading Engine.
API credentials are never committed here. The wallet stores each user's keys in
Linux Secret Service, under their existing wallet profile.

## Components

```
catalog.json                 protocol version, file names and SHA-256 hashes
LICENSE                      The Unlicense
plugins/<venue>/config.json  public API settings and display name
plugins/<venue>/src/cex_plugin/*.py  reviewable adapter source
plugins/<venue>/adapter.zip  deterministic runtime bundle of that source
tools/build_catalog.py       reproducible packaging and catalog generation
shared/cex_plugin/*.py        authoritative helpers for experimental plugins
docs/EXCHANGE_ASSESSMENT.md  compatibility decisions and primary sources
tests/test_adapters.py       network-free contract fixtures
PROTOCOL.md                  Spot v1 interface and onboarding requirements
```

The original catalog contains **MEXC Spot V3** and **Gate Spot V4**. Their adapters
were extracted from `p2piratedotcom/MM_Engine` at
`e365642c3fc0395977b69f9ce02e1d8765243813` (Unlicense). The API implementations,
signing and response normalization are preserved; imports/types were moved into
the plugin namespace and a configuration-based factory was added. Binance is now included as an experimental plugin. Kraken remains under
compatibility review and is not advertised as supported in this snapshot.

## Experimental additions

The new exchange batch adds CoinEx, WhiteBIT, Poloniex and Binance as separate plugins.
Each is explicitly **experimental and not tested against live accounts, sandboxes
or funded trades**; existing catalog/MEXC/Gate CI does not validate their APIs.
Only plugins present in `catalog.json` are downloadable in the current snapshot.
See [the exchange compatibility assessment](docs/EXCHANGE_ASSESSMENT.md)
for exclusions, primary documentation and outstanding acceptance work.

New plugin helpers are maintained in [shared/](shared/README.md) and copied into
standalone source/bundles by the builder. This batch changes CEX_configs only:
no engine, wallet or credential contract changes.

## Build and validation

Python 3.11+; building these bundles requires only the standard library:

```sh
python3 tools/build_catalog.py
python3 -m unittest discover -s tests -v
```

Commit source, generated ZIPs and catalog together. CI rebuilds and rejects a
mismatch. ZIPs have fixed timestamps, sorted entries and fixed permissions.
Byte-identical compressed output assumes the same Python/zlib toolchain.

## Installation and updates

The wallet asks to download plugins after installing the compatible trading
engine. Downloads are pinned to one `main` commit in this repository, with
repository identity, protocol, paths, sizes and file hashes checked by the wallet
and checked again by the engine. Installed snapshots remain available offline.
The trading-engine page has a separate **CEX plugins** update action. Updates
pause and withdraw orders, respect the active-swap shutdown guard, clear live
permission and reconnect in preview; downloaded code is never hot-loaded into a
running trading session. Existing MEXC/Gate keys do not move.

Hashes verify consistency and integrity; they are not independent signatures or
proof that code is trustworthy. Anyone who can publish this repository can supply
executable adapters. Only reviewed plugin source belongs here. A child process
contains failures but is **not an operating-system security sandbox**.

The paired new wallet and engine must be released before this catalog is used in
production. Engine releases must advertise `plugin_protocol: 1`; older releases
are rejected before wallet secrets or live permissions are passed to them.

For development only, set `P2PIRATE_CEX_PLUGIN_DIR` to this checkout's absolute
path and use a checksum-verified local engine candidate. This override takes
precedence over downloaded snapshots; remove it to test normal download/update.
Do not test candidate plugins against funded wallets or enable live orders merely
to validate installation.

## Adding an exchange

Add `plugins/<venue>/` following [PROTOCOL.md](PROTOCOL.md), implement the complete
Spot contract and add fixture tests. Build and commit the catalog. No registry,
strategy or GUI change is needed for an exchange that fits v1. Exchanges requiring
new trading products, unsupported credentials or different engine policy need an
explicit protocol/core enhancement and compatibility review before onboarding.
