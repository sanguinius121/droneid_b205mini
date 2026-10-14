#!/usr/bin/env python3
"""Experimental centered 20-MS/s hybrid detector + renewed channel hold.

First hardware stage uses one fixed sample rate. Wide/narrow rate switching
needs hardware transition measurements before it can be enabled.
"""
import contextlib,signal,sys,math
from pathlib import Path
from types import SimpleNamespace
from droneid_realtime import parser

FREQUENCIES_MHZ=[2414.5,2429.5,2444.5,2459.5,5756.5,5776.5,5796.5,5816.5]


def main():
    p=parser();p.description=__doc__;p.set_defaults(sample_rate=20e6,duration=.7,frequencies=[str(f) for f in FREQUENCIES_MHZ])
    p.add_argument('--hit-timeout',type=float,default=1.3)
    p.add_argument('--center-first',action='store_true',help='Known channel center first; legacy PSD retry if CRC fails')
    p.add_argument('--predictor-shadow',action='store_true',help='Log device/host timing and read-only frequency predictions; never changes scheduler')
    p.add_argument('--rx-boundary',type=float,default=None,help='Experimental finite RX sub-block seconds; preserve nominal scan dwell, decoder unchanged')
    p.add_argument('--predictor-control',action='store_true',help='Experimental ZC/CP same-channel deadline RF control; requires shadow and short RX boundary')
    p.add_argument('--predictor-budget',default=None,help='Prior metrics JSON containing retune_pair_ms')
    opts=p.parse_args()
    for name in ('sample_rate','duration','max_seconds','queue_mib','decode_queue_mib','hit_timeout'):
        v=getattr(opts,name)
        if not math.isfinite(v) or v<=0:p.error(name+' must be positive and finite')
    if not 15.36e6<=opts.sample_rate<=25e6:p.error('Hybrid requires sample rate 15.36--25 MS/s')
    if opts.predictor_control and (not opts.predictor_shadow or opts.rx_boundary is None):p.error('Predictor control requires --predictor-shadow and --rx-boundary')
    if opts.rx_boundary is not None and (not math.isfinite(opts.rx_boundary) or not .02<=opts.rx_boundary<=opts.duration):p.error('RX boundary must be 0.02 s through nominal dwell')
    if opts.max_candidates<=0 or not math.isfinite(opts.gain):p.error('Invalid candidate limit or gain')
    if opts.queue_mib*1024**2<opts.sample_rate*opts.duration*8:p.error('Queue must fit one IQ block')
    from turbo_decoder import TurboDecoder
    TurboDecoder()
    import packetizer_concat_live_fixed as device
    from realtime_capture_runner import run_capture
    args=SimpleNamespace(**vars(opts),packet_type='droneid',legacy=False,debug=False,chunk_time=.25,
        settle_time=.03,lock_on_hit=True,lock_rounds=4,revisit_interval=60.,raw_mib=0.,
        detector_backend='hybrid',async_processing=True,truncate_output=False,hit_timeout_s=opts.hit_timeout)
    frequencies=device.parse_frequency_list(args.frequencies)
    for suffix in ('','.records.jsonl','.events.jsonl','.summary.json','.decoded.jsonl')+ (('.shadow.jsonl',) if opts.predictor_shadow else ()):
        if Path(str(args.output_file)+suffix).exists():raise FileExistsError('Fresh output required')
    stopped=False
    def stop(*unused):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    with contextlib.redirect_stdout(sys.stderr):
        usrp=device.initialize_usrp(args)
        args.analog_bandwidth_requested_hz=min(opts.sample_rate,20e6)
        usrp.set_rx_bandwidth(args.analog_bandwidth_requested_hz,0)
        _,metadata,streamer,buffer=device.set_sdr(usrp,args.sample_rate,args.duration,args.gain)
        args.analog_bandwidth_actual_hz=float(usrp.get_rx_bandwidth(0))
        print('Hybrid RF actual Fs:',usrp.get_rx_rate(0),'bandwidth:',args.analog_bandwidth_actual_hz,flush=True)
    return run_capture(args,usrp,streamer,metadata,buffer,device.uhd,frequencies,lambda:stopped)


if __name__=='__main__':main()
