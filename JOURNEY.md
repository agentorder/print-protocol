# First journey: 500 business cards

## What success looks like

A customer asks an agent for 500 double-sided business cards. The agent finds one printer's supported configuration, obtains a precise quote, gives the customer a private review link, waits for their decision, and submits one demo order after approval. The customer sees that artwork acceptance is still outstanding.

## Steps and responsibilities

| Step | Actor | Action | Evidence |
|---|---|---|---|
| 1 | Customer | Requests 500 cards and supplies intended print requirements | Requirements match `example-rfq.json` |
| 2 | Agent | Reads printer discovery and catalog; confirms version and origin | Declaration contains printer-specific URLs |
| 3 | Agent | Sends the RFQ using a stable retry key | Quote includes all options, itemized amount, expiry and review URL |
| 4 | Printer service | Validates capabilities and fixes the quote | NZD 109.25 sample total; three days after artwork acceptance |
| 5 | Agent | Gives the customer the review URL and explains it is a demo | Customer can see exactly what they would approve |
| 6 | Customer | Approves or declines in the review form | Server records a decision; no charge or order yet |
| 7 | Agent | Fetches current quote state | Approved and unexpired quote permits submission |
| 8 | Agent | Submits the quote ID using a stable order retry key | One persistent order ID |
| 9 | Printer/customer | Would supply, inspect and approve artwork next | Demo ends at `pending_artwork_review` |

## Exceptions to demonstrate

- Unsupported stock, card size or delivery: return the unsupported fields and ask for a supported request. Do not guess a substitute or invent a shipping price.
- Customer declines: no order. Revised requirements require a new RFQ and fresh approval.
- Quote expires: request a new quote. Approval of the old quote does not transfer.
- Agent retries after a network interruption: return the original quote/order. Do not add another order.
- Artwork metadata looks correct: still await actual file inspection; metadata is not preflight.
- Price configuration changes: existing quotes retain their original amounts; only new RFQs use the new configuration after service restart.

## Minimum real-printer inputs

1. Actual finished size, stock brand/finish and weight, quantities, colour and finishing combinations.
2. Prices, whether prices include tax, applicable tax treatment and minimum charges.
3. Pickup location or delivery zones, shipping prices and excluded destinations.
4. Artwork requirements, upload route, preflight responsibility and proof-approval process.
5. Lead time, order cutoff, holidays, capacity and who accepts a job into production.
6. Customer approval/payment terms and who may act for the customer.

## Pilot acceptance checklist

- [ ] A printer has verified every supported option and price.
- [ ] Unsupported requests are routed explicitly, with no silent substitutions.
- [ ] Customers authenticate separately from agents before approving real orders.
- [ ] Production hosting, HTTPS, rate limits, monitoring, backups and retention are configured.
- [ ] Artwork can be received, inspected, corrected and approved.
- [ ] The printer controls final production acceptance.
- [ ] Cancellation, expiry, tax, payment and delivery terms are agreed.
- [ ] One real job is manually reconciled end to end before broader rollout.

These are future pilot gates. The local prototype demonstrates the ordering conversation without making commercial commitments.
