"""G0 acquisition/burst records with explicit unknowns and source-coordinate checks.

Based on the CAPTURE subagent draft, reviewed for required requested/measured fields.
"""
import math
import numbers
import uuid
from copy import deepcopy

STAGES=('detected','frequency_resample','zc_ofdm','qpsk','payload_bytes','raw_crc','payload_parse')
STATUSES={'passed','failed','not_run','unknown'}
REASONS={None,'stream_start','retune','rx_gap','overflow','restart','queue_drop','clock_reset','unknown'}
TIMINGS=('retune','settle','receive','process','write')
ACQUISITION_KEYS={'schema_version','record_type','session_id','acquisition_id','continuity_id','device','center_requested_hz','center_actual_hz','sample_rate_requested_sps','sample_rate_actual_sps','gain_requested_db','gain_actual_db','sample_count','first_sample_device_time_s','receive_start_monotonic_s','receive_end_monotonic_s','rx_errors','dropped_samples','discontinuity_reason','timing_seconds','first_sample_index_in_continuity','device_time_reference_id','continuity_tolerance_samples'}
BURST_KEYS={'schema_version','record_type','session_id','acquisition_id','burst_id','burst_range','slice_range','cfo_hz','output_file','output_offset_bytes','output_length_samples','iq_sha256','duplicate_of','stage_status','failure_reason'}

def _number(value,name,nullable=False,positive=False,nonnegative=False):
    if value is None and nullable:return
    if isinstance(value,bool) or not isinstance(value,numbers.Real) or not math.isfinite(value):raise ValueError(f'{name} must be finite')
    if positive and value<=0:raise ValueError(f'{name} must be positive')
    if nonnegative and value<0:raise ValueError(f'{name} must be nonnegative')

def _integer(value,name,nullable=False):
    if value is None and nullable:return
    if isinstance(value,bool) or not isinstance(value,numbers.Integral) or value<0:raise ValueError(f'{name} must be a nonnegative integer')

def _text(value,name,nullable=False):
    if value is None and nullable:return
    if not isinstance(value,str) or not value:raise ValueError(f'{name} must be a nonempty string')

def make_acquisition(**kwargs):
    required={'device','center_requested_hz','sample_rate_requested_sps','sample_count','receive_start_monotonic_s','receive_end_monotonic_s'}
    if required-set(kwargs):raise ValueError(f'Missing acquisition fields: {sorted(required-set(kwargs))}')
    defaults=dict(schema_version=1,record_type='acquisition',session_id=str(uuid.uuid4()),acquisition_id=str(uuid.uuid4()),continuity_id=None,center_actual_hz=None,sample_rate_actual_sps=None,gain_requested_db=None,gain_actual_db=None,first_sample_device_time_s=None,rx_errors=[],dropped_samples=None,discontinuity_reason='unknown',timing_seconds={key:None for key in TIMINGS},first_sample_index_in_continuity=None,device_time_reference_id=None,continuity_tolerance_samples=None)
    record=deepcopy(dict(defaults,**kwargs))
    if record['timing_seconds'].get('receive') is None:
        record['timing_seconds']['receive']=record['receive_end_monotonic_s']-record['receive_start_monotonic_s']
    validate_acquisition(record)
    return record

