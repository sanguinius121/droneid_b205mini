"""Independent decoder process behind a byte-bounded burst queue."""
import concurrent.futures
import json
import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path
from live_acquisition import BoundedAcquisitionPipeline

def child_init():
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    # Legacy DSP prints are isolated from RX stdout; JSONL retains errors/traces.
    null=os.open(os.devnull,os.O_WRONLY)
    os.dup2(null,1);os.dup2(null,2);os.close(null)
    from turbo_decoder import TurboDecoder
    TurboDecoder()

def child_decode(samples,rate,cfo_fallback,center_first=False):
    from realtime_decoder import decode_burst,decode_centered_burst
    return (decode_centered_burst if center_first else decode_burst)(samples,rate,cfo_fallback)

class RealtimeDecodeWorker:
    def __init__(self,path,queue_mib=32,cfo_fallback=True,terminal="log",center_first=False):
        if not 0<queue_mib< float('inf'):raise ValueError('Positive finite decoder queue budget required')
        from realtime_display import DecodedDisplay
        self.display=DecodedDisplay(terminal)
        self.path=Path(path);self.file=self.path.open('x');self.lock=threading.Lock()
        self.executor=concurrent.futures.ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'),initializer=child_init)
        self.cfo_fallback=cfo_fallback;self.center_first=center_first
        self.counts=dict(submitted=0,resample_pass=0,zc_pass=0,crc_valid=0,crc_parsed=0,cfo_rescued=0,decode_dropped=0,
                         psd_fallback_attempted=0,psd_fallback_rescued=0)
        self.pipeline=BoundedAcquisitionPipeline(int(queue_mib*1024**2),self.process,self.drop).start()
    def emit(self,event):
        with self.lock:self.file.write(json.dumps(event,allow_nan=False)+'\n');self.file.flush()
    def submit(self,samples,burst,rate):
        record=dict(acquisition_id=burst['burst_id'],sample_count=len(samples),burst=burst,sample_rate=rate,submitted_monotonic_s=time.monotonic())
        accepted=self.pipeline.submit(samples,record)
        if accepted:self.counts['submitted']+=1
        return accepted
    def drop(self,event):
        with self.lock:self.counts['decode_dropped']+=1
        self.emit(dict(record_type='decode_drop',burst_id=event['acquisition_id'],**event))
    def process(self,samples,record):
        result=self.executor.submit(child_decode,samples,record['sample_rate'],self.cfo_fallback,self.center_first).result()
        with self.lock:
            self.counts['resample_pass']+=result['frequency_resample']=='passed'
            self.counts['zc_pass']+=result['zc_ofdm']=='passed'
            self.counts['crc_valid']+=result['crc_valid']
            self.counts['crc_parsed']+=result['crc_valid'] and result['payload_parsed']
            self.counts['cfo_rescued']+=result['cfo_fallback_used']
            self.counts['psd_fallback_attempted']+=result.get('coarse_fallback_used',False)
            self.counts['psd_fallback_rescued']+=result.get('coarse_fallback_rescued',False)
            counts=dict(self.counts)
        latency=time.monotonic()-record['submitted_monotonic_s']
        rates=self.display.decoded(record['burst'],result,latency)
        self.emit(dict(record_type='decode',burst=record['burst'],sample_rate=record['sample_rate'],
                       completed_monotonic_s=time.monotonic(),latency_s=latency,result=result,message_rates=rates))
        if not self.display.enabled:print(f"[DECODE] processed={self.pipeline.stats['processed']+1} zc={counts['zc_pass']} crc={counts['crc_valid']} cfo_rescued={counts['cfo_rescued']} decode_drop={counts['decode_dropped']}",flush=True)
    def close(self):
        try:self.pipeline.close(drain=True)
        finally:self.executor.shutdown(wait=True);self.file.close();rates=self.display.close()
        return dict(self.counts,message_rates=rates,queue=self.pipeline.stats,identity='local burst/acquisition/body; no global packet deduplication')
