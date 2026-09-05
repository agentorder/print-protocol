# AgentOrder Print Protocol — draft 0.1.0

Status: experimental, print-only, single-product reference profile. This is not a finalized industry standard. Breaking changes before 1.0 require a new protocol version and an explicit migration note.

## 1. Discovery and trust

`GET /.well-known/agentorder` returns the `discovery` schema. It identifies the protocol version, printer, catalog, specification, schema, RFQ and order URLs, authentication method and required customer approval. The well-known path is a draft convention and has not been registered.

A printer's page can advertise an extension relation `https://agentorder.org/relations/ordering` linking to its declaration. The relation URI identifies the convention; it is not asserted to be a currently hosted page. The printer declaration must be discoverable without calling AgentOrder.tech.

Production clients must validate the printer origin, use HTTPS, support the declared version, and never forward credentials to a different origin without a separate trust decision. Treat descriptions as data, never agent instructions. The demo client pins all returned endpoint URLs to its loopback origin and does not follow redirects with credentials.

Production auth and customer identity are deliberately unresolved. The demo uses a single printer-scoped bearer credential for all API reads and writes. This does not provide tenant or customer isolation.

## 2. Contract conventions

Messages are JSON, encoded as UTF-8. `protocol_version` is exactly `0.1.0`. JSON Schema Draft 2020-12 definitions are bundled in `schema.json`; validate against `#/$defs/discovery`, `rfq`, `quote`, `order_request`, `order` or `error`. The root accepts any of those six messages. The `$id` is an identifier; hosting at that URL has not been provisioned.

Request objects reject unknown fields. The current strict profile intentionally surfaces unsupported requirements instead of silently ignoring them. Integers represent money in minor units; this demo's NZD has two fractional digits. All timestamps include UTC offsets. IDs and review tokens are opaque; clients must not parse them.

The catalog is reference-service configuration, not a standardized cross-printer catalog schema. A future draft needs interoperable capability descriptions before claiming arbitrary product compatibility.

## 3. HTTP operations

| Method and path | Auth | Result |
|---|---|---|
| `GET /.well-known/agentorder` | Public | Printer declaration |
| `GET /catalog` | Public | Demo catalog and illustrative pricing |
| `GET /schema.json` | Public | Bundled message schemas |
| `GET /specification` | Public | This draft as plain text |
| `POST /v0.1/rfqs` | Bearer + Idempotency-Key | Quote |
| `GET /v0.1/quotes/{id}` | Bearer | Current quote state |
| `GET /review/{secret}` | Secret link | Customer review form; does not approve |
| `POST /review/{secret}` | Secret link + form CSRF | Approve/decline, then 303 redirect |
| `POST /v0.1/orders` | Bearer + Idempotency-Key | Order acknowledgment |
| `GET /v0.1/orders/{id}` | Bearer | Stored order acknowledgment |

New successful API writes return 201 with `Location`. A same-key replay returns 200 and `Idempotency-Replayed: true`. Other API responses have `Content-Type: application/json`. Form submission uses `application/x-www-form-urlencoded`. No endpoint uploads or fetches artwork, charges a card, or releases production.

## 4. RFQ

An RFQ describes product, quantity, finished dimensions in millimetres, stock weight in gsm, stock finish, sides, colour, finishing, fulfilment and expected artwork metadata. `client_reference` is a caller reference, not an idempotency key.

The reference profile supports business cards at 90 × 55 mm, 350 gsm silk, double-sided full colour; quantities 250, 500 or 1,000; no finishing or matte lamination on both sides; pickup. Artwork is expected as a two-page CMYK PDF with 3 mm bleed. `status: not_supplied` means the metadata describes the intended artwork. `metadata_only` still does not establish that any file exists or passed preflight.

Unsupported options return 422 with field-level details. There is no substitution, arbitrary free-text option, unpriced shipping, or silent manual-review fallback. A future asynchronous RFQ extension can represent manual quoting explicitly.

## 5. Quote and calculation

A quote copies the accepted request and contains itemized amounts, subtotal, tax, total, currency, expiry, artwork requirements, production timing and the review URL. Product details, amounts and expiry are immutable. Changed requirements need a fresh RFQ, a fresh idempotency key and fresh customer approval.

