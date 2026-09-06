# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected security vulnerability or accidental exposure of private information. Use GitHub's private vulnerability reporting feature when it is enabled for this repository. Until then, contact the repository owner privately through the GitHub profile linked from the organisation.

Include a concise description, affected file or endpoint, reproduction steps that use only dummy data, likely impact, and a suggested remediation if you have one. Do not send real customer artwork, credentials, payment details, review links, or production data.

We will acknowledge reports as soon as practical and coordinate a fix before public disclosure when appropriate.

## Supported code

Only the current `main` branch is maintained. The reference endpoint is a local demonstration and is not suitable for production use.

## Security boundaries in the demo

- The server listens on `127.0.0.1` only.
- A review URL is a secret capability for a demonstration decision; it does not authenticate a customer.
- The implementation never handles payment, artwork uploads, or production fulfilment.
- The API key and SQLite database must remain private and are excluded by `.gitignore`.

Before a real deployment, use HTTPS, independent customer identity and authorization, least-privilege credentials, rate limits, monitoring, backups, and a documented retention policy.
