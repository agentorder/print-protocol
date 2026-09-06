# AgentOrder Print Protocol — v0.2.0

**Status:** experimental. **Scope:** business cards only; one quote per RFQ; artwork by HTTPS URL; Stripe is the only supported payment rail. AgentOrder is a UCP extension and hosted merchant adapter. It is not a commerce or payment standard.

## 1. Compatibility contract

AgentOrder adds a quote loop before a UCP Checkout. It reuses UCP's merchant, cart, line-item, buyer, fulfillment, currency, totals, checkout, order and payment-handler objects without redefining their shape. It reuses AP2 Checkout and Payment Mandates without redefining them.

| Standard | AgentOrder use |
|---|---|
| UCP | Discovery at `/.well-known/ucp`; catalog/cart/checkout/order; capability negotiation; merchant identity and payment handler. |
| UCP AP2 extension | `dev.ucp.common.payment.ap2_mandate`, extending checkout. The UCP checkout's `ap2` field carries merchant authorization and the checkout mandate. |
| AP2 v0.2 | A merchant-signed UCP Checkout JWT, closed Checkout Mandate (`vct: mandate.checkout.1`), and closed Payment Mandate (`vct: mandate.payment.1`). |
| MCP / A2A | Transport bindings only. They do not replace UCP discovery or AP2 authorization. |

