# Vendored UCP schemas

Pinned upstream commit: `f878636e17164ff259492825e79b3564e993cfee` from [`Universal-Commerce-Protocol/ucp`](https://github.com/Universal-Commerce-Protocol/ucp).

The AgentOrder v0.2 schema references these canonical UCP `$id`s:

- `https://ucp.dev/schemas/shopping/types/buyer.json`
- `https://ucp.dev/schemas/shopping/types/fulfillment_destination.json`
- `https://ucp.dev/schemas/shopping/types/line_item.json`

The three public canonical URLs returned HTTP 404 on 6 September 2026. Their source files and recursive local `$ref` dependencies are vendored here for deterministic, offline validation. The original `$id` values are intentionally unchanged.
