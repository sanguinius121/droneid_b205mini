"""Latest decoded JSON on a TTY, JSONL messages when stdout is redirected."""
from collections import deque
from datetime import datetime
import json,sys,threading,time
from zoneinfo import ZoneInfo
class DecodedDisplay:
    def __init__(self,mode='auto',stream=None,clock=time.monotonic):
        self.stream=stream or sys.stdout;self.clock=clock;self.started=clock()
        self.overwrite=mode=='overwrite' or (mode=='auto' and self.stream.isatty())
        self.enabled=mode!='log';self.lock=threading.Lock();self.timestamps=deque();self.total=0
        self.latest=None;self.candidates=0;self.status='running';self.done=threading.Event();self.thread=None
        if self.enabled and self.overwrite:
            self.thread=threading.Thread(target=self.refresh,name='droneid-display',daemon=True);self.thread.start()
    def rate(self,now):
        while self.timestamps and self.timestamps[0]<=now-5:self.timestamps.popleft()
        elapsed=max(0.,now-self.started)
        return dict(decoded_messages=self.total,decoded_messages_per_s=self.total/elapsed if elapsed else 0.,
                    decoded_messages_per_s_last5s=len(self.timestamps)/min(5.,elapsed) if elapsed else 0.,elapsed_s=elapsed)
    def capture(self,count):
        with self.lock:self.candidates=count
    def decoded(self,burst,result,latency_s):
        with self.lock:
            now=self.clock();message=None
            if result['crc_valid'] and result['payload_parsed']:
                self.total+=1;self.timestamps.append(now)
                value=result['decode_result']
                message=dict(type='decoded_message',time=datetime.now(ZoneInfo('Asia/Saigon')).isoformat(),
                             burst_id=burst['burst_id'],crc_valid=True,payload=value['payload'],
                             cfo_fallback_used=result['cfo_fallback_used'],latency_s=latency_s)
                self.latest=message
            rates=self.rate(now)
            if message is not None and self.enabled and not self.overwrite:
                self.stream.write(json.dumps(dict(message,**rates),ensure_ascii=False,allow_nan=False)+'\n');self.stream.flush()
            return rates
    def render(self):
        with self.lock:
            view=dict(status=self.status,candidates=self.candidates,**self.rate(self.clock()),latest_message=self.latest)
            self.stream.write('\033[2J\033[H'+json.dumps(view,ensure_ascii=False,indent=2,allow_nan=False)+'\n');self.stream.flush()
    def refresh(self):
        while not self.done.wait(1):self.render()
    def close(self):
        self.done.set()
        if self.thread:self.thread.join()
        self.status='stopped'
        if self.enabled and self.overwrite:self.render()
        with self.lock:return self.rate(self.clock())
