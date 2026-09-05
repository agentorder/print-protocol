"""Small agent-style client. Customer approval is performed separately in a browser."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8787')
    sub = parser.add_subparsers(dest='operation', required=True)
    quote = sub.add_parser('quote')
    quote.add_argument('--request', default=str(Path(__file__).with_name('example-rfq.json')))
    quote.add_argument('--key', help='Reuse this key to retrieve the same quote after a retry.')
    order = sub.add_parser('order')
    order.add_argument('quote_id')
    args = parser.parse_args()
    base = args.base.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('This demo client only connects to http://127.0.0.1:PORT.')
    key = os.environ.get('AGENTORDER_API_KEY', '')
    if len(key) < 24:
        parser.error('Use the same AGENTORDER_API_KEY as the server.')
    opener = build_opener(NoRedirect())

    def request(url, body=None, retry_key=None, authenticated=True):
        target = urlsplit(url)
        if (target.scheme, target.netloc) != (parsed.scheme, parsed.netloc):
            raise ValueError('Refusing to forward credentials to another origin.')
        headers = {'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + key
        if retry_key:
            headers['Idempotency-Key'] = retry_key
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        with opener.open(Request(url, data=data, headers=headers), timeout=10) as response:
            return json.load(response)

    discovery = request(base + '/.well-known/agentorder', authenticated=False)
    if discovery['protocol_version'] != '0.1.0' or discovery['mode'] != 'demo':
        raise ValueError('This client requires AgentOrder demo protocol 0.1.0.')
    if args.operation == 'quote':
        retry_key = args.key or ('rfq-' + secrets.token_hex(12))
        print('Retry key: ' + retry_key, file=sys.stderr)
        body = json.loads(Path(args.request).read_text())
        result = request(discovery['rfq_url'], body, retry_key)
        print(json.dumps(result, indent=2))
        print('\nOpen the review URL and approve or decline the demo quote:', result['review_url'], file=sys.stderr)
        print('After approval run: python demo.py order ' + result['quote_id'], file=sys.stderr)
    else:
        # Quote IDs are opaque single path segments, never URLs.
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', args.quote_id):
            parser.error('Invalid quote ID.')
        current = request(base + '/v0.1/quotes/' + args.quote_id)
        if current['status'] not in ('approved', 'ordered'):
            raise ValueError('Quote is ' + current['status'] + '; an approved, unexpired quote is required.')
        result = request(discovery['orders_url'], {'protocol_version': '0.1.0', 'quote_id': args.quote_id},
                         'order-' + args.quote_id)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except HTTPError as error:
        print(f'HTTP {error.code}: ' + error.read().decode(), file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
