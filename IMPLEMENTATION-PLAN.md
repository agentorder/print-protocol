# AgentOrder v0.2 reference implementation plan

**Status:** implementation plan; no live Stripe, UCP, AP2, MCP, or A2A credentials are required to complete the local reference implementation.

## Decision

Replace the single-printer v0.1 demo with a hosted-printer adapter. SQLite remains the local persistence layer. The adapter owns protocol translation, UCP discovery, request verification, quote state, mandate verification, and Stripe Connect orchestration; the printer configures capabilities and pricing in a UI later.

The first UCP platform is the AgentOrder reference agent, exposed through an MCP server and an A2A agent card. It advertises `org.agentorder.shopping.print_quote` version `2026-09-06`, so the extension is negotiated in the first pilot.

## Hosted-printer provisioning

1. Printer signs up at AgentOrder.tech, proves control of its domain or chooses an AgentOrder-hosted profile URL, and accepts the hosted-service authorization.
2. Printer connects Stripe via Connect. Store only Stripe account identifiers and encrypted platform credentials; never store card data.
3. Printer selects accepted named card presets, sides, stock GSM weights and finishes, quantities, lead-time rules, and whether quotes are automatic or manually answered.
4. Service creates a per-printer tenant, signing key, UCP profile, capability `config`, and audit namespace.
5. Stripe Connect sandbox onboarding is requested only after the manual quote flow passes.
6. Printer publishes `/.well-known/ucp` on its own domain when possible. For a hosted-domain profile, document the merchant-identity and domain-authorization relationship before claiming UCP discovery compatibility.

## Business UCP profile

The server generates this structure from the printer tenant. `UCP_RELEASE` must be the exact selected UCP snapshot; the AP2 capability is `dev.ucp.common.payment.ap2_mandate`.

```json
{
  "ucp": {
    "version": "UCP_RELEASE",
    "services": {
      "dev.ucp.shopping": [{
        "version": "UCP_RELEASE",
        "spec": "https://ucp.dev/UCP_RELEASE/specification/overview/",
        "transport": "rest",
        "schema": "https://ucp.dev/UCP_RELEASE/services/shopping/rest.openapi.json",
        "endpoint": "https://api.agentorder.tech/ucp/v1/printers/{printer_id}"
      }]
    },
    "capabilities": {
      "dev.ucp.shopping.checkout": [{"version": "UCP_RELEASE", "spec": "…", "schema": "…"}],
      "dev.ucp.common.payment.ap2_mandate": [{"version": "UCP_RELEASE", "spec": "…", "schema": "…", "extends": "dev.ucp.shopping.checkout"}],
      "org.agentorder.shopping.print_quote": [{
        "version": "2026-09-06",
        "spec": "https://agentorder.org/specification/0.2",
        "schema": "https://agentorder.org/schemas/0.2/print-quote.json",
        "extends": ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout"],
        "config": {
          "business_cards": {
            "finished_size_presets": ["us_3.5x2in"],
            "sides": [1, 2],
            "stock_gsm": [300, 350, 400],
            "stock_finish": ["uncoated", "silk", "matte", "gloss"],
            "quantity": {"minimum": 100, "maximum": 10000, "increment": 50},
            "finishing": ["none", "matte_laminate_both_sides"]
          }
        }
      }]
    },
    "payment_handlers": {}
  },
  "keys": [{"kid": "printer-key-id", "kty": "EC", "crv": "P-256", "x": "…", "y": "…", "alg": "ES256"}]
}
```

This follows the published UCP profile structure: service endpoint, versioned capabilities, schema URLs under the owning namespace, and discovery JWKs. Replace every ellipsis with the selected UCP release's canonical URLs during implementation. [UCP profile example](https://github.com/Universal-Commerce-Protocol/ucp/blob/main/docs/documentation/core-concepts.md)

## Server and database changes

### Keep

- Python 3.11, `http.server` only for the local reference server, SQLite, parameterized queries, request-size limits, JSON rejection rules, unique order-per-quote protection, and idempotency persistence.
- Loopback-only local development binding and synthetic payments in test mode.

### Rewrite

| Component | v0.2 change |
|---|---|
| `server.py` | Replace `/.well-known/agentorder` and v0.1 routes with per-printer `/.well-known/ucp`, a UCP REST binding, AgentOrder RFQ/quote operations, checkout creation, mandate verification, and Stripe Connect adapter interfaces. Retain no v0.1 compatibility endpoints. |
| SQLite schema in `server.py` | Replace shared bearer-key demo records with `printers`, `printer_capabilities`, `signing_keys`, `rfqs`, `quotes`, `checkouts`, `mandate_audit`, `stripe_payments`, `orders`, and tenant-scoped idempotency keys. Persist named size presets and GSM stock values, never local unit conversions. Encrypt secrets outside SQLite where production deployment supplies a key manager. |
| `schema.json` | Rewrite as the **single canonical AgentOrder JSON Schema** for v0.2: RFQ, quote, print-job, decline, status, error, and capability config. `SPECIFICATION.md` links to it; no second executable schema file is introduced. UCP and AP2 schemas are external canonical dependencies, referenced rather than copied. |
| `test_protocol.py` | Rewrite around v0.2 negotiation, printer config enforcement, regional-preset/GSM-stock examples, quote decline, buyer/destination forwarding, one-review/mandate flow, UCP checkout construction, signature/replay failures, tenant isolation, and idempotent charge initiation. |
| `example-rfq.json` | Rewrite as a region-neutral business-card RFQ using a named preset and GSM stock. |
| `examples.json` | Rewrite as UCP business profile, platform profile, RFQ, quote with required UCP line item, declined quote, checkout, AP2 fixtures, error, and order examples. Fixtures contain no live URLs, keys, account IDs, or customer data. |
| `README.md` | Rewrite setup, architecture, security boundaries, and local reference-agent instructions. Remove NZD/demo-order claims. |
| `JOURNEY.md` | Rewrite as the one-review AP2 mandate journey and hosted-printer operator checklist. |

