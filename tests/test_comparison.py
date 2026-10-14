"""Independent transport fixtures; no RF hardware or captured flight data."""
import importlib.util,json,os,pty,signal,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parents[1]/'comparison'
sys.path.insert(0,str(HERE))
import engine,adapters,collect_uart
WIRE=b'CRC flag 1\r\nnum=12, device=2708, serial=AIR2TEST, model=Air 2, drone_GPS=105.678901,21.012345, home_GPS=0,0, Altitude=35.5, freq=2444.5, rssi=-70, distance=0.1km,\r\n'
CSV=b'=dji_O,3,2444.5,-70,DJI Mini 3(63),MINI3TEST,105.678901,21.012345,0,0,0,0,3.5|10,0|0|0;\n'
def wait_for(path,process):
 deadline=time.monotonic()+6
 while time.monotonic()<deadline:
  if path.exists() and path.stat().st_size:return
  if process.poll() is not None:raise AssertionError('Collector exited early')
  time.sleep(.02)
 raise AssertionError('Timed out waiting for '+str(path))
class ComparisonTests(unittest.TestCase):
 def test_uart_fragmentation_and_coordinate_order(self):
  collect_uart.ORDER='lonlat';p=collect_uart.MapParser();rows=[]
  for b in WIRE:rows+=p.feed(bytes([b]),1000000000,1)
  self.assertEqual(len(rows),1);r=adapters.normalize('uart',rows[0]);self.assertEqual(r['position'],[21.012345,105.678901]);self.assertEqual(r['crc'],'reported_pass')
 def test_crc_evidence_never_upgraded_by_coordinates(self):
  self.assertEqual(adapters.normalize('uart',dict(monotonic_ns=1,serial='X',crc_flag=0,latitude=21,longitude=105))['crc'],'reported_fail')
  r=adapters.normalize('e200',dict(monotonic_ns=1,decoded=dict(serial_number='X',drone_lat=21,drone_lon=105)));self.assertEqual(r['crc'],'unknown')
  self.assertIsNone(adapters.normalize('ours',dict(result=dict(crc_valid=False))))
 def test_partial_json_tail(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'rows';t=engine.Tail(p);p.write_text('{"x":');self.assertEqual(t.read(),[])
   with p.open('a') as f:f.write('1}\n')
   self.assertEqual(t.read(),[{'x':1}]);self.assertEqual(t.read(),[])
 def test_common_window_excludes_reference_outside_capture(self):
  e=engine.Engine();e.begin=10;e.end=20;e.rows=[dict(t=t,source='e200') for t in [9,10,15,20,21]]
  self.assertEqual([r['t'] for r in e.snapshot()['events']],[10,15,20]);self.assertEqual(e.snapshot()['elapsed_s'],10)
 def test_e200_tcp_collector_fragmented_stream(self):
  with tempfile.TemporaryDirectory() as d:
   with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
   out=Path(d)/'e200';proc=subprocess.Popen([sys.executable,str(HERE/'collect_e200.py'),'--output',str(out),'--seconds','10','--listen-ip','127.0.0.1','--listen-port',str(port),'--peer-ip','127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    wait_for(out/'ready.json',proc)
    with socket.create_connection(('127.0.0.1',port)) as conn:
     conn.sendall(CSV[:17]);conn.sendall(CSV[17:]);wait_for(out/'messages.jsonl',proc)
    proc.send_signal(signal.SIGTERM);_,err=proc.communicate(timeout=6);self.assertEqual(proc.returncode,0,err.decode())
    rows=[json.loads(l) for l in (out/'messages.jsonl').read_text().splitlines()];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['decoded']['serial_number'],'MINI3TEST');self.assertEqual(adapters.normalize('e200',rows[0])['crc'],'unknown');self.assertEqual((out/'tcp.bin').read_bytes(),CSV)
   finally:
    if proc.poll() is None:proc.kill();proc.communicate()
 def test_uart_real_collector_with_pseudo_terminal(self):
  with tempfile.TemporaryDirectory() as d:
   master,slave=pty.openpty();device=os.ttyname(slave);out=Path(d)/'uart'
   proc=subprocess.Popen([sys.executable,str(HERE/'collect_uart.py'),'--output',str(out),'--seconds','10','--ports',device,'--gps-order','lonlat'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    wait_for(out/'ready.json',proc);os.write(master,WIRE[:25]);os.write(master,WIRE[25:]);wait_for(out/'messages.jsonl',proc)
    proc.send_signal(signal.SIGTERM);_,err=proc.communicate(timeout=6);self.assertEqual(proc.returncode,0,err.decode())
    rows=[json.loads(l) for l in (out/'messages.jsonl').read_text().splitlines()];self.assertEqual(len(rows),1);self.assertEqual(adapters.normalize('uart',rows[0])['position'],[21.012345,105.678901])
   finally:
    if proc.poll() is None:proc.kill();proc.communicate()
    os.close(master);os.close(slave)

 def test_engine_orchestrates_three_sources_with_current_wrapper(self):
  commands=[]
  class Process:
   def __init__(self,code=None):self.returncode=code
   def poll(self):return self.returncode
   def send_signal(self,sig):self.returncode=0
   def wait(self,timeout=None):self.returncode=0;return 0
  def launch(cmd,**kwargs):
   commands.append(cmd)
   if 'run_receiver.py' in cmd[1]:
    output=Path(cmd[cmd.index('-o')+1])
    Path(str(output)+'.events.jsonl').write_text(json.dumps(dict(record_type='receive',acquisition=dict(receive_start_monotonic_s=10,receive_end_monotonic_s=11)))+'\n')
    Path(str(output)+'.decoded.jsonl').write_text(json.dumps(dict(record_type='decode',completed_monotonic_s=11,result=dict(zc_ofdm='passed',crc_valid=True,decode_result=dict(payload=dict(serial_number='TEST',latitude=21,longitude=105)))))+'\n')
    return Process(0)
   output=Path(cmd[cmd.index('--output')+1]);output.mkdir();(output/'ready.json').write_text('{}')
   row=dict(monotonic_ns=10500000000,decoded=dict(serial_number='TEST',drone_lat=21,drone_lon=105)) if 'collect_e200.py' in cmd[1] else dict(monotonic_ns=10500000000,serial='TEST',crc_flag=1,latitude=21,longitude=105)
   (output/'messages.jsonl').write_text(json.dumps(row)+'\n');return Process()
  with tempfile.TemporaryDirectory() as d:
   e=engine.Engine(dict(uart_ports=['/dev/test-uart'],e200_listen_port=54321));e.path=Path(d);e.status='preparing'
   with patch.object(engine.subprocess,'Popen',side_effect=launch):e.run(30)
   state=e.snapshot();self.assertEqual(state['status'],'stopped',state['error']);self.assertEqual(len(state['events']),3);self.assertEqual(state['stages']['zc_pass'],1)
   self.assertTrue(commands[2][1].endswith('scripts/run_receiver.py'));self.assertIn('/dev/test-uart',commands[1]);self.assertIn('54321',commands[0]);self.assertTrue((e.path/'map_summary.json').exists())
