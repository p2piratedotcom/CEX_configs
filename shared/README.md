# Plugin-local shared source

`cex_plugin/` is the authoritative source for common helpers in the new
CoinEx/WhiteBIT/Poloniex adapters. The packaging tool copies these files into each
plugin's `src/cex_plugin/` and builds its standalone ZIP. Edit helpers here and
regenerate every affected bundle; never maintain divergent generated copies.
Changing shared runtime source requires bumping the version of every already
published affected plugin, regenerating its ZIP and catalog hashes.

These helpers belong to downloadable plugins, not to the engine. They implement
bounded wallet-routed HTTP, wire dataclasses, finite decimal validation, private
nonce/client-ID persistence and command budgets. They do not implement strategy
or pricing policy. Existing MEXC/Gate source/bundles are unchanged.

Linux-only (`fcntl`), matching the currently released engine. Runtime dependencies
are Python standard library and the engine's bundled aiohttp; no system Python,
external SDK or additional package installation is required.

Private state holds hashes of key identities, nonces, original order identifiers,
exchange order IDs and confirmed terminal snapshots; no API keys or secrets.
Each key may retain up to 10,000 attempts / 8 MiB of state. Reaching the bound fails
closed; do not clear this state to replay an uncertain order. Use a unique API key
for this application: external consumers do not participate in its nonce lock.
