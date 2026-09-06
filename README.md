# AgentOrder Print Protocol

**An open protocol for agentic print ordering.**

Draft **0.1.0** connects a printer's capabilities to a structured request for quote, a customer review step, and an order acknowledgment. The first reference product is double-sided business cards.

**This is a working local demonstration, not a live print service.** All prices are synthetic. It does not take payments, inspect artwork, reserve production capacity, or send work to a printer.

## First milestone

An agent requests 500 double-sided business cards, receives a quote, gives the customer a review link, and submits one demo order after approval.

```text
Printer discovery → RFQ → fixed quote → customer review → approval → order
                                                                  ↓
                                                   pending artwork review
```

The example returns **NZD 95.00 + NZD 14.25 illustrative tax = NZD 109.25** for 500 cards without lamination. Production is estimated at three business days after the printer accepts the artwork. Pickup only; no delivery fee is implied or omitted.

## Run locally

Requires Python 3.11 or later. From this repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export AGENTORDER_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python server.py
```

The service binds only to `127.0.0.1:8787`. Keep the key private. To run the client in the same terminal, stop the server, restart it using `python server.py &`, then run:

```sh
python demo.py quote
```

Open the printed **review URL** in your browser. Check the quantity, options, sample price and turnaround; click **Approve demo quote** or **Decline**. Then use the quote ID printed by the client:

```sh
python demo.py order QUOTE_ID
```

The client will refuse to submit an unapproved quote. The server enforces approval independently. The order acknowledgment says `pending_artwork_review`; it does not claim printing has begun. Retrying order submission returns the same order.

SQLite stores quotes, decisions, orders and retry keys in `agentorder-demo.sqlite3`. Restarting the service on the same port with the same database and API key preserves the journey. Keep the database private; do not commit it. This single-printer demo shares one API credential across its clients.

## Repository map

| File | Purpose |
|---|---|
| [SPECIFICATION.md](SPECIFICATION.md) | Draft wire contract, state transitions and errors |
| [JOURNEY.md](JOURNEY.md) | Customer/agent/printer responsibilities and pilot checklist |
| [schema.json](schema.json) | JSON Schema definitions for discovery, RFQ, quote, order and errors |
| [example-rfq.json](example-rfq.json) | A complete 500-card request |
| [examples.json](examples.json) | Matching illustrative discovery, quote and order messages |
| [catalog.json](catalog.json) | Explicit demonstration options and pricing |
| [server.py](server.py) | HTTP reference endpoint, customer review and persistent state |
| [demo.py](demo.py) | Agent-style client for the complete journey |
| [test_protocol.py](test_protocol.py) | Contract, HTTP and ordering integrity tests |
| [GOVERNANCE.md](GOVERNANCE.md) | Early-stage stewardship and public contribution process |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution and local-validation guidance |
| [SECURITY.md](SECURITY.md) | Private-reporting guidance and demo security boundaries |

## Verify

```sh
python -m unittest -v
```

Tests exercise the real HTTP server on a temporary loopback port, including approval, expiry, invalid options, retries, persistence and concurrent order submission. They create no live orders and use temporary databases.

## Discovery

A printer exposes `GET /.well-known/agentorder`, returning its own catalog and RFQ endpoint. For this draft, a website may also point to that declaration using an experimental link:

```html
<link rel="https://agentorder.org/relations/ordering"
      href="https://printer.example/.well-known/agentorder">
```

These are proposed conventions, not claims of IANA registration. A link to a generic specification alone cannot tell an agent where to request a quote. The declaration supplies the printer-specific endpoints. No dependency on AgentOrder's hosted service is required.

## Pilot boundaries

- **Implemented:** discovery, schema validation, catalog checks, immutable quote prices, expiry, review/decline, order creation, persistence and idempotent retries.
- **Demonstration assumptions:** 90 × 55 mm, 350 gsm silk, full colour on both sides; 250/500/1,000 cards; optional matte lamination; NZD; illustrative tax; pickup.
- **Before a real customer pilot:** replace and verify pricing, establish customer and agent identities with distinct permissions, handle artwork upload/preflight and proofs, obtain explicit commercial order authorization, add production-grade hosting, rate limits and operational monitoring.

The review link is a secret capability. Anyone with it, including software, can submit a demo decision. CSRF protection prevents ordinary cross-site form submission; it does **not** establish a human identity or stop an authorized agent impersonating a customer. Production must bind approval to an independently authenticated customer and the exact quote.

## Project and product

`AgentOrder.org` is the intended open-project home. `AgentOrder.tech` is the intended hosted-product home. This repository does not deploy either domain. The project currently has founder stewardship; independent governance has not yet been established. See [GOVERNANCE.md](GOVERNANCE.md).

The draft specification, examples and implementation are available under the [MIT License](LICENSE). This license does not grant rights to the AgentOrder name or marks.

## Participate

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. Use issues for reproducible defects and concrete interoperability needs. Keep customer data, artwork, quote-review links, credentials and real order details out of public channels. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.
