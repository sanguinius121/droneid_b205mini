"""Own bounded capture subprocesses, retain raw artifacts and compare one window."""
import collections,json,os,pathlib,signal,subprocess,sys,threading,time,uuid
from adapters import normalize
ROOT=pathlib.Path(__file__).resolve().parents[1];HERE=pathlib.Path(__file__).resolve().parent
class Tail:
    def __init__(self,path):self.path=path;self.offset=0;self.buffer=b''
    def read(self):
        if not self.path.exists():return []
        with self.path.open('rb') as f:f.seek(self.offset);data=f.read();self.offset=f.tell()
        self.buffer+=data;lines=self.buffer.split(b'\n');self.buffer=lines.pop()
        return [json.loads(line) for line in lines if line.strip()]
class Engine:
    def __init__(self,config=None):
        self.config=config or {};self.lock=threading.RLock();self.status='idle';self.rows=[];self.error=None;self.path=None;self.begin=None;self.end=None;self.proc={};self.stop_event=threading.Event();self.thread=None;self.stage=collections.Counter();self.replay=False;self.uart_order='lonlat'
    def start(self,seconds,order):
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('Phiên trước chưa dừng xong')
            self.rows=[];self.stage=collections.Counter();self.status='preparing';self.begin=self.end=None;self.error=None;self.proc={};self.replay=False;self.uart_order=order;self.stop_event.clear()
            stamp=time.strftime('%Y%m%d_%H%M%S',time.localtime());(ROOT/'sessions').mkdir(exist_ok=True);self.path=ROOT/'sessions'/('map_'+stamp+'_'+uuid.uuid4().hex[:6]);self.path.mkdir()
            (self.path/'map_config.json').write_text(json.dumps(dict(seconds=seconds,uart_gps_order=order,sources=['e200','uart','ours'],receiver='CPU async hybrid center-first predictor-control boundary 0.175 s 20 MS/s',connection_config=self.config,note='UART GPS coordinate order requires verification on first fix'),indent=2))
            self.thread=threading.Thread(target=self.run,args=(seconds,),daemon=True);self.thread.start()
    def stop(self):self.stop_event.set()
    def ingest(self,source,rows):
        with self.lock:
            for row in rows:
                if source=='ours':
                    self.stage['candidates']+=1;self.stage['zc_pass']+=row['result'].get('zc_ofdm')=='passed'
                event=normalize(source,row)
                if event:self.rows.append(event)
    def run(self,seconds):
        logs=[];acquisition_end=None;decode_end=None
        def launch(name,args):
            log=(self.path/(name+'.log')).open('x');logs.append(log)
            p=subprocess.Popen([sys.executable,*args],cwd=ROOT,env=dict(os.environ,OPENBLAS_NUM_THREADS='1'),stdout=log,stderr=subprocess.STDOUT);self.proc[name]=p;return p
        def ready(name):
            deadline=time.monotonic()+8
            while not (self.path/name/'ready.json').exists():
                if self.stop_event.is_set():raise InterruptedError('Dừng trước khi thu RF')
                if self.proc[name].poll() is not None or time.monotonic()>deadline:raise RuntimeError(name+' không khởi tạo được; xem log')
                time.sleep(.05)
        tails={s:Tail(self.path/p) for s,p in dict(e200='e200/messages.jsonl',uart='uart/messages.jsonl',ours='receiver.fc32.decoded.jsonl').items()};events=Tail(self.path/'receiver.fc32.events.jsonl')
        def harvest():
            nonlocal acquisition_end,decode_end
            for event in events.read():
                if event.get('record_type')=='receive':
                    a=event['acquisition']
                    with self.lock:
                        if self.begin is None:self.begin=a['receive_start_monotonic_s'];self.status='running'
                    acquisition_end=max(acquisition_end or 0,a['receive_end_monotonic_s'])
            for name,tail in tails.items():
                rows=tail.read()
                if name=='ours':
                    rows=[r for r in rows if r.get('record_type')=='decode']
                    if rows:decode_end=max(decode_end or 0,max(r['completed_monotonic_s'] for r in rows))
                self.ingest(name,rows)
        try:
            launch('e200',[str(HERE/'collect_e200.py'),'--output',str(self.path/'e200'),'--seconds',str(seconds+180),'--listen-ip',self.config.get('e200_listen_ip','172.31.100.1'),'--listen-port',str(self.config.get('e200_listen_port',52002)),'--peer-ip',self.config.get('e200_peer_ip','172.31.100.2')]);ready('e200')
            launch('uart',[str(HERE/'collect_uart.py'),'--output',str(self.path/'uart'),'--seconds',str(seconds+180),'--gps-order',self.uart_order,'--baudrate',str(self.config.get('uart_baudrate',115200)),'--ports',*self.config.get('uart_ports',['/dev/ttyUSB0','/dev/ttyUSB1'])]);ready('uart')
            rx=launch('ours',[str(ROOT/'scripts/run_receiver.py'),'-o',str(self.path/'receiver.fc32'),'--max-seconds',str(seconds),'--max-candidates','100000','--terminal','log'])
            deadline=time.monotonic()+seconds+60;termination=None
            while rx.poll() is None:
                harvest()
                if any(self.proc[s].poll() is not None for s in ('e200','uart')):raise RuntimeError('Collector tham chiếu dừng sớm')
                if self.stop_event.is_set() and termination is None:
                    with self.lock:self.status='stopping'
                    rx.send_signal(signal.SIGTERM);termination=time.monotonic()
                if time.monotonic()>deadline or (termination and time.monotonic()-termination>30):raise TimeoutError('SDR không dừng trong thời gian giới hạn')
                time.sleep(.2)
            harvest()
            if rx.returncode:raise RuntimeError('SDR exit '+str(rx.returncode)+'; xem ours.log')
        except InterruptedError:pass
        except BaseException as exc:
            with self.lock:self.error=str(exc)
        finally:
            for p in self.proc.values():
                if p.poll() is None:
                    p.send_signal(signal.SIGTERM)
                    try:p.wait(timeout=10)
                    except subprocess.TimeoutExpired:p.kill();p.wait()
            try:harvest()
            except Exception as exc:
                with self.lock:self.error=self.error or str(exc)
            with self.lock:
                self.end=max(acquisition_end or 0,decode_end or 0) or None
                self.status='error' if self.error else 'stopped'
            (self.path/'processes.json').write_text(json.dumps({n:p.returncode for n,p in self.proc.items()},indent=2))
            for log in logs:log.close()
            self.save()
    def save(self):
        if self.path:
            (self.path/'normalized.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
            (self.path/'map_summary.json').write_text(json.dumps(self.snapshot(),indent=2))
    def snapshot(self):
        with self.lock:
            end=self.end or time.monotonic();elapsed=max(0,end-self.begin) if self.begin is not None else 0
            filtered=[r for r in self.rows if self.begin is not None and self.begin<=r['t']<=end]
            return dict(status=self.status,error=self.error,session=self.path.name if self.path else None,path=str(self.path) if self.path else None,elapsed_s=elapsed,start_monotonic_s=self.begin,end_monotonic_s=self.end,uart_gps_order=self.uart_order,replay=self.replay,stages=dict(self.stage),events=filtered)
    def load_replay(self,path):
        # Replay artifacts retain original coordinates. No synthetic flight points.
        self.path=path;self.replay=True;self.status='replay';self.rows=[]
        ev=[json.loads(l) for l in (path/'receiver.fc32.events.jsonl').read_text().splitlines()];ac=[e['acquisition'] for e in ev if e.get('record_type')=='receive'];dec=[json.loads(l) for l in (path/'receiver.fc32.decoded.jsonl').read_text().splitlines()];dec=[d for d in dec if d.get('record_type')=='decode']
        self.begin=min(a['receive_start_monotonic_s'] for a in ac);self.end=max([a['receive_end_monotonic_s'] for a in ac]+[d['completed_monotonic_s'] for d in dec])
        for name,p in [('e200','e200/messages.jsonl'),('uart','uart/messages.jsonl')]:self.ingest(name,[json.loads(l) for l in (path/p).read_text().splitlines()])
        self.ingest('ours',dec)
