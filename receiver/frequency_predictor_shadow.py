"""Read-only, causal frequency hypotheses; never issues a scheduler action.

Qualified ZC/CP bursts seed provisional hypotheses before CRC; CRC-only
hypotheses remain as a latency control. Device sample times locate frames. Host/device anchors only estimate availability;
old captures without anchors cannot establish whether a prediction was actionable.
"""
import argparse,collections,json,math,time
from pathlib import Path

PERIODS=(.5,.55,.58,.6,.64)
WINDOW=.05

def clock_anchor(usrp):
    import time
    before=time.monotonic()
    try:
        device=float(usrp.get_time_now().get_real_secs());after=time.monotonic()
        if not math.isfinite(device):raise ValueError('Nonfinite device time')
        return dict(host_before_s=before,host_after_s=after,device_time_s=device,
                    uncertainty_s=(after-before)/2,method='get_time_now_host_bracket_estimate')
    except Exception as exc:
        return dict(host_before_s=before,host_after_s=time.monotonic(),device_time_s=None,error=str(exc))

class ShadowPredictor:
    def __init__(self,output=None):
        self.output=Path(output) if output else None
        self.file=self.output.open('x') if self.output else None
        self.acquisitions={};self.predictions=[];self.frames=[];self.seen=set();self.rejected=collections.Counter();self.transitions=collections.Counter();self.last_by_serial={};self.timing={};self.clock_epoch=0;self.last_device=None;self.current_session=None;self.decode_offset=0;self.decode_buffer=b'';self.provisional={};self.confirmations={}
    def emit(self,row):
        if self.file:self.file.write(json.dumps(row,allow_nan=False)+'\n');self.file.flush()
    def observe_acquisition(self,a,anchor=None,tune=None):
        first=a.get('first_sample_device_time_s');session=a['session_id']
        if session!=self.current_session:
            self.clock_epoch+=1;self.current_session=session;self.last_device=None;self.last_by_serial.clear();self.transitions.clear()
        if first is not None and self.last_device is not None and first<self.last_device:
            self.clock_epoch+=1;self.last_by_serial.clear();self.transitions.clear()
        if first is not None:self.last_device=first
        row=dict(a=a,anchor=anchor,tune=tune,domain=(session,self.clock_epoch))
        self.acquisitions[a['acquisition_id']]=row
    def observe_qualified_burst(self,burst,available_host_s):
        """Seed at the detector ZC/CP gate, without inventing serial or CRC."""
        bid=burst['burst_id']
        if bid in self.provisional:return
        entry=self.acquisitions.get(burst['acquisition_id'])
        if entry is None:self.rejected['provisional_missing_acquisition']+=1;return
        a=entry['a'];first=a.get('first_sample_device_time_s');fs=a.get('sample_rate_actual_sps');body=burst['burst_range']
        if a['rx_errors'] or first is None or not fs or not 0<=body['start_sample']<body['end_sample']<=a['sample_count']:
            self.rejected['provisional_invalid_timing']+=1;return
        f=dict(record_type='shadow_provisional_frame',burst_id=bid,acquisition_id=a['acquisition_id'],domain=entry['domain'],serial=None,frequency_hz=a['center_requested_hz'],device_start_s=first+body['start_sample']/fs,device_end_s=first+body['end_sample']/fs,shadow_observed_host_s=available_host_s,availability_device_interval_s=None,host_start_interval_s=None,confidence='detector_zc_cp',crc_status='pending')
        anchor=entry['anchor']
        if anchor and anchor.get('device_time_s') is not None:
            lo=anchor['host_before_s']-anchor['device_time_s'];hi=anchor['host_after_s']-anchor['device_time_s']
            f['host_start_interval_s']=[f['device_start_s']+lo,f['device_start_s']+hi];f['availability_device_interval_s']=[available_host_s-hi,available_host_s-lo] if available_host_s is not None else None
        self.provisional[bid]=f;self.emit(f)
        for period in PERIODS:
            for horizon in (1,2):self.predict(f,'provisional_same_channel_burst_clock',f['frequency_hz'],f['device_start_s']+period*horizon,period=period,horizon=horizon,source_confidence='detector_zc_cp',identity_at_creation='unknown')

    def observe_decode(self,d,available_host_s=None):
        if d.get('record_type')!='decode':return
        available_host_s=d['completed_monotonic_s'] if available_host_s is None else available_host_s
        bid=d['burst']['burst_id']
        if bid in self.seen:return
        self.seen.add(bid)
        if bid in self.provisional:
            valid=bool(d['result'].get('crc_valid'))
            payload=(d['result'].get('decode_result') or {}).get('payload') or {}
            confirmation=dict(record_type='shadow_confirmation',source_burst_id=bid,crc_status='passed' if valid else 'failed',serial=payload.get('serial_number') if valid else None,confirmed_host_s=d['completed_monotonic_s'],observer_host_s=available_host_s,zc_cp_confirmation_is_not_revoked_by_crc_failure=True)
            self.confirmations[bid]=confirmation;self.emit(confirmation)
        if not d['result'].get('crc_valid') or not d['result'].get('payload_parsed'):
            self.rejected['not_crc_and_parsed']+=1;return
        entry=self.acquisitions.get(d['burst']['acquisition_id'])
        if entry is None:self.rejected['missing_acquisition']+=1;return
        a=entry['a'];first=a.get('first_sample_device_time_s');fs=a.get('sample_rate_actual_sps')
        if a['rx_errors'] or first is None or fs is None:
            self.rejected['rx_error_or_missing_device_time']+=1;return
        body=d['burst']['burst_range'];start=body['start_sample'];end=body['end_sample']
        if not 0<=start<end<=a['sample_count']:
            self.rejected['invalid_sample_range']+=1;return
        payload=d['result']['decode_result']['payload'];serial=payload['serial_number']
        available_host_s=d['completed_monotonic_s'] if available_host_s is None else available_host_s
        frame=dict(record_type='shadow_frame',burst_id=bid,acquisition_id=a['acquisition_id'],domain=entry['domain'],serial=serial,sequence=payload.get('sequence_number'),frequency_hz=a['center_requested_hz'],device_start_s=first+start/fs,device_end_s=first+end/fs,decode_completed_host_s=d['completed_monotonic_s'],shadow_observed_host_s=available_host_s,host_start_interval_s=None,availability_device_interval_s=None,tune_to_frame_device_delta_s=None)
        anchor=entry['anchor']
        if anchor and anchor.get('device_time_s') is not None:
            device=anchor['device_time_s'];lo=anchor['host_before_s']-device;hi=anchor['host_after_s']-device
            frame['host_start_interval_s']=[frame['device_start_s']+lo,frame['device_start_s']+hi]
            frame['availability_device_interval_s']=[available_host_s-hi,available_host_s-lo]
        self.frames.append(frame)
        # Predict before training from this observation; no future information.
        for period in PERIODS:
            for horizon in (1,2):self.predict(frame,'same_channel_burst_clock',frame['frequency_hz'],frame['device_start_s']+period*horizon,period=period,horizon=horizon)
        # Historical tune hypotheses require a measured tune/device mapping.
        tune=entry['tune']
        if tune and anchor and anchor.get('device_time_s') is not None:
            tune_mid=(tune['host_start_s']+tune['host_end_s'])/2
            anchor_mid=(anchor['host_before_s']+anchor['host_after_s'])/2
            tune_device=tune_mid-anchor_mid+anchor['device_time_s']
            frame['tune_to_frame_device_delta_s']=frame['device_start_s']-tune_device
            for delta in (.58,1.21):self.predict(frame,'same_channel_tune_clock_diagnostic',frame['frequency_hz'],tune_device+delta,tune_reference_estimated=True)
        else:self.rejected['tune_hypothesis_unavailable']+=1
        choices=[(count,target) for (domain,who,origin,target),count in self.transitions.items() if domain==frame['domain'] and who==serial and origin==frame['frequency_hz'] and origin!=target and count>=3]
        if choices:
            count,target=max(choices)
            self.predict(frame,'observed_transition_heuristic',target,frame['device_start_s']+.58,training_observations=count,selection_bias='only observed CRC frames, not true RF hop sequence')
        key=(frame['domain'],serial)
        previous=self.last_by_serial.get(key)
        if previous and previous['domain']==frame['domain']:
            dt=frame['device_start_s']-previous['device_start_s']
            if 0<dt<=1.5:self.transitions[(frame['domain'],serial,previous['frequency_hz'],frame['frequency_hz'])]+=1
        if previous is None or frame['device_start_s']>previous['device_start_s']:self.last_by_serial[key]=frame
        self.emit(frame)
    def predict(self,frame,kind,frequency,target,**extra):
        window=[target-WINDOW,target+WINDOW];available=frame['availability_device_interval_s']
        availability='unknown_without_availability_timestamp' if frame['shadow_observed_host_s'] is None else 'unknown_without_anchor'
        if available:
            availability='late' if available[0]>window[0] else 'available_before_window' if available[1]<=window[0] else 'uncertain_at_boundary'
        p=dict(record_type='shadow_prediction',prediction_id=len(self.predictions),source_burst_id=frame['burst_id'],domain=frame['domain'],serial=frame['serial'],kind=kind,frequency_hz=frequency,target_device_s=target,window_device_s=window,availability=availability,created_host_s=frame['shadow_observed_host_s'],**extra)
        self.predictions.append(p);self.emit(p)
    def poll_decode_file(self,path):
        with Path(path).open('rb') as f:
            f.seek(self.decode_offset);data=f.read();self.decode_offset=f.tell()
        self.decode_buffer+=data;lines=self.decode_buffer.split(b'\n');self.decode_buffer=lines.pop()
        for line in lines:
            if line.strip():self.observe_decode(json.loads(line),available_host_s=time.monotonic())

    def summarize(self):
        groups={}
        for p in self.predictions:
            lo,hi=p['window_device_s'];intervals=[]
            for entry in self.acquisitions.values():
                a=entry['a'];t=a.get('first_sample_device_time_s');fs=a.get('sample_rate_actual_sps')
                if entry['domain']!=p['domain'] or a['rx_errors'] or t is None or not fs or abs(a['center_requested_hz']-p['frequency_hz'])>5000:continue
                right=t+a['sample_count']/fs
                if t<hi and right>lo:intervals.append((max(t,lo),min(right,hi)))
            covered=0.;edge=lo
            for a,b in sorted(intervals):covered+=max(0,b-max(a,edge));edge=max(edge,b)
            observed=any(f['domain']==p['domain'] and (p['serial'] is None or f['serial']==p['serial']) and f['burst_id']!=p['source_burst_id'] and abs(f['frequency_hz']-p['frequency_hz'])<=5000 and lo<=f['device_start_s']<=hi for f in self.frames)
            coverage='fully_observed' if covered>=hi-lo-1e-7 else 'partly_observed' if covered>0 else 'unobserved'
            key=p['kind']+(':'+str(p['period'])+':h'+str(p['horizon']) if 'period' in p else '')
            g=groups.setdefault(key,collections.Counter());g['predictions']+=1;g[coverage]+=1;g['observed_crc_match']+=observed;g['fully_observed_crc_match']+=coverage=='fully_observed' and observed;g['fully_observed_no_crc_match']+=coverage=='fully_observed' and not observed;g['available_observed_crc_match']+=observed and p['availability']=='available_before_window';g[p['availability']]+=1
        result=dict(mode='SHADOW_ONLY_NO_RF_CONTROL',provisional_frames=len(self.provisional),provisional_confirmations=dict(collections.Counter(c['crc_status'] for c in self.confirmations.values())),provisional_pending=len(self.provisional)-len(self.confirmations),crc_frames=len(self.frames),acquisitions=len(self.acquisitions),rejected=dict(self.rejected),groups={k:dict(v) for k,v in groups.items()},limitations=['Provisional predictions have unknown serial at creation; CRC matching for them is channel-level, not per-drone identity.','No CRC match does not prove no RF burst.','Coverage excludes error acquisitions.','Old captures without anchors cannot establish causal availability.','Multiple period hypotheses are separate experiments, not additive successful predictions.','Transition heuristic is biased by the scan schedule.','No E200 index/offset rule is mapped to the eight-channel receiver.'])
        return result
    def close(self):
        if self.file:self.file.close();self.file=None

