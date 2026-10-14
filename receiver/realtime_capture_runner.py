"""Realtime capture plus independent bounded decoding. UHD is supplied by the live entry point."""
from functools import partial
import hashlib
import json
import math
import queue
import time
import threading
import uuid
from pathlib import Path

from burst_detector import BurstDetector
from burst_writer import BurstWriter
from frequency_scheduler import FrequencyScheduler
from live_acquisition import BoundedAcquisitionPipeline
from uhd_acquisition import receive_acquisition


def run_capture(args, usrp, streamer, metadata, buffer, uhd, frequencies, should_stop):
    if args.packet_type != 'droneid' or args.legacy or args.debug:
        raise ValueError('Instrumented runner supports non-legacy DroneID without GUI only')
    for name in ('sample_rate', 'duration', 'chunk_time', 'max_seconds', 'queue_mib', 'revisit_interval'):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be finite and positive')
    if not math.isfinite(args.raw_mib) or args.raw_mib < 0 or args.settle_time < 0 or not math.isfinite(args.settle_time):
        raise ValueError('Raw budget and settle time must be finite and nonnegative')
    fs = float(usrp.get_rx_rate(0))
    count = int(args.duration * fs)
    if count <= 0 or args.max_seconds <= 0 or args.max_candidates <= 0:
        raise ValueError('Positive acquisition duration and run limits required')
    budget = int(args.queue_mib * 1024**2)
    if args.async_processing and budget < count * 8:
        raise ValueError('Queue budget must fit at least one acquisition at actual Fs')
    padding = math.ceil(225e-6 * fs)
    backend = getattr(args,'detector_backend','legacy')
    if backend not in ('legacy','cpu','cuda','hybrid'):raise ValueError('Unknown detector backend')
    if getattr(args,'center_first',False) and backend!='hybrid':
        raise ValueError('Center-first requires a qualified single-channel hybrid detector')
    detector_function=None
    if backend=='hybrid':
        from hybrid_burst_detector import find_hybrid_candidates
        if not 15.36e6<=fs<=25e6:raise ValueError('Hybrid requires actual sample rate 15.36--25 MS/s')
        detector_function=find_hybrid_candidates
    elif backend!='legacy':
        from fast_burst_detector import find_burst_candidates_fast
        detector_function=partial(find_burst_candidates_fast,backend=backend)
        if backend=='cuda':
            # Warm up before stream start, failing explicitly if CUDA is unavailable.
            from fast_burst_detector import stft_envelope
            import numpy as np
            stft_envelope(np.zeros(100000,dtype=np.complex64),backend='cuda')
    overlap = math.ceil(665e-6 * fs) + padding
    if backend=='hybrid':overlap=math.ceil(.006*fs)
    detector = BurstDetector(int(args.chunk_time * fs), padding,overlap,
                             **({'detector':detector_function} if detector_function else {}))
    scheduler = FrequencyScheduler(frequencies, dwell_s=args.duration,
        lock_rounds=args.lock_rounds, lock_on_hit=args.lock_on_hit,
        revisit_interval_s=args.revisit_interval)
    if getattr(args,'hit_timeout_s',None) is not None:
        from hit_timeout_scheduler import HitTimeoutScheduler
        scheduler=HitTimeoutScheduler(frequencies,dwell_s=args.duration,timeout_s=args.hit_timeout_s)
    if getattr(args,'rx_boundary',None) is not None:
        if backend!='hybrid' or getattr(args,'hit_timeout_s',None) is None:
            raise ValueError('Short boundary requires hybrid with qualified-hit timeout')
        from short_boundary_scheduler import ShortBoundaryScheduler
        scheduler=ShortBoundaryScheduler(frequencies,dwell_s=args.duration,timeout_s=args.hit_timeout_s,boundary_s=args.rx_boundary)
    if getattr(args,'predictor_control',False):
        if not getattr(args,'predictor_shadow',False) or getattr(args,'rx_boundary',None) is None:
            raise ValueError('Prediction control requires shadow and short boundary')
        from predicted_frequency_scheduler import PredictedFrequencyScheduler
        budgets={}
        if getattr(args,'predictor_budget',None):
            prior=json.loads(Path(args.predictor_budget).read_text())
            budgets={(float(r['source_mhz'])*1e6,float(r['target_mhz'])*1e6):float(r['p95_ms'])/1000 for r in prior['retune_pair_ms']}
            if any(not math.isfinite(v) or not 0<=v<=1 for v in budgets.values()):raise ValueError('Invalid measured tune budget')
        scheduler=PredictedFrequencyScheduler(frequencies,dwell_s=args.duration,timeout_s=args.hit_timeout_s,boundary_s=args.rx_boundary,settle_s=args.settle_time,budgets=budgets)
    output = Path(args.output_file).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [output, Path(str(output)+'.records.jsonl'),
             Path(str(output)+'.events.jsonl'), Path(str(output)+'.summary.json'), Path(str(output)+'.decoded.jsonl')]
    if any(p.exists() for p in paths):
        raise FileExistsError('Use a fresh output filename; capture evidence is never overwritten')
    shadow_path=Path(str(output)+'.shadow.jsonl')
    if getattr(args,'predictor_shadow',False) and shadow_path.exists():
        raise FileExistsError('Fresh shadow evidence required')
    from realtime_decode_worker import RealtimeDecodeWorker
    decoder = RealtimeDecodeWorker(paths[4],args.decode_queue_mib,not args.no_cfo_fallback,
        terminal=getattr(args,"terminal","log"),center_first=getattr(args,'center_first',False))
    shadow=None
    shadow_lock=threading.RLock()
    last_tune=None
    if getattr(args,'predictor_shadow',False):
        from frequency_predictor_shadow import ShadowPredictor,clock_anchor
        shadow=ShadowPredictor(shadow_path)
    feedback = queue.Queue()
    session = str(uuid.uuid4())
    started = time.monotonic()
    hops = 0
    completed_visits=0
    retunes=0
    received_seconds = 0.
    error_blocks = 0
    raw_saved = 0
    failure = None
    with BurstWriter(output.parent, output.name, paths[1].name) as writer, paths[2].open('x') as events:
        def event(record):
            events.write(json.dumps(record, allow_nan=False)+'\n')
            events.flush()
        def process(samples, record):
            t = time.monotonic()
            candidates = detector.process_acquisition(samples, record) if not record['rx_errors'] else []
            record['timing_seconds']['process'] = time.monotonic()-t
            writer.write_acquisition(record)
            t = time.monotonic()
            saved = 0
            for candidate in candidates:
                if writer.candidate_saved >= args.max_candidates:
                    break
                burst = writer.write_burst(record, samples, **candidate)
                lo,hi=burst['slice_range']['start_sample'],burst['slice_range']['end_sample']
                if shadow and backend=='hybrid':
                    with shadow_lock:
                        qualified_at=time.monotonic()
                        shadow.observe_qualified_burst(burst,qualified_at)
                        if getattr(args,'predictor_control',False) and burst['burst_id'] in shadow.provisional:
                            feedback.put(dict(record_type='prediction_seed',frame=dict(shadow.provisional[burst['burst_id']]),qualified_at=qualified_at))
                        # Persist the actual detector availability for causal replay.
                        shadow.emit(dict(record_type='shadow_qualified_burst',burst=burst,available_host_s=qualified_at))
                decoder.submit(samples[lo:hi],burst,record['sample_rate_actual_sps'])
                saved += 1
            feedback.put(dict(acquisition_id=record['acquisition_id'],
                frequency_hz=record['center_requested_hz'], detected_count=saved,
                rx_ok=not record['rx_errors'], observed_at=record['receive_end_monotonic_s'],
                process_s=record['timing_seconds']['process'], write_s=time.monotonic()-t,
                candidates_seen=len(candidates), saved_total=writer.candidate_saved))
        pipeline = BoundedAcquisitionPipeline(budget, process, on_drop=lambda e: feedback.put(dict(drop=e)))
        if args.async_processing:
            pipeline.start()
        def drain_feedback():
            if shadow:
                with shadow_lock:shadow.poll_decode_file(paths[4])
            while True:
                try: item = feedback.get_nowait()
                except queue.Empty: break
                event(item)
                if item.get('record_type')=='prediction_seed':
                    scheduler.on_qualified_frame(item['frame'],now=time.monotonic())
                    continue
                if 'drop' in item:
                    continue
                scheduler.on_feedback(item['acquisition_id'],item['frequency_hz'],item['detected_count'],
                    rx_ok=item['rx_ok'],observed_at=item['observed_at'],now=time.monotonic())
                decoder.display.capture(item["saved_total"])
                if not decoder.display.enabled:print(f"[PROGRESS] candidates_saved={item['saved_total']} elapsed={time.monotonic()-started:.1f}s", flush=True)
        try:
            while not should_stop() and time.monotonic()-started < args.max_seconds and writer.candidate_saved < args.max_candidates:
                drain_feedback()
                action = scheduler.next_action(time.monotonic())
                t = time.monotonic()
                if action['retune']:
                    usrp.set_rx_freq(uhd.types.TuneRequest(action['frequency_hz']), 0)
                    retunes+=1
                tune_end=time.monotonic()
                tune_s = tune_end-t
                if getattr(args,'predictor_control',False) and action['retune']:
                    scheduler.observe_tune(action.get('origin_frequency_hz'),action['frequency_hz'],tune_s)
                if shadow and action['retune']:
                    last_tune=dict(host_start_s=t,host_end_s=tune_end,frequency_hz=action['frequency_hz'],meaning='software call bracket, not measured RF settle')
                t = time.monotonic()
                if action['retune']:
                    settle_end = time.monotonic() + args.settle_time
                    while not should_stop() and time.monotonic() < settle_end:
                        time.sleep(min(0.05, max(0., settle_end-time.monotonic())))
                settle_s = time.monotonic()-t
                anchor=clock_anchor(usrp) if shadow else None
                samples, record = receive_acquisition(streamer, metadata, buffer, (max(1,int(round(action['dwell_s']*fs))) if getattr(args,'rx_boundary',None) is not None else count), uhd,
                    args.device, action['frequency_hz'], float(usrp.get_rx_freq(0)),
                    args.sample_rate, fs, should_stop=should_stop)
                record['session_id'] = session
                record['gain_requested_db'] = args.gain if args.gain > 0 else None
                record['gain_actual_db'] = float(usrp.get_rx_gain(0))
                record['timing_seconds']['retune'] = tune_s
                record['timing_seconds']['settle'] = settle_s
                scheduler.register_acquisition(record['acquisition_id'],action['frequency_hz'],record['receive_start_monotonic_s'])
                hops += 1
                completed_visits+=action.get('boundary_visit_last',True)
                received_seconds += len(samples)/fs
                error_blocks += bool(record['rx_errors'])
                event(dict(record_type='receive',acquisition=record,action=action))
                if shadow:
                    with shadow_lock:shadow.observe_acquisition(record,anchor,last_tune)
                    event(dict(record_type='shadow_timing',acquisition_id=record['acquisition_id'],anchor=anchor,tune=last_tune))
                if raw_saved + samples.nbytes <= args.raw_mib * 1024**2 and len(samples):
                    raw_path = output.parent / (output.name+'.'+record['acquisition_id']+'.raw.fc32')
                    data = samples.astype('<c8',copy=False).tobytes()
                    with raw_path.open('xb') as raw: raw.write(data)
                    raw_saved += len(data)
                    event(dict(record_type='raw_block',acquisition_id=record['acquisition_id'],
                        file=raw_path.name,sha256=hashlib.sha256(data).hexdigest(),sample_count=len(samples)))
                    del data
                if len(samples):
                    if args.async_processing: pipeline.submit(samples,record)
                    else: process(samples,record)
                del samples
                if args.run_once and completed_visits >= len(frequencies): break
        except BaseException as exc:
            failure = repr(exc)
            raise
        finally:
            try:
                if args.async_processing: pipeline.close(drain=True)
            except BaseException as exc:
                if failure is None:
                    failure = repr(exc)
                    raise
                event(dict(record_type='shutdown_error',error=repr(exc)))
            finally:
                decoder_error=None
                try:decode_summary=decoder.close()
                except BaseException as exc:
                    decoder_error=exc;decode_summary=dict(error=repr(exc),counts=decoder.counts,queue=decoder.pipeline.stats)
                    if failure is None:failure=repr(exc)
                drain_feedback()
                scheduler_snapshot=scheduler.snapshot()
                scheduler.stop()
                elapsed = time.monotonic()-started
                summary = dict(session_id=session,elapsed_s=elapsed,hops=hops,frequency_retunes=retunes,completed_nominal_visits=completed_visits,
                    sample_rate_actual_sps=fs,received_iq_seconds=received_seconds,
                    received_iq_fraction=received_seconds/elapsed if elapsed else 0.,
                    rx_error_blocks=error_blocks,candidates_saved=writer.candidate_saved,
                    async_processing=args.async_processing,queue=pipeline.stats,
                    raw_bytes_saved=raw_saved,failure=failure,config=vars(args),
                    candidate_count_is_crc_verified=False,decoder=decode_summary,scheduler=scheduler_snapshot)
                if shadow:
                    with shadow_lock:
                        summary['predictor_shadow']=shadow.summarize()
                        if getattr(args,'predictor_control',False):
                            summary['predictor_shadow']['mode']='OBSERVATION_WITH_ACTIVE_ZC_CP_CONTROL'
                        shadow.close()
                paths[3].write_text(json.dumps(summary,indent=2)+'\n')
                if not decoder.display.enabled:print(json.dumps(summary,indent=2),flush=True)
                if decoder_error is not None:raise RuntimeError("Realtime decoder failed") from decoder_error
    return summary
