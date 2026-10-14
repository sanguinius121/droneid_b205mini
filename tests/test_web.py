import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
from server import Receiver,check_origin
from fastapi import HTTPException
from starlette.requests import Request
class WebTests(unittest.TestCase):
 def test_only_crc_valid_counted_and_partial_row_ignored(self):
  r=Receiver()
  with tempfile.TemporaryDirectory() as d:
   r.output=Path(d)/'test.fc32'
   Path(str(r.output)+'.decoded.jsonl').write_text('\n'.join([json.dumps({'result':{'crc_valid':True}}),json.dumps({'result':{'crc_valid':False}}),'{partial']))
   self.assertEqual(r.state()['decoded_messages'],1)
 def test_idle_does_not_start_hardware(self):
  with patch('server.subprocess.Popen') as p:
   self.assertEqual(Receiver().state()['status'],'idle');p.assert_not_called()
 def test_reject_foreign_origin(self):
  req=Request({'type':'http','method':'POST','path':'/api/start','scheme':'http','server':('localhost',8088),'headers':[(b'host',b'localhost:8088'),(b'origin',b'https://example.org')],'query_string':b''})
  with self.assertRaises(HTTPException):check_origin(req)
 def test_same_origin(self):
  req=Request({'type':'http','method':'POST','path':'/api/start','scheme':'http','server':('localhost',8088),'headers':[(b'host',b'localhost:8088'),(b'origin',b'http://localhost:8088')],'query_string':b''});check_origin(req)
