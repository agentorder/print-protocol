"""AgentOrder Print Protocol: a loopback-only demonstration implementation."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / 'schema.json').read_text())
VERSION = '0.1.0'
NOTICE = 'Demonstration only. Sample prices; no payment, printing or fulfilment takes place.'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class Problem(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.body = {'error': {'code': code, 'message': message, 'details': details or []}}


def validate(kind, body):
    schema = {'$schema': SCHEMA['$schema'], '$defs': SCHEMA['$defs'], '$ref': '#/$defs/' + kind}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    if errors:
        raise Problem(422, 'invalid_request', 'Request does not match the draft schema.',
                      [('/' + '/'.join(map(str, e.absolute_path)) + ': ' + e.message) for e in errors[:10]])


class Store:
    def __init__(self, path, base, catalog_path=None, clock=time.time):
        self.path, self.base, self.clock = str(path), base.rstrip('/'), clock
        self.catalog = json.loads(Path(catalog_path or ROOT / 'catalog.json').read_text())
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS quotes (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL, review_token TEXT UNIQUE NOT NULL,
                    csrf TEXT NOT NULL, approved_at TEXT);
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, quote_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency (
                    route TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(route, key));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def discovery(self):
        return {'protocol': 'AgentOrder Print Protocol', 'protocol_version': VERSION,
                'mode': 'demo', 'printer_name': self.catalog['printer_name'],
                'specification_url': self.base + '/specification',
                'schema_url': self.base + '/schema.json', 'catalog_url': self.base + '/catalog',
                'rfq_url': self.base + '/v0.1/rfqs', 'orders_url': self.base + '/v0.1/orders',
                'authentication': {'type': 'bearer', 'scope': 'single_printer_demo'},
                'human_approval_required': True, 'notice': NOTICE}

    def checked_options(self, request):
        c = self.catalog
        fields = ('product', 'size_mm', 'stock_gsm', 'stock_finish', 'sides', 'colour')
        unsupported = [f'{k}: supported value is {c[k]}' for k in fields if request[k] != c[k]]
        if str(request['quantity']) not in c['base_prices_minor']:
            unsupported.append('quantity: choose ' + ', '.join(c['base_prices_minor']))
        if request['finishing'] not in c['finishing_surcharges_minor']:
            unsupported.append('finishing: choose ' + ', '.join(c['finishing_surcharges_minor']))
        if request['fulfilment']['method'] != c['fulfilment']:
            unsupported.append('fulfilment: pickup only; delivery is not priced')
        for key, value in c['artwork'].items():
            if request['artwork'][key] != value:
                unsupported.append(f'artwork.{key}: supported value is {value}')
        if unsupported:
            raise Problem(422, 'unsupported_options', 'These options need a different quote.', unsupported)

    def quote_body(self, row):
        body = json.loads(row['body'])
        body['status'] = row['status']
        if row['status'] in ('quoted', 'approved') and self.clock() >= row['expires']:
            body['status'] = 'expired'
        return body

    def get_quote(self, quote_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM quotes WHERE id = ?', (quote_id,)).fetchone()
            if row is None:
                raise Problem(404, 'not_found', 'Quote not found.')
            return self.quote_body(row)

    def get_order(self, order_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM orders WHERE id = ?', (order_id,)).fetchone()
            if row is None:
                raise Problem(404, 'not_found', 'Order not found.')
            return json.loads(row['body'])

    def mutate(self, route, key, body):
        if not key or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}', key):
            raise Problem(400, 'invalid_idempotency_key', 'Supply an Idempotency-Key of 8–100 safe characters.')
        fingerprint = hashlib.sha256(canonical(body).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cached = db.execute('SELECT * FROM idempotency WHERE route=? AND key=?', (route, key)).fetchone()
            if cached:
                if cached['fingerprint'] != fingerprint:
                    raise Problem(409, 'idempotency_conflict', 'This key was already used for a different request.')
                return json.loads(cached['response']), True
            if route == 'rfq':
                validate('rfq', body)
                self.checked_options(body)
                c, now = self.catalog, self.clock()
                quote_id, token, csrf = secrets.token_urlsafe(18), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                base = c['base_prices_minor'][str(body['quantity'])]
                finishing = c['finishing_surcharges_minor'][body['finishing']]
                subtotal = base + finishing
                tax = (subtotal * c['tax_basis_points'] + 5000) // 10000
                response = {'protocol_version': VERSION, 'mode': 'demo', 'quote_id': quote_id,
                            'status': 'quoted', 'created_at': stamp(now),
                            'expires_at': stamp(now + c['quote_validity_seconds']), 'request': body,
                            'lines': [{'description': f"{body['quantity']} double-sided business cards", 'amount_minor': base},
                                      {'description': body['finishing'], 'amount_minor': finishing}],
                            'money': {'currency': c['currency'], 'subtotal_minor': subtotal, 'tax_minor': tax,
                                      'total_minor': subtotal + tax, 'tax_basis_points': c['tax_basis_points'],
                                      'tax_label': c['tax_label']},
                            'production': {'business_days': c['production_business_days'], 'starts_after': 'printer_accepts_artwork'},
                            'artwork_requirements': c['artwork'], 'review_url': self.base + '/review/' + token,
                            'quote_url': self.base + '/v0.1/quotes/' + quote_id,
                            'order_url': self.base + '/v0.1/orders', 'notice': NOTICE}
                validate('quote', response)
                db.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?)',
                           (quote_id, canonical(response), now + c['quote_validity_seconds'], 'quoted', token, csrf, None))
            elif route == 'order':
                validate('order_request', body)
                row = db.execute('SELECT * FROM quotes WHERE id=?', (body['quote_id'],)).fetchone()
                if row is None:
                    raise Problem(404, 'not_found', 'Quote not found.')
                existing = db.execute('SELECT body FROM orders WHERE quote_id=?', (body['quote_id'],)).fetchone()
                if existing:
                    response = json.loads(existing['body'])
                else:
                    quote = self.quote_body(row)
                    if quote['status'] == 'expired':
                        raise Problem(410, 'quote_expired', 'Request a fresh quote and approval.')
                    if quote['status'] != 'approved':
                        raise Problem(409, 'approval_required', 'The customer must approve this exact quote first.')
                    order_id = secrets.token_urlsafe(18)
                    response = {'protocol_version': VERSION, 'mode': 'demo', 'order_id': order_id,
                                'quote_id': row['id'], 'client_reference': quote['request']['client_reference'],
                                'status': 'pending_artwork_review', 'created_at': stamp(self.clock()),
                                'approved_at': row['approved_at'], 'money': quote['money'],
                                'order_url': self.base + '/v0.1/orders/' + order_id, 'notice': NOTICE}
                    validate('order', response)
                    db.execute('INSERT INTO orders VALUES (?,?,?)', (order_id, row['id'], canonical(response)))
                    db.execute("UPDATE quotes SET status='ordered' WHERE id=?", (row['id'],))
            else:
                raise Problem(404, 'not_found', 'Unknown operation.')
            db.execute('INSERT INTO idempotency VALUES (?,?,?,?)', (route, key, fingerprint, canonical(response)))
            return response, False

    def review(self, token):
        with self.connect() as db:
            row = db.execute('SELECT * FROM quotes WHERE review_token=?', (token,)).fetchone()
            if row is None:
                raise Problem(404, 'not_found', 'Review link not found.')
            return self.quote_body(row), row['csrf']

    def decide(self, token, csrf, decision):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM quotes WHERE review_token=?', (token,)).fetchone()
            if row is None:
                raise Problem(404, 'not_found', 'Review link not found.')
            if not secrets.compare_digest(row['csrf'], csrf):
                raise Problem(403, 'invalid_csrf', 'Reload the review page and try again.')
            if decision not in ('approved', 'rejected'):
                raise Problem(400, 'invalid_decision', 'Choose approve or decline.')
            status = self.quote_body(row)['status']
            if status == 'expired':
                raise Problem(410, 'quote_expired', 'Request a fresh quote.')
            if status != 'quoted':
                if status == decision:
                    return
                raise Problem(409, 'decision_locked', 'This quote already has a decision or order.')
            db.execute('UPDATE quotes SET status=?, approved_at=? WHERE id=?',
                       (decision, stamp(self.clock()) if decision == 'approved' else None, row['id']))


def review_html(quote, csrf):
    esc = html.escape
    q, m = quote['request'], quote['money']
    amount = lambda n: f"{m['currency']} {n / 100:,.2f}"
    action = urlsplit(quote['review_url']).path
    controls = ''
    if quote['status'] == 'quoted':
        controls = f'''<form method="post" action="{esc(action)}">
        <input type="hidden" name="csrf" value="{esc(csrf)}">
        <button name="decision" value="approved">Approve demo quote</button>
        <button class="secondary" name="decision" value="rejected">Decline</button></form>'''
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Review your quote · AgentOrder</title>
    <style>body{{font:17px/1.6 system-ui,sans-serif;background:#f4f3ee;color:#183b36;margin:0}}
    main{{max-width:660px;margin:48px auto;padding:32px;background:white;border:1px solid #d9dfd9;border-radius:16px}}
    h1{{line-height:1.2}} .tag{{font-size:13px;letter-spacing:.1em;text-transform:uppercase}}
    .note{{background:#fff2cf;padding:14px;border-radius:8px}} dl{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
    dd{{margin:0}} .total{{font-size:28px;font-weight:700}} button{{font:inherit;padding:12px 18px;background:#185c4d;color:white;border:0;border-radius:8px;cursor:pointer;margin:8px 8px 0 0}}
    .secondary{{background:#e8ece8;color:#183b36}} small{{overflow-wrap:anywhere}} @media(max-width:700px){{main{{margin:16px;padding:20px}} dl{{grid-template-columns:1fr}}}}</style>
    <main><p class="tag">AgentOrder / Demonstration</p><h1>Review your business cards</h1>
    <p class="note">Sample quote. Approval does not charge you or send anything to print.</p>
    <dl><dt>Quantity</dt><dd>{q['quantity']:,}</dd><dt>Finished size</dt><dd>{q['size_mm']['width']} × {q['size_mm']['height']} mm</dd>
    <dt>Stock</dt><dd>{q['stock_gsm']} gsm {esc(q['stock_finish'])}</dd><dt>Print</dt><dd>Full colour, both sides</dd>
    <dt>Finishing</dt><dd>{esc(q['finishing'].replace('_', ' '))}</dd><dt>Collection</dt><dd>Pickup (demo)</dd>
    <dt>Production estimate</dt><dd>{quote['production']['business_days']} business days after the printer accepts artwork</dd>
    <dt>Artwork</dt><dd>2-page CMYK PDF, 3 mm bleed. Artwork has not been inspected.</dd>
    <dt>Subtotal</dt><dd>{amount(m['subtotal_minor'])}</dd><dt>{esc(m['tax_label'])}</dt><dd>{amount(m['tax_minor'])}</dd></dl>
    <p class="total">{amount(m['total_minor'])}</p><p>Status: <strong>{esc(quote['status'])}</strong><br>
    Valid until {esc(quote['expires_at'])} (UTC)</p>{controls}
    <p>After approval, your agent can submit this demo order. Artwork review is still required.</p>
    <small>Quote {esc(quote['quote_id'])}. This private review link permits a demo decision; it does not verify customer identity.</small></main></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = 'AgentOrderDemo/0.1'

    def log_message(self, *args):
        # Do not put capability links, customer requests or bearer credentials in access logs.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, body, content_type='application/json', extra=None):
        data = canonical(body).encode() if content_type == 'application/json' else body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def auth(self):
        expected = 'Bearer ' + self.server.api_key
        if not secrets.compare_digest(self.headers.get('Authorization', ''), expected):
            raise Problem(401, 'unauthorized', 'A valid printer API bearer token is required.')

    def read_body(self, expected_type):
        if self.headers.get('Transfer-Encoding'):
            raise Problem(400, 'unsupported_encoding', 'Use Content-Length, not chunked encoding.')
        if self.headers.get_content_type() != expected_type:
            raise Problem(415, 'unsupported_media_type', 'Expected ' + expected_type + '.')
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            length = -1
        if length < 0:
            raise Problem(411, 'length_required', 'Supply Content-Length.')
        if length > 32768:
            raise Problem(413, 'body_too_large', 'Maximum request body is 32 KiB.')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise Problem(400, 'incomplete_body', 'Incomplete request body.')
        try:
            text = raw.decode('utf-8')
            if expected_type == 'application/json':
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise ValueError('Duplicate JSON member')
                        result[key] = value
                    return result
                return json.loads(text, object_pairs_hook=pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
            return parse_qs(text, strict_parsing=True, max_num_fields=5)
        except (ValueError, UnicodeError, RecursionError):
            raise Problem(400, 'malformed_body', 'The request body is malformed.')

    def dispatch(self):
        store = self.server.store
        if self.headers.get('Host') != urlsplit(store.base).netloc:
            raise Problem(400, 'invalid_host', 'Use the configured server address.')
        path = urlsplit(self.path).path
        if self.command == 'GET':
            if path == '/.well-known/agentorder':
                return self.reply(200, store.discovery())
            if path == '/catalog':
                return self.reply(200, store.catalog)
            if path == '/schema.json':
                return self.reply(200, SCHEMA)
            if path == '/specification':
                return self.reply(200, (ROOT / 'SPECIFICATION.md').read_text(), 'text/plain')
            if path == '/':
                return self.reply(200, {'name': 'AgentOrder reference endpoint', 'mode': 'demo',
                                        'discovery': store.base + '/.well-known/agentorder', 'notice': NOTICE})
            if path.startswith('/review/'):
                quote, csrf = store.review(path[len('/review/'):])
                return self.reply(200, review_html(quote, csrf), 'text/html')
            self.auth()
            if path.startswith('/v0.1/quotes/'):
                return self.reply(200, store.get_quote(path[len('/v0.1/quotes/'):]))
            if path.startswith('/v0.1/orders/'):
                return self.reply(200, store.get_order(path[len('/v0.1/orders/'):]))
        elif self.command == 'POST':
            if path.startswith('/review/'):
                origin = self.headers.get('Origin')
                if origin and origin != store.base:
                    raise Problem(403, 'invalid_origin', 'Submit from the review page.')
                fields = self.read_body('application/x-www-form-urlencoded')
                if set(fields) != {'csrf', 'decision'} or any(len(v) != 1 for v in fields.values()):
                    raise Problem(400, 'invalid_form', 'Supply one decision and one CSRF value.')
                store.decide(path[len('/review/'):], fields['csrf'][0], fields['decision'][0])
                return self.reply(303, '', 'text/plain', {'Location': path})
            self.auth()
            routes = {'/v0.1/rfqs': 'rfq', '/v0.1/orders': 'order'}
            if path in routes:
                body = self.read_body('application/json')
                response, replay = store.mutate(routes[path], self.headers.get('Idempotency-Key'), body)
                location = response['quote_url'] if routes[path] == 'rfq' else response['order_url']
                return self.reply(200 if replay else 201, response,
                                  extra={'Location': location, 'Idempotency-Replayed': str(replay).lower()})
        raise Problem(404, 'not_found', 'Endpoint not found.')

    def handle_request(self):
        try:
            self.dispatch()
        except Problem as problem:
            self.reply(problem.status, problem.body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (ValueError, RecursionError):
            self.reply(400, {'error': {'code': 'malformed_body', 'message': 'Invalid JSON value.', 'details': []}})
        except Exception:
            self.reply(500, {'error': {'code': 'internal_error', 'message': 'Unexpected server error.', 'details': []}})

    do_GET = handle_request
    do_POST = handle_request


def make_server(port, db, api_key, catalog=None, clock=time.time):
    if len(api_key) < 24:
        raise ValueError('AGENTORDER_API_KEY must be at least 24 characters.')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.api_key = api_key
    server.store = Store(db, f'http://127.0.0.1:{server.server_port}', catalog, clock)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--db', default='agentorder-demo.sqlite3')
    parser.add_argument('--catalog', default=str(ROOT / 'catalog.json'))
    args = parser.parse_args()
    key = os.environ.get('AGENTORDER_API_KEY', '')
    if len(key) < 24:
        parser.error('Set AGENTORDER_API_KEY to a random value of at least 24 characters; see README.')
    app = make_server(args.port, args.db, key, args.catalog)
    print(f'AgentOrder demo listening at {app.store.base} — sample pricing only.', flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close()