### Delete and replace

| v0.1 file | Action | Replacement |
|---|---|---|
| `catalog.json` | Delete. Its fixed NZD, gsm, and pricing model conflicts with tenant UCP capability configuration. | Per-printer capability config stored in SQLite plus safe fixtures in `examples.json`. |
| `demo.py` | Delete. Its secret-link approval and direct v0.1 order submission model conflicts with UCP/AP2. | `reference_agent.py`, `mcp_server.py`, and `agent-card.json` fixtures. |

`SPECIFICATION.md` is revised, not replaced; its embedded schemas are documentation views of canonical `schema.json` and will be kept byte-for-byte aligned by tests or generated from it. No v0.1 schema remains in the repository after the migration.

## Reference agent

- `reference_agent.py` fetches the printer profile, sends `UCP-Agent` pointing to its platform profile, computes the negotiated capability intersection, reads `config`, and refuses RFQs outside it.
- `mcp_server.py` exposes read-only discovery and quote-request tools, then a checkout-review handoff. It cannot synthesize AP2 mandates or charge without a trusted-surface result.
- `agent-card.json` describes the A2A handoff for quote discovery and review. The card contains no private routing, keys, or printer credentials.
- The reference agent creates the final UCP Checkout before opening the trusted review. The trusted surface returns the signed closed AP2 Checkout and Payment Mandates plus payment credential. There is no separate AgentOrder approval record.

## Manual quote UI — critical path

Build this before the sandbox Stripe adapter. It proves the side printers actually need, while the fake payment adapter proves the rest of the transaction loop.

1. **RFQ inbox:** tenant-scoped list with buyer and fulfillment destination, print preset, GSM stock/finish, quantity, artwork URL, expiry, and signature state.
2. **Quote form:** printer enters a fixed total through the UCP line item, lead time, and any allowed production note. The server validates values against that printer's capability `config`; it creates one immutable quote or a normal `declined` status with reason.
3. **Send:** service signs/publishes the quote to the reference agent. The agent can create the UCP Checkout only from the stored quote; the UI cannot alter it afterwards.
4. **Audit:** record operator, quote revision (one only in v0.2), timestamps, and the emitted quote hash. Do not expose buyer data across tenants.

Pilot proof: a real printer receives a realistic RFQ, clicks through price + lead time + send, and the reference agent receives an interoperable quote. This succeeds before any Stripe sandbox charge exists.

## Stripe Connect boundary

1. Server verifies the Checkout Mandate against the merchant-signed UCP Checkout and verifies its expiry, hash, audience, signature chain, and negotiated capabilities.
2. Server verifies the Payment Mandate binding and payment credential scope according to AP2.
3. Server starts a standard Stripe Connect PaymentIntent/charge for the printer's connected account using the verified final amount and idempotency key.
4. Store mandate hashes and Stripe identifiers, never raw payment credentials or full mandate contents beyond the minimum dispute/audit retention policy.
5. Webhook-confirmed payment success creates the UCP order. A failed payment returns a UCP/AP2-compatible failure and never enters production.

The Stripe API shape and payment method chosen for the pilot remain implementation details; the invariant is that AgentOrder verifies AP2 and Stripe performs a standard connected-account charge.

## Delivery sequence

1. Freeze the selected UCP snapshot and settle the AP2 capability identifier conflict.
2. Replace canonical schema and fixtures; add schema/profile validation tests.
3. Build SQLite tenant/configuration and UCP discovery.
4. Build the MCP server, platform profile, and A2A card; prove negotiated RFQ against one hosted-printer fixture.
5. Build the manual quote UI and prove a printer operator can send a quote from the RFQ inbox; then request Stripe Connect sandbox onboarding.
6. Build checkout and mandate-verification interfaces with deterministic AP2 test vectors.
7. Add a fake Stripe Connect adapter that proves the loop, then a sandbox Stripe adapter and webhook tests.
8. Run the local end-to-end fixture: reference agent → tenant profile → RFQ → quote → UCP checkout → fixture mandates → fake payment → UCP order.

## Definition of done for this step

The repo has one v0.2 AgentOrder schema, no v0.1 endpoint or fixed-NZD schema artifacts, a testable UCP profile, and a reference agent that proves capability negotiation. Live Stripe and live printers are not prerequisites for this implementation milestone.
