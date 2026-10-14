"""Single-source local viewer. Opening this server never starts RF capture."""
import argparse,json,subprocess,sys,threading,time,signal
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
ROOT=Path(__file__).resolve().parents[1]
class Receiver:
 def __init__(self):self.process=None;self.output=None;self.log=None;self.started=None;self.finished=None;self.lock=threading.Lock()
 def start(self,seconds,baseline=False):
  with self.lock:
   if self.process and self.process.poll() is None:raise ValueError('Receiver is already running')
   folder=ROOT/'sessions'/time.strftime('%Y%m%d_%H%M%S');folder.mkdir(parents=True,exist_ok=False)
   if self.log:self.log.close()
   self.started=time.monotonic();self.finished=None
   self.output=folder/'receiver.fc32';self.log=(folder/'receiver.log').open('w')
   command=[sys.executable,str(ROOT/'scripts/run_receiver.py'),'-o',str(self.output),'--max-seconds',str(seconds),'--max-candidates','100000','--terminal','log']
   if baseline:command.append('--baseline')
   (folder/'command.json').write_text(json.dumps(command,indent=2))
   self.process=subprocess.Popen(command,cwd=ROOT,stdout=self.log,stderr=subprocess.STDOUT)
 def stop(self):
  with self.lock:
   if self.process and self.process.poll() is None:self.process.send_signal(signal.SIGINT)
 def close(self):
  self.stop()
  if self.process:
   try:self.process.wait(timeout=40)
   except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=10)
  if self.log:self.log.close()
 def state(self):
  with self.lock:
   output=self.output;code=self.process.poll() if self.process else None
   running=self.process is not None and code is None
   if self.process and not running and self.finished is None:self.finished=time.monotonic()
   elapsed=(self.finished or time.monotonic())-self.started if self.started is not None else 0
  rows=[];summary={}
  if output:
   p=Path(str(output)+'.decoded.jsonl')
   if p.exists():
    for line in p.read_text().splitlines():
     try:
      r=json.loads(line)
      if r['result'].get('crc_valid'):rows.append(r)
     except (ValueError,KeyError):continue
   p=Path(str(output)+'.summary.json')
   if p.exists():
    try:summary=json.loads(p.read_text())
    except ValueError:pass
  return dict(status='running' if running else 'idle' if output is None else 'stopped' if code==0 else 'error',exit_code=code,output=str(output) if output else None,decoded_messages=len(rows),elapsed_s=elapsed,decoded_messages_per_s=len(rows)/elapsed if elapsed>0 else 0,messages=rows,summary=summary)
receiver=Receiver()
@asynccontextmanager
async def lifespan(app):
 yield
 receiver.close()
app=FastAPI(lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'web/static'),name='static')
@app.get('/')
def index():return FileResponse(ROOT/'web/static/index.html')
@app.get('/api/state')
def state():return receiver.state()
def check_origin(request):
 origin=request.headers.get('origin')
 if origin and origin!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Origin mismatch')
@app.post('/api/start')
async def start(request:Request):
 check_origin(request);body=await request.json();seconds=body.get('seconds',180)
 if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not 30<=seconds<=3600:raise HTTPException(400,'seconds must be 30–3600')
 try:receiver.start(seconds,body.get('baseline') is True)
 except (ValueError,FileExistsError) as e:raise HTTPException(409,str(e))
 return receiver.state()
@app.post('/api/stop')
def stop(request:Request):check_origin(request);receiver.stop();return receiver.state()
if __name__=='__main__':
 import uvicorn
 p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8088);args=p.parse_args()
 uvicorn.run(app,host=args.host,port=args.port)