The normative UCP object schemas remain the current [UCP schema reference](https://ucp.dev/specification/reference/). The normative AP2 mandate schemas remain [Checkout Mandate](https://ap2-protocol.org/ap2/checkout_mandate/) and [Payment Mandate](https://ap2-protocol.org/ap2/payment_mandate/).

## 2. Discovery and bootstrap

A printer, or AgentOrder acting as its authorized hosted endpoint, publishes a UCP Business Discovery Profile at:

```text
https://{printer-domain}/.well-known/ucp
```

The profile declares the standard UCP shopping service, `dev.ucp.shopping.checkout`, `dev.ucp.common.payment.ap2_mandate`, and `org.agentorder.shopping.print_quote` version `2026-09-06`. The AgentOrder capability extends UCP shopping cart and checkout. Its schema and specification are published from `https://agentorder.org/`; its version date is independent of AgentOrder's semver message version.

The hosted endpoint may serve the profile and transports for a printer that lacks an API. The printer remains merchant of record and the Stripe connected account remains the payee. The hosted service must be authorized to sign the merchant checkout response and publish the relevant public JWK in the profile.

Each printer declares accepted business-card values in the capability's UCP `config`; the reference client must read this before submitting an RFQ. Example capability declaration:

```json
{
  "org.agentorder.shopping.print_quote": [{
    "version": "2026-09-06",
    "extends": ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout"],
    "spec": "https://agentorder.org/specification/0.2",
    "schema": "https://agentorder.org/schemas/0.2/print-quote.json",
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
}
```

`config` is the printer's authoritative accepted-value declaration. The wire schema permits only the three named regional presets; custom sizes are out of scope for v0.2.

### First capable agent

The initial platform that declares the AgentOrder capability is the AgentOrder reference client: an MCP server with an A2A agent card. Its UCP platform profile advertises the same capability/version as the printer profile. That gives the pilot one interoperable agent without waiting for Gemini or another platform to implement the extension.

UCP negotiation activates the capability only when both profiles declare the same version. A generic UCP agent that does not advertise it cannot initiate an RFQ; it may still use other UCP capabilities. This is an adoption constraint, not a protocol fallback. Upstream standardization of a quoted-product capability remains the route to generic UCP-agent support.

## 3. Flow

```text
UCP platform profile                 Hosted printer endpoint / printer
        | UCP capability negotiation             |
        |--------------------------------------->|
        | <---- active UCP + AgentOrder set -----|
        |                                        |
        | AgentOrder RFQ (signed; idem key)      |
        |--------------------------------------->|
        | <-------- one immutable quote ----------|
        |                                        |
        | UCP Checkout using quote line item      |
        |--------------------------------------->|
        | < merchant-signed Checkout -------------|
        |                                        |
        | human reviews exact Checkout            |
        |--- trusted surface signs AP2 ---------->|
        |                                        |
        | AP2 mandates + payment credential       |
        |--------------------------------------->|
        | <---------- UCP order / receipts -------|
```

The quote does not charge, create a UCP order, or authorize payment. A fresh quote is required after expiry or any changed print requirement. The completed UCP Checkout is the sole payable representation of the accepted quote.

## 4. AgentOrder-only messages

Every AgentOrder message has `agentorder_version: "0.2.0"`. UCP and AP2 messages retain their own versioning and are passed in their canonical forms.

### 4.1 RFQ

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agentorder.org/schemas/0.2/rfq.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["agentorder_version", "rfq_id", "print_job", "buyer", "fulfillment"],
  "properties": {
    "agentorder_version": {"const": "0.2.0"},
    "rfq_id": {"type": "string", "minLength": 1},
    "buyer": {"$ref": "https://ucp.dev/schemas/shopping/types/buyer.json"},
    "fulfillment": {"$ref": "https://ucp.dev/schemas/shopping/types/fulfillment_destination.json"},
    "print_job": {"$ref": "#/$defs/business_card"}
  },
  "$defs": {
    "business_card": {
      "type": "object",
      "additionalProperties": false,
      "required": ["quantity", "finished_size", "sides", "colour", "stock", "artwork"],
      "properties": {
        "quantity": {"type": "integer", "minimum": 1, "maximum": 100000},
        "finished_size": {"enum": ["us_3.5x2in", "uk_eu_85x55mm", "au_nz_90x55mm"]},
        "sides": {"enum": [1, 2]},
        "colour": {"enum": ["CMYK", "black"]},
        "stock": {"type": "object", "additionalProperties": false, "required": ["weight_gsm"], "properties": {"weight_gsm": {"type": "integer", "minimum": 150, "maximum": 600}, "finish": {"enum": ["uncoated", "silk", "matte", "gloss"]}}},
        "finishing": {"enum": ["none", "matte_laminate_both_sides"]},
        "artwork": {
          "type": "object",
          "additionalProperties": false,
          "required": ["url"],
          "properties": {
            "url": {"type": "string", "format": "uri", "pattern": "^https://"},
            "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}
          }
        }
      }
    }
  }
}
```

`artwork.url` is a reference only. The server must not fetch arbitrary URLs during RFQ creation. A later authenticated artwork handoff obtains the file through a bounded, allow-listed transfer process.

### 4.2 Quote

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agentorder.org/schemas/0.2/quote.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["agentorder_version", "quote_id", "rfq_id", "status", "print_job"],
  "properties": {
    "agentorder_version": {"const": "0.2.0"},
    "quote_id": {"type": "string", "minLength": 1},
    "rfq_id": {"type": "string", "minLength": 1},
    "status": {"enum": ["quoted", "declined"]},
    "expires_at": {"type": "string", "format": "date-time"},
    "print_job": {"$ref": "rfq.json#/$defs/business_card"},
    "lead_time": {"type": "object", "additionalProperties": false, "required": ["business_days", "starts_after"], "properties": {"business_days": {"type": "integer", "minimum": 1}, "starts_after": {"const": "artwork_accepted"}}},
    "review": {"type": "object", "additionalProperties": false, "required": ["url"], "properties": {"url": {"type": "string", "format": "uri", "pattern": "^https://"}}},
    "quote_line_item": {"type": "object", "$ref": "https://ucp.dev/schemas/shopping/types/line_item.json"},
    "decline": {"type": "object", "additionalProperties": false, "required": ["reason"], "properties": {"reason": {"enum": ["price", "lead_time", "specification", "artwork", "other"]}}},
    "declined_at": {"type": "string", "format": "date-time"}
  },
  "allOf": [
    {"if": {"properties": {"status": {"const": "quoted"}}}, "then": {"required": ["expires_at", "lead_time", "review", "quote_line_item"]}},
    {"if": {"properties": {"status": {"const": "declined"}}}, "then": {"required": ["decline", "declined_at"]}}
  ]
}
```

A `quoted` response has a required fixed-price UCP line item, referenced to the frozen vendored UCP line-item schema at `ucp.lock.json`. Currency, price, tax, buyer, fulfillment destination, payment method, checkout totals and merchant identity exist only in their UCP objects. A `declined` response has no payable line item, lead time, review, or expiry; it records only the reason and time.

