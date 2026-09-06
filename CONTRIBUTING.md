# Contributing to AgentOrder Print Protocol

Thank you for helping make print ordering work across independent printers and agents.

## Before opening an issue

Search existing issues first. Use GitHub Issues for a specific interoperability problem, a missing print requirement, or an implementation defect. Use GitHub Discussions for early ideas and questions once Discussions is enabled.

Do not include customer names, addresses, artwork, quote links, API keys, access tokens, credentials, or real order data in an issue, pull request, example, test, or log.

## Proposing a change

Keep proposals narrow and describe:

1. The real print-ordering situation that fails today.
2. The proposed message or state change.
3. How an independent printer and agent would implement it.
4. Whether the change is compatible with draft 0.1.0.

Changes to a message shape must update `SPECIFICATION.md`, `schema.json`, examples, and relevant tests together. Do not add vendor-only behaviour to the core protocol. Document it as an extension and keep it optional.

## Local checks

Use Python 3.11 or later:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v
```

The tests run only against temporary loopback services and databases. They do not place orders, take payments, upload artwork, or contact a real printer.

## Review expectations

Maintainers review changes for customer control, clear error handling, independent implementation, privacy, and migration impact. Draft 0.x versions can change, but a breaking change requires a new protocol version and release note before merge.

By submitting a contribution, you agree that it may be distributed under the repository's MIT License.
