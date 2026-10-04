"""Contract fixtures run in fresh interpreters to isolate identical module names."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import json, sys
from decimal import Decimal
from cex_plugin.adapter import create_client
from cex_plugin.models import HedgeSide
from cex_plugin.types import LiveTradingDisabled
from cex_plugin import http
config=json.loads(sys.argv[1]); calls=[]
class Transport:
 def request(self, **kwargs):
  calls.append(kwargs)
  url=kwargs['url']
  if '/account' in url:
   return ({'accountType':'SPOT','canTrade':False,'balances':[{'asset':'ARRR','free':'2','locked':'1'}]}
    if config['venue']=='MEXC' else [{'currency':'ARRR','available':'2','locked':'1'}])
  if '/depth' in url or '/order_book' in url:
   return {'bids':[['0.3','4']],'asks':[['0.31','5']]}
  raise AssertionError('Unexpected fixture request')
client=create_client(config,api_key='fixture-key',api_secret='fixture-secret',trading_enabled=False)
client.transport=Transport()
assert client.account()['balances'][0]['free']=='2'
assert client.order_book('ARRRUSDT').bids[0].quantity==Decimal('4')
assert all('fixture-secret' not in x['url'] for x in calls)
assert all(x['method']=='GET' for x in calls)
try:
 client.place_limit_order(symbol='ARRRUSDT',side=HedgeSide.SELL,quantity=Decimal('1'),price=Decimal('0.3'),client_order_id='fixture')
 raise AssertionError('Read-only client accepted a write')
except LiveTradingDisabled: pass
assert len(calls)==2
http.configure_wallet_proxy('http://127.0.0.1:12345')
assert http._wallet_proxy_url=='http://127.0.0.1:12345'
print('normalized account/book, signing and write gate passed')
'''

class AdapterContractTests(unittest.TestCase):
 def test_bundles_match_source_and_catalog(self):
  catalog=json.loads((ROOT/'catalog.json').read_text())
  for item in catalog['plugins']:
   for kind in ('config','adapter'):
    self.assertEqual(hashlib.sha256((ROOT/item[kind]).read_bytes()).hexdigest(),item[kind+'_sha256'])
   with ZipFile(ROOT/item['adapter']) as bundle:
    for name in bundle.namelist():
     self.assertEqual(bundle.read(name),(ROOT/Path(item['adapter']).parent/'src'/name).read_bytes())
 def test_mexc_and_gate_common_contract_without_network(self):
  for venue in ('mexc','gate'):
   with self.subTest(venue=venue):
    config=(ROOT/'plugins'/venue/'config.json').read_text()
    env={'PYTHONPATH':str(ROOT/'plugins'/venue/'adapter.zip')}
    result=subprocess.run([sys.executable,'-c',FIXTURE,config],env=env,text=True,capture_output=True,timeout=10)
    self.assertEqual(result.returncode,0,result.stderr)
