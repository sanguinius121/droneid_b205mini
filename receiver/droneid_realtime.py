#!/usr/bin/env python3
"""Measured CPU/async/adaptive profile with realtime DSP+Turbo+CFO fallback."""
import argparse,signal,math,contextlib,sys
from pathlib import Path
from types import SimpleNamespace

def parser():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('-o','--output-file',required=True,help='Fresh IQ path; metadata and decoded JSONL written beside it')
 p.add_argument('--device',choices=['b205mini','x310'],default='b205mini')
 p.add_argument('--serial');p.add_argument('--addr',default='192.168.40.2');p.add_argument('--subdev')
 p.add_argument('-g','--gain',type=float,default=30)
 p.add_argument('-s','--sample-rate',type=float,default=50e6)
 p.add_argument('-t','--duration',type=float,default=2)
 p.add_argument('-f','--frequencies',nargs='*')
 p.add_argument('--max-seconds',type=float,default=180)
 p.add_argument('--max-candidates',type=int,default=100)
 p.add_argument('--queue-mib',type=float,default=1024)
 p.add_argument('--decode-queue-mib',type=float,default=32)
 p.add_argument('--no-cfo-fallback',action='store_true')
 p.add_argument('--terminal',choices=['auto','overwrite','json','log'],default='auto',help='Latest JSON on terminal; JSONL if redirected; log keeps counters')
 p.add_argument('--run-once',action='store_true')
 return p

def main():
 p=parser();opts=p.parse_args()
 for name in ("sample_rate","duration","max_seconds","queue_mib","decode_queue_mib"):
  value=getattr(opts,name)
  if not math.isfinite(value) or value<=0:p.error(name+" must be finite and positive")
 if opts.max_candidates<=0:p.error("max_candidates must be positive")
 if not math.isfinite(opts.gain):p.error("gain must be finite")
 if opts.queue_mib*1024**2<opts.sample_rate*opts.duration*8:p.error("Acquisition queue must fit one IQ block")
 from turbo_decoder import TurboDecoder
 TurboDecoder() # Fail before touching SDR when optional dependency is missing.
 import packetizer_concat_live_fixed as device
 from realtime_capture_runner import run_capture
 args=SimpleNamespace(**vars(opts),packet_type='droneid',legacy=False,debug=False,chunk_time=.25,
                      settle_time=.03,lock_on_hit=True,lock_rounds=4,revisit_interval=60,
                      raw_mib=0,detector_backend='cpu',async_processing=True,truncate_output=False)
 frequencies=device.parse_frequency_list(args.frequencies)
 output=Path(args.output_file).resolve()
 for suffix in ('','.records.jsonl','.events.jsonl','.summary.json','.decoded.jsonl'):
  if Path(str(output)+suffix).exists():raise FileExistsError('Use a fresh output path')
 stopped=False
 def stop(*unused):
  nonlocal stopped
  stopped=True
 signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
 with contextlib.redirect_stdout(sys.stderr):
  usrp=device.initialize_usrp(args)
  _,metadata,streamer,buffer=device.set_sdr(usrp,args.sample_rate,args.duration,args.gain)
 return run_capture(args,usrp,streamer,metadata,buffer,device.uhd,frequencies,lambda:stopped)
if __name__=='__main__':main()