def replay(capture,output,seed_qualified=False):
    events=[json.loads(l) for l in Path(str(capture)+'.events.jsonl').read_text().splitlines()]
    anchors={e['acquisition_id']:e.get('anchor') for e in events if e.get('record_type')=='shadow_timing'}
    tunes={e['acquisition_id']:e.get('tune') for e in events if e.get('record_type')=='shadow_timing'}
    decoded=[json.loads(l) for l in Path(str(capture)+'.decoded.jsonl').read_text().splitlines()]
    qualified=[]
    if seed_qualified:
        summary=json.loads(Path(str(capture)+'.summary.json').read_text())
        if summary['config'].get('detector_backend')!='hybrid':raise ValueError('Only qualified hybrid captures may seed provisional replay')
        source=Path(str(capture)+'.shadow.jsonl')
        if source.exists():qualified=[e for e in map(json.loads,source.read_text().splitlines()) if e.get('record_type')=='shadow_qualified_burst']
        if not qualified:
            # Old captures did not log detector availability: never manufacture it.
            qualified=[dict(burst=d['burst'],available_host_s=None) for d in decoded if d.get('record_type')=='decode']
    probe=ShadowPredictor(output)
    # Acquisition entries are available before their decode; observation training
    # follows decode completion order, not retrospective RF chronological order.
    timeline=[(e['acquisition']['receive_end_monotonic_s'],0,e['acquisition']) for e in events if e.get('record_type')=='receive']+[(d['completed_monotonic_s'],1,d) for d in decoded if d.get('record_type')=='decode']
    ends={e['acquisition']['acquisition_id']:e['acquisition']['receive_end_monotonic_s'] for e in events if e.get('record_type')=='receive'}
    timeline=[(t,0 if k==0 else 2,row) for t,k,row in timeline]
    timeline += [(q['available_host_s'] if q['available_host_s'] is not None else ends[q['burst']['acquisition_id']],1,q) for q in qualified]
    for _,kind,row in sorted(timeline,key=lambda x:(x[0],x[1])):
        if kind==0:probe.observe_acquisition(row,anchors.get(row['acquisition_id']),tunes.get(row['acquisition_id']))
        elif kind==1:probe.observe_qualified_burst(row['burst'],row['available_host_s'])
        else:probe.observe_decode(row)
    result=probe.summarize();probe.close();Path(str(output)+'.summary.json').write_text(json.dumps(result,indent=2));return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--provisional',action='store_true',help='Replay ZC/CP seeds; missing historical detector timestamps remain unknown');args=parser.parse_args();print(json.dumps(replay(args.capture,args.output,args.provisional),indent=2))