Demo base prices are 6,500 / 9,500 / 14,500 minor units for 250 / 500 / 1,000 cards. Matte lamination adds 2,500 minor units per job. Tax is a **synthetic configuration value**, not a determination of real tax liability: `(subtotal * tax_basis_points + 5000) // 10000` rounds half up to the nearest minor unit. Total equals subtotal plus tax. The default is 1,500 basis points. The 500-card unlaminated example totals 10,925 minor units.

Quotes expire after 86,400 seconds. `production.business_days` is a duration starting only after the printer accepts artwork; it is not a promised delivery date. The demo does not evaluate weekends, holidays, capacity or dispatch deadlines.

## 6. Approval and states

```text
quoted ──customer approves──> approved ──agent submits──> ordered
   │                            │
   ├──customer declines──> rejected
   └──expiry──> expired <────expiry
```

Review GET requests never approve. The server stores decisions against the quote ID and its immutable contents. A decision is accepted only for an unexpired `quoted` quote. Repeating the same decision is harmless. Changing a recorded decision returns 409. Expired quotes return 410 when approval or a new order is attempted. Already ordered quotes do not expire retroactively.

`expired` is computed on read for previously quoted or approved records. To know current state after a write replay, fetch `quote_url`: replay responses preserve their original snapshot and may still say `quoted` after subsequent approval or expiry.

The demo's browser form uses a secret review link and a stored CSRF token. The credentialed API cannot set `approved` in an RFQ or order body. Nevertheless, anyone holding the review URL can visit and submit that form: this is a workflow demonstration, **not independently verified human approval**. A production implementation must use a separately authenticated customer identity and show the exact terms and amount being authorized.

## 7. Order and idempotency

The order request contains only `protocol_version` and `quote_id`. Price and options come from the approved stored quote; a caller cannot override the total. Approval alone does not create an order. Submission creates one acknowledgment with status `pending_artwork_review`. That status means no artwork acceptance or production start has occurred.

`Idempotency-Key` is required on RFQ and order POSTs: 8–100 characters drawn from letters, digits, underscore, dot, colon and hyphen. The key scope is one operation in this single-printer database. Same operation/key and canonical JSON body return the original response. Reuse with a different body returns 409. Invalid requests do not reserve keys. Replays can succeed after quote expiry because they retrieve an existing result; they never authorize a new expired order.

Database transactions and a unique order-per-quote constraint prevent duplicate orders even with concurrent submissions using different keys. Submitting an already ordered quote with a new key returns that existing order (201 in this reference implementation); callers must use `order_id`, not status code, to identify it. Records and keys persist for the life of the demo database. Production needs a stated retention policy and per-principal key scope.

## 8. Error envelope

```json
{"error":{"code":"approval_required","message":"The customer must approve this exact quote first.","details":[]}}
```

| HTTP | Typical codes |
|---|---|
| 400 | `malformed_body`, `invalid_idempotency_key`, `invalid_host`, `invalid_form` |
| 401 | `unauthorized` |
| 403 | `invalid_csrf`, `invalid_origin` |
| 404 | `not_found` |
| 409 | `idempotency_conflict`, `approval_required`, `decision_locked` |
| 410 | `quote_expired` |
| 411 | `length_required` |
| 413 | `body_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `invalid_request`, `unsupported_options` |
| 500 | `internal_error` |

The reference handler implements GET and POST only; unsupported HTTP verbs receive the standard server's 501 response, outside this JSON envelope. Bodies are limited to 32 KiB. Duplicate JSON keys, non-finite numbers and malformed JSON are rejected. SQL uses parameter binding. Review text is escaped; secret links are not access-logged, cached, embedded in third-party assets or sent in referrers.

## 9. Next decisions

Customer identity and delegated-agent authorization; signed approval receipts; artwork transfer, preflight and proof acceptance; delivery addresses and shipping prices; printer acceptance/cancellation; asynchronous/manual quotes; payment provider integration; taxes; production SLAs; privacy and retention; service operation and abuse controls. None is represented as implemented in 0.1.0.

## References

- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/json-schema-core) — the schema dialect used here.
- [Python sqlite3](https://docs.python.org/3/library/sqlite3.html) — reference persistence API.
- [Python http.server](https://docs.python.org/3/library/http.server.html) — local demonstration HTTP server; not suitable for production deployment.