def validate_acquisition(record):
    if not isinstance(record,dict) or set(record)!=ACQUISITION_KEYS:raise ValueError('Acquisition keys do not match G0')
    if type(record['schema_version']) is not int or record['schema_version']!=1 or record['record_type']!='acquisition':raise ValueError('Invalid acquisition schema/type')
    for key in ['session_id','acquisition_id','device']:_text(record[key],key)
    for key in ['continuity_id','device_time_reference_id']:_text(record[key],key,nullable=True)
    for key in ['center_requested_hz','sample_rate_requested_sps']:_number(record[key],key,positive=True)
    for key in ['center_actual_hz','sample_rate_actual_sps']:_number(record[key],key,nullable=True,positive=True)
    for key in ['gain_requested_db','gain_actual_db','first_sample_device_time_s']:_number(record[key],key,nullable=True)
    for key in ['receive_start_monotonic_s','receive_end_monotonic_s']:_number(record[key],key)
    if record['receive_end_monotonic_s']<record['receive_start_monotonic_s']:raise ValueError('Receive clock moved backwards')
    for key in ['sample_count','dropped_samples','first_sample_index_in_continuity']:_integer(record[key],key,nullable=key!='sample_count')
    if not isinstance(record['rx_errors'],list):raise ValueError('rx_errors must be an array')
    for error in record['rx_errors']:
        if not isinstance(error,dict) or set(error)!={'code','message'}:raise ValueError('Invalid RX error record')
        _text(error['code'],'rx error code')
        if not isinstance(error['message'],str):raise ValueError('RX error message must be string')
    if record['discontinuity_reason'] not in REASONS:raise ValueError('Unknown discontinuity reason')
    timing=record['timing_seconds']
    if not isinstance(timing,dict) or set(timing)!=set(TIMINGS):raise ValueError('Invalid timing keys')
    for key,value in timing.items():_number(value,key,nullable=True,nonnegative=True)
    if timing['receive'] is not None and not math.isclose(timing['receive'],record['receive_end_monotonic_s']-record['receive_start_monotonic_s'],rel_tol=1e-9,abs_tol=1e-9):raise ValueError('Receive timing disagrees with timestamps')
    tolerance=record['continuity_tolerance_samples']
    _number(tolerance,'continuity tolerance',nullable=True,nonnegative=True)
    if tolerance is not None and tolerance>0.5:raise ValueError('Continuity tolerance exceeds half a sample')
    if record['continuity_id'] is not None and any(record[k] is None for k in ['center_actual_hz','sample_rate_actual_sps','first_sample_device_time_s','first_sample_index_in_continuity','device_time_reference_id','continuity_tolerance_samples']):raise ValueError('Claimed continuity requires measured clock/index/frequency/rate')

def validate_burst(record,acquisition):
    validate_acquisition(acquisition)
    if not isinstance(record,dict) or set(record)!=BURST_KEYS:raise ValueError('Burst keys do not match G0')
    if type(record['schema_version']) is not int or record['schema_version']!=1 or record['record_type']!='burst':raise ValueError('Invalid burst schema/type')
    for key in ['session_id','acquisition_id','burst_id','output_file']:_text(record[key],key)
    if record['session_id']!=acquisition['session_id'] or record['acquisition_id']!=acquisition['acquisition_id']:raise ValueError('Burst acquisition/session mismatch')
    for key in ['burst_range','slice_range']:
        interval=record[key]
        if not isinstance(interval,dict) or set(interval)!={'start_sample','end_sample'}:raise ValueError('Invalid interval keys')
        for bound,value in interval.items():_integer(value,bound)
    b,s=record['burst_range'],record['slice_range']
    if not 0<=s['start_sample']<=b['start_sample']<b['end_sample']<=s['end_sample']<=acquisition['sample_count']:raise ValueError('Invalid interval containment')
    for key in ['output_offset_bytes','output_length_samples']:_integer(record[key],key)
    if record['output_offset_bytes']%8 or record['output_length_samples']!=s['end_sample']-s['start_sample']:raise ValueError('IQ offset/length mismatch')
    _number(record['cfo_hz'],'CFO',nullable=True)
    _text(record['duplicate_of'],'duplicate_of',nullable=True)
    if record['duplicate_of']==record['burst_id']:raise ValueError('Self duplicate reference')
    if record['failure_reason'] is not None and not isinstance(record['failure_reason'],str):raise ValueError('Invalid failure reason')
    sha=record['iq_sha256']
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid IQ hash')
    statuses=record['stage_status']
    if not isinstance(statuses,dict) or set(statuses)!=set(STAGES) or any(v not in STATUSES for v in statuses.values()):raise ValueError('Invalid stage statuses')

def acquisitions_contiguous(previous,current):
    validate_acquisition(previous);validate_acquisition(current)
    if previous['continuity_id'] is None or current['continuity_id'] is None:return False
    if current['discontinuity_reason'] is not None:return False
    if previous['rx_errors'] or current['rx_errors'] or previous['dropped_samples']!=0 or current['dropped_samples']!=0:return False
    for key in ['session_id','continuity_id','device_time_reference_id','center_actual_hz','sample_rate_actual_sps']:
        if previous[key]!=current[key]:return False
    if current['first_sample_index_in_continuity']!=previous['first_sample_index_in_continuity']+previous['sample_count']:return False
    expected=previous['first_sample_device_time_s']+previous['sample_count']/previous['sample_rate_actual_sps']
    error_samples=abs(current['first_sample_device_time_s']-expected)*previous['sample_rate_actual_sps']
    return error_samples <= min(previous['continuity_tolerance_samples'],current['continuity_tolerance_samples'])
