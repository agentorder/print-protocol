"""Loopback-only AgentOrder v0.2 reference server; not hosted-product code."""
from __future__ import annotations
import base64, hashlib, json, secrets, sqlite3, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.exceptions import InvalidSignature
from jsonschema import Draft202012Validator, FormatChecker
from schema_support import ucp_registry, config_allows

ROOT=Path(__file__).resolve().parent; SCHEMA=json.loads((ROOT/'schema.json').read_text()); VERSION='0.2.0'
def canon(v): return json.dumps(v,sort_keys=True,separators=(',',':'))
def b64d(v): return base64.urlsafe_b64decode(v+'='*(-len(v)%4))
def stamp(t): return datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
class Problem(Exception):
 def __init__(self,status,code,message,details=[]): self.status=status; self.body={'agentorder_version':VERSION,'error':{'code':code,'message':message,'details':details}}
def validate(kind,body):
 v=Draft202012Validator({'$ref':f'#/$defs/{kind}','$defs':SCHEMA['$defs']},registry=ucp_registry(),format_checker=FormatChecker()); e=list(v.iter_errors(body))
 if e: raise Problem(422,'invalid_request','Request does not match AgentOrder v0.2 schema.',[{'code':x.message} for x in e[:5]])
MIGRATIONS=["""CREATE TABLE IF NOT EXISTS printers(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,signing_key TEXT NOT NULL);CREATE TABLE IF NOT EXISTS printer_capabilities(printer_id TEXT PRIMARY KEY,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS signing_keys(printer_id TEXT PRIMARY KEY,kid TEXT NOT NULL,public_jwk TEXT NOT NULL);CREATE TABLE IF NOT EXISTS rfqs(id TEXT PRIMARY KEY,printer_id TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL);CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,printer_id TEXT NOT NULL,rfq_id TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL);CREATE TABLE IF NOT EXISTS idempotency_keys(printer_id TEXT NOT NULL,key TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(printer_id,key));"""]
class Store:
 def __init__(self,path,base,platforms,clock=time.time):
  self.path=str(path);self.base=base;self.platforms=platforms;self.clock=clock
  with self.db() as d:
   for q in MIGRATIONS:d.executescript(q)
 def db(self): d=sqlite3.connect(self.path);d.row_factory=sqlite3.Row;return d
 def seed(self,printer):
  with self.db() as d:
   d.execute('INSERT OR REPLACE INTO printers VALUES(?,?,?,?)',(printer['id'],printer['name'],canon(printer['config']),'local'))
   d.execute('INSERT OR REPLACE INTO printer_capabilities VALUES(?,?)',(printer['id'],canon(printer['config'])))
   d.execute('INSERT OR REPLACE INTO signing_keys VALUES(?,?,?)',(printer['id'],printer['kid'],canon(printer['jwk'])))
 def printer(self,pid):
  with self.db() as d:
   r=d.execute('SELECT * FROM printers WHERE id=?',(pid,)).fetchone()
  if not r: raise Problem(404,'invalid_request','Unknown printer.')
  return r
 def profile(self,pid):
  r=self.printer(pid); config=json.loads(r['config']);
  with self.db() as d:k=d.execute('SELECT * FROM signing_keys WHERE printer_id=?',(pid,)).fetchone()
  return {'ucp':{'version':'2026-06-15','services':{'dev.ucp.shopping':[{'version':'2026-06-15','spec':'https://ucp.dev/specification/','transport':'rest','schema':'https://ucp.dev/services/shopping/rest.openapi.json','endpoint':self.base+'/ucp/v1/printers/'+pid}]},'capabilities':{'dev.ucp.shopping.checkout':[{'version':'2026-06-15','spec':'https://ucp.dev/specification/','schema':'https://ucp.dev/schemas/shopping/checkout.json'}],'dev.ucp.common.payment.ap2_mandate':[{'version':'2026-06-15','spec':'https://ucp.dev/specification/','schema':'https://ucp.dev/schemas/common/payment_ap2_mandate.json','extends':'dev.ucp.shopping.checkout'}],'org.agentorder.shopping.print_quote':[{'version':'2026-09-06','spec':'https://agentorder.org/specification/0.2','schema':'https://agentorder.org/schemas/0.2/schema.json','extends':['dev.ucp.shopping.cart','dev.ucp.shopping.checkout'],'config':config}]},'payment_handlers':{}},'keys':[json.loads(k['public_jwk'])]}
 def verify(self,pid,headers,raw):
  platform=headers.get('UCP-Agent',''); profile=self.platforms.get(platform)
  if not profile: raise Problem(401,'invalid_signature','Unknown platform profile.')
  active=profile['ucp']['capabilities'].get('org.agentorder.shopping.print_quote',[])
  if not any(x['version']=='2026-09-06' for x in active): raise Problem(403,'invalid_signature','AgentOrder capability was not negotiated.')
  try: kid,ts,nonce,sig=headers['X-AgentOrder-Signature'].split(':');ts=int(ts)
  except Exception: raise Problem(401,'invalid_signature','Malformed request signature.')
  if abs(self.clock()-ts)>300: raise Problem(401,'invalid_signature','Stale request signature.')
  if nonce in getattr(self,'nonces',set()): raise Problem(409,'idempotency_conflict','Replayed request signature.')
  key=next((x for x in profile['keys'] if x.get('kid')==kid),None)
  if not key: raise Problem(401,'invalid_signature','Unknown signing key.')
  try:
   pub=ec.EllipticCurvePublicNumbers(int.from_bytes(b64d(key['x']),'big'),int.from_bytes(b64d(key['y']),'big'),ec.SECP256R1()).public_key(); raw_sig=b64d(sig); pub.verify(encode_dss_signature(int.from_bytes(raw_sig[:32],'big'),int.from_bytes(raw_sig[32:],'big')),f'{ts}.{nonce}.'.encode()+raw,ec.ECDSA(hashes.SHA256()))
  except (InvalidSignature,ValueError): raise Problem(401,'invalid_signature','Invalid request signature.')
  self.nonces=getattr(self,'nonces',set());self.nonces.add(nonce)
 def rfq(self,pid,key,body):
  if not key or len(key)<8: raise Problem(400,'idempotency_conflict','Idempotency-Key required.')
  validate('rfq',body);r=self.printer(pid);config=json.loads(r['config'])
  if not config_allows(body['print_job'],config): raise Problem(422,'unsupported_print_job','Print job is outside printer capability config.')
  fp=hashlib.sha256(canon(body).encode()).hexdigest(); response={'agentorder_version':VERSION,'rfq_id':body['rfq_id'],'status':'received'}
  with self.db() as d:
   old=d.execute('SELECT * FROM idempotency_keys WHERE printer_id=? AND key=?',(pid,key)).fetchone()
   if old:
    if old['fingerprint']!=fp: raise Problem(409,'idempotency_conflict','Key used with different body.')
    return json.loads(old['response']),True
   try:d.execute('INSERT INTO rfqs VALUES(?,?,?,?,?)',(body['rfq_id'],pid,canon(body),'open',self.clock()))
   except sqlite3.IntegrityError: raise Problem(409,'idempotency_conflict','RFQ id already exists.')
   d.execute('INSERT INTO idempotency_keys VALUES(?,?,?,?)',(pid,key,fp,canon(response)))
  return response,False
 def get_quote(self,pid,qid):
  with self.db() as d:r=d.execute('SELECT * FROM quotes WHERE id=?',(qid,)).fetchone()
  if not r: raise Problem(404,'invalid_request','Quote not found.')
  if r['printer_id']!=pid: raise Problem(403,'invalid_signature','Cross-tenant quote access denied.')
  return json.loads(r['body'])
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def reply(self,status,body,extra={}):
  raw=canon(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');[self.send_header(k,v) for k,v in extra.items()];self.end_headers();self.wfile.write(raw)
 def read(self):
  if self.headers.get_content_type()!='application/json':raise Problem(400,'invalid_request','JSON required.')
  try:return self.rfile.read(int(self.headers['Content-Length']))
  except Exception:raise Problem(400,'invalid_request','Content-Length required.')
 def handle_request(self):
  try:
   p=urlsplit(self.path).path;parts=p.split('/');store=self.server.store
   if self.command=='GET' and len(parts)==6 and parts[1:4]==['ucp','v1','printers'] and parts[5]=='.well-known': return self.reply(200,store.profile(parts[4]))
   if self.command=='GET' and len(parts)==6 and parts[1:4]==['ucp','v1','printers'] and parts[5].startswith('quotes-'): return self.reply(200,store.get_quote(parts[4],parts[5][7:]))
   if self.command=='POST' and len(parts)==6 and parts[1:4]==['ucp','v1','printers'] and parts[5]=='rfqs':
    raw=self.read();store.verify(parts[4],self.headers,raw);body=json.loads(raw);out,replay=store.rfq(parts[4],self.headers.get('Idempotency-Key'),body);return self.reply(200 if replay else 202,out,{'Idempotency-Replayed':str(replay).lower()})
   raise Problem(404,'invalid_request','Endpoint not found.')
  except Problem as e:self.reply(e.status,e.body)
  except Exception:self.reply(500,{'agentorder_version':VERSION,'error':{'code':'invalid_request','message':'Internal reference-server error.','details':[]}})
 do_GET=handle_request;do_POST=handle_request
def make_server(port,db,platforms,printer,clock=time.time):
 s=ThreadingHTTPServer(('127.0.0.1',port),Handler);s.store=Store(db,f'http://127.0.0.1:{s.server_port}',platforms,clock);s.store.seed(printer);return s