### 4.3 Proof decision and production status

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agentorder.org/schemas/0.2/status.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["agentorder_version", "quote_id", "proof_status", "production_status"],
  "properties": {
    "agentorder_version": {"const": "0.2.0"},
    "quote_id": {"type": "string"},
    "proof_status": {"enum": ["not_required", "pending", "approved", "changes_requested"]},
    "production_status": {"enum": ["awaiting_artwork", "artwork_review", "in_production", "dispatched", "completed", "on_hold"]}
  }
}
```

A quote review is the single purchase approval: the trusted surface renders the final UCP Checkout and the human signs the AP2 Checkout and Payment Mandates there. AgentOrder creates no separate approval record. A proof decision is a production-control decision only; it cannot authorize payment.

## 5. UCP and AP2 boundary

For an unexpired quote, the hosted endpoint creates a UCP Checkout whose line item represents that quote before requesting human review. The checkout must include the quote's final amount, merchant/payee, policies, fulfillment selection, and expiry using UCP fields. It must be merchant-signed according to the negotiated UCP AP2 extension.

The shopping agent presents that exact Checkout to a trusted surface. That review is the sole human purchase approval. A charge requires:

1. a closed AP2 Checkout Mandate with `vct: "mandate.checkout.1"`, containing the merchant-signed Checkout JWT and its hash;
2. a closed AP2 Payment Mandate with `vct: "mandate.payment.1"`, bound to that checkout hash; and
3. a payment credential scoped to the approved payment mandate.

The hosted endpoint verifies the Checkout Mandate itself, then creates a standard Stripe Connect charge for the printer's connected account. Stripe is not asked to verify AP2. The endpoint records the mandate hashes, UCP Checkout Receipt, Stripe payment identifier, and payment receipt against the resulting UCP order. No AgentOrder endpoint accepts a caller-supplied approval flag, payment amount, merchant, or payment token as a substitute.

## 6. Transport and security

| Operation | Requirement |
|---|---|
| RFQ submission | Signed request, negotiated UCP capability, and `Idempotency-Key`. |
| Quote response | Immutable quote, expiry, UCP active-capability metadata, and signed response where the negotiated UCP binding requires it. |
| Review | Separately authenticated human session; CSRF token; exact quote, total, expiry, artwork/proof state shown before decision. Review URL alone is insufficient. |
| Checkout completion | UCP/AP2 mandate verification, idempotency key, and Stripe credential only after valid mandate. |
| Artwork | HTTPS URL only in v0.2. No public fetch, direct upload, or arbitrary redirect following. |
| Hosted tenancy | Per-printer keys, Stripe account binding, idempotency scope, audit records, and authorization boundary. |

Use UCP profile JWKs and HTTP message signatures for service-to-service requests. Reject stale timestamps, invalid signatures, replayed request identifiers, cross-tenant identifiers, unknown fields, and changed bodies for an existing idempotency key. Do not place review tokens in logs, referrers, analytics, or third-party assets.

### Error response

Every AgentOrder endpoint returns this envelope for an error. `agentorder_version` is mandatory; UCP and AP2 errors remain in their respective envelopes.

```json
{
  "agentorder_version": "0.2.0",
  "error": {
    "code": "quote_expired",
    "message": "This quote has expired; request a fresh quote.",
    "details": []
  }
}
```

`code` is one of `invalid_request`, `unsupported_print_job`, `quote_expired`, `idempotency_conflict`, `invalid_signature`, `invalid_csrf`, or `mandate_required`. A buyer decline is a normal quote status, never an error. `details` is an array and may be empty. HTTP status is 400, 401, 403, 409, 410, or 422 as appropriate.

## 7. Explicit AgentOrder-only surface

AgentOrder defines only:

- business-card print specification;
- RFQ identity and quote identity;
- quote validity;
- lead time beginning after artwork acceptance;
- artwork URL and checksum reference;
- proof decision;
- production status.

It does **not** define merchant identity, buyer, cart, line item, money, currency, tax, delivery address, fulfillment selection, checkout, order, payment method, payment credential, mandate, or receipt.

## 8. Deferred from v0.2

Multi-item RFQs, multiple quotes, shipping pricing, customer-uploaded artwork, printer-side API integration, non-Stripe rails, autonomous/open mandates, cancellations/refunds, and ACP-specific endpoints are out of scope. ACP may be added as a transport adapter only if it can map to the same UCP Checkout and AP2 boundary without new AgentOrder fields.

## References

- [UCP core concepts and discovery](https://github.com/Universal-Commerce-Protocol/ucp/blob/main/docs/documentation/core-concepts.md)
- [UCP schema reference](https://ucp.dev/specification/reference/)
- [AP2 v0.2 specification](https://ap2-protocol.org/ap2/specification/)
- [AP2 Checkout Mandate](https://ap2-protocol.org/ap2/checkout_mandate/)
- [AP2 Payment Mandate](https://ap2-protocol.org/ap2/payment_mandate/)
