import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
from urllib.parse import urlencode, urlsplit

from jsonschema import Draft202012Validator
from server import ROOT, SCHEMA, Problem, Store, make_server, validate

RFQ = json.loads((ROOT / 'example-rfq.json').read_text())
KEY = 'test-only-agentorder-key-123456789'


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.now = [1800000000.0]
        cls.app = make_server(0, Path(cls.temp.name) / 'test.sqlite3', KEY, clock=lambda: cls.now[0])
        cls.thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.app.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, body=None, key=None, auth=True, method=None, extra=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.app.server_port, timeout=5)
        headers = {'Authorization': 'Bearer ' + KEY} if auth else {}
        if key:
            headers['Idempotency-Key'] = key
        if body is not None:
            headers['Content-Type'] = 'application/json'
            raw = json.dumps(body)
        headers.update(extra or {})
        conn.request(method or ('POST' if raw is not None else 'GET'), path, body=raw, headers=headers)
        response = conn.getresponse()
        payload = response.read().decode()
        result = json.loads(payload) if 'application/json' in response.getheader('Content-Type', '') else payload
        output = response.status, result, dict(response.getheaders())
        conn.close()
        return output

    def quote(self, suffix='', body=None):
        status, quote, _ = self.request('/v0.1/rfqs', body or copy.deepcopy(RFQ), self.id() + suffix)
        self.assertEqual(status, 201, quote)
        return quote

    def decide(self, quote, decision='approved', csrf=None):
        path = urlsplit(quote['review_url']).path
        status, page, headers = self.request(path, auth=False)
        self.assertEqual(status, 200)
        if csrf is None:
            csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        return self.request(path, auth=False, raw=urlencode({'csrf': csrf, 'decision': decision}),
                            extra={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': self.app.store.base})

    def order(self, quote, suffix=''):
        return self.request('/v0.1/orders', {'protocol_version': '0.1.0', 'quote_id': quote['quote_id']},
                            self.id() + '-order' + suffix)

    def test_schema_and_examples(self):
        Draft202012Validator.check_schema(SCHEMA)
        validate('rfq', RFQ)
        examples = json.loads((ROOT / 'examples.json').read_text())
        for kind, example in examples.items():
            validate(kind, example)

    def test_discovery(self):
        status, declaration, _ = self.request('/.well-known/agentorder', auth=False)
        self.assertEqual(status, 200)
        validate('discovery', declaration)
        self.assertTrue(declaration['rfq_url'].startswith(self.app.store.base))

    def test_complete_journey(self):
        quote = self.quote()
        validate('quote', quote)
        self.assertEqual(quote['money']['total_minor'], 10925)
        self.assertEqual(sum(line['amount_minor'] for line in quote['lines']), 9500)
        self.assertEqual(self.order(quote)[0], 409)
        self.assertEqual(self.decide(quote)[0], 303)
        current = self.request(urlsplit(quote['quote_url']).path)[1]
        self.assertEqual(current['status'], 'approved')
        status, order, _ = self.order(quote)
        self.assertEqual(status, 201, order)
        validate('order', order)
        self.assertEqual(order['status'], 'pending_artwork_review')
        self.assertEqual(order['money'], quote['money'])
        self.assertEqual(self.request(urlsplit(order['order_url']).path)[1], order)
        self.assertEqual(self.request(urlsplit(quote['quote_url']).path)[1]['status'], 'ordered')

    def test_decline_blocks_order(self):
        quote = self.quote()
        self.assertEqual(self.decide(quote, 'rejected')[0], 303)
        self.assertEqual(self.order(quote)[0], 409)

    def test_get_does_not_approve(self):
        quote = self.quote()
        self.request(urlsplit(quote['review_url']).path, auth=False)
        self.assertEqual(self.request(urlsplit(quote['quote_url']).path)[1]['status'], 'quoted')

    def test_customer_csrf(self):
        quote = self.quote()
        self.assertEqual(self.decide(quote, csrf='wrong')[0], 403)
        self.assertEqual(self.order(quote)[0], 409)

    def test_cross_origin_form(self):
        quote = self.quote()
        path = urlsplit(quote['review_url']).path
        status, _, _ = self.request(path, auth=False, raw='csrf=x&decision=approved',
                                    extra={'Content-Type':'application/x-www-form-urlencoded','Origin':'https://other.example'})
        self.assertEqual(status, 403)

    def test_unsupported_options(self):
        changes = {'quantity': 501, 'size_mm': {'width': 85, 'height': 55}, 'stock_gsm': 400,
                   'stock_finish': 'uncoated', 'sides': 1, 'colour': 'black_only',
                   'finishing': 'gold_foil', 'fulfilment': {'method': 'delivery'},
                   'artwork': dict(RFQ['artwork'], colour_space='RGB')}
        for field, value in changes.items():
            with self.subTest(field=field):
                body = copy.deepcopy(RFQ)
                body[field] = value
                status, error, _ = self.request('/v0.1/rfqs', body, self.id())
                self.assertEqual(status, 422)
                self.assertEqual(error['error']['code'], 'unsupported_options')

    def test_schema_rejects_missing_or_extra_fields(self):
        body = copy.deepcopy(RFQ)
        del body['artwork']
        self.assertEqual(self.request('/v0.1/rfqs', body, self.id())[0], 422)
        body = dict(RFQ, approved=True)
        self.assertEqual(self.request('/v0.1/rfqs', body, self.id())[0], 422)

    def test_auth_required(self):
        self.assertEqual(self.request('/v0.1/rfqs', RFQ, self.id(), auth=False)[0], 401)
        quote = self.quote()
        self.assertEqual(self.request(urlsplit(quote['quote_url']).path, auth=False)[0], 401)

    def test_retry_and_conflict(self):
        first = self.quote()
        status, replay, headers = self.request('/v0.1/rfqs', RFQ, self.id())
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        self.assertEqual(headers['Idempotency-Replayed'], 'true')
        self.assertEqual(self.request('/v0.1/rfqs', dict(RFQ, quantity=250), self.id())[0], 409)

    def test_concurrent_order_deduplication(self):
        quote = self.quote()
        self.decide(quote)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda n: self.order(quote, str(n)), range(6)))
        self.assertTrue(all(r[0] == 201 for r in results), results)
        self.assertEqual(len({r[1]['order_id'] for r in results}), 1)
        status, replay, _ = self.order(quote, '0')
        self.assertEqual(status, 200)
        self.assertEqual(replay, results[0][1])

    def test_expiry_blocks_approval_and_order(self):
        quote = self.quote()
        path = urlsplit(quote['review_url']).path
        page = self.request(path, auth=False)[1]
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        old = self.now[0]
        try:
            self.now[0] += 86401
            self.assertEqual(self.decide(quote, csrf=csrf)[0], 410)
            self.assertEqual(self.order(quote)[0], 410)
            self.assertEqual(self.request(urlsplit(quote['quote_url']).path)[1]['status'], 'expired')
        finally:
            self.now[0] = old

    def test_approved_quote_still_expires(self):
        quote = self.quote()
        self.decide(quote)
        old = self.now[0]
        try:
            self.now[0] += 86401
            self.assertEqual(self.order(quote)[0], 410)
        finally:
            self.now[0] = old

    def test_persistence_and_catalog_changes(self):
        quote = self.quote()
        self.decide(quote)
        first = self.order(quote)[1]
        new_store = Store(self.app.store.path, self.app.store.base, clock=lambda: self.now[0])
        new_store.catalog['base_prices_minor']['500'] = 999999
        self.assertEqual(new_store.get_quote(quote['quote_id'])['money']['total_minor'], 10925)
        self.assertEqual(new_store.get_order(first['order_id']), first)

    def test_lamination_price(self):
        quote = self.quote(body=dict(RFQ, finishing='matte_lamination_both_sides'))
        self.assertEqual(quote['money']['subtotal_minor'], 12000)
        self.assertEqual(quote['money']['tax_minor'], 1800)
        self.assertEqual(quote['money']['total_minor'], 13800)

    def test_price_cannot_be_overridden(self):
        quote = self.quote()
        self.decide(quote)
        status, _, _ = self.request('/v0.1/orders', {'protocol_version':'0.1.0','quote_id':quote['quote_id'],'total_minor':1}, self.id())
        self.assertEqual(status, 422)

    def test_bad_json_and_body_limits(self):
        for raw in ('{', '{"quantity":NaN}', '{"x":1,"x":2}'):
            self.assertEqual(self.request('/v0.1/rfqs', raw=raw, key=self.id(), extra={'Content-Type':'application/json'})[0], 400)
        self.assertEqual(self.request('/v0.1/rfqs', raw='x'*32769, key=self.id(), extra={'Content-Type':'application/json'})[0], 413)
        self.assertEqual(self.request('/v0.1/rfqs', raw='{}', key=self.id(), extra={'Content-Type':'text/plain'})[0], 415)

    def test_idempotency_key_required(self):
        self.assertEqual(self.request('/v0.1/rfqs', RFQ)[0], 400)

    def test_host_header(self):
        self.assertEqual(self.request('/.well-known/agentorder', extra={'Host':'other.example'})[0], 400)

    def test_unknown_quote(self):
        self.assertEqual(self.request('/v0.1/quotes/not-a-real-quote')[0], 404)


if __name__ == '__main__':
    unittest.main()
