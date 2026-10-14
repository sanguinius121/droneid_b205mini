"""Baseline-first offline decoder with explicit DSP and optional Turbo fallback."""
import time
from decode_frame import decode_symbols
from dsp_variants import symbols_for_mode
from qpsk_soft import SoftTurboQPSKDecoder


def decode_packet(packet,dsp_mode='baseline',use_turbo=False):
    if dsp_mode not in ('baseline','fallback_zc'):raise ValueError('Unknown DSP policy')
    paths=['baseline']+(['integer_cfo_zc'] if dsp_mode=='fallback_zc' else [])
    traces=[];symbols_cache={};first_result=None
    # Validate requested dependency even when baseline happens to pass CRC.
    backend=None
    if use_turbo:
        from turbo_decoder import TurboDecoder
        backend=TurboDecoder()
    for decoder in ['hard']+(['turbo'] if use_turbo else []):
        for path in paths:
            t=time.monotonic()
            try:
                if path not in symbols_cache:
                    symbols_cache[path]=packet.get_symbol_data(skip_zc=True) if path=='baseline' else symbols_for_mode(packet,path)
                turbo_attempts=[]
                def factory(symbols):
                    adapter=SoftTurboQPSKDecoder(symbols,turbo=backend)
                    adapter.turbo_attempts=turbo_attempts
                    return adapter
                result=decode_symbols(symbols_cache[path],decoder_factory=factory if decoder=='turbo' else None)
                if first_result is None:first_result=result
                trace=dict(path=path,decoder=decoder,crc_valid=result['crc_valid'],
                    raw_crc_status=result['raw_crc_status'],payload_parse_status=result['payload_parse_status'],
                    attempts=result['attempts'],turbo_attempts=turbo_attempts,runtime_s=time.monotonic()-t)
                if path!='baseline':trace['integer_cfo_bins']=getattr(packet,'integer_cfo_bins',None)
                traces.append(trace)
                if result['crc_valid']:
                    return dict(result,decode_path=path,decode_backend=decoder,decode_traces=traces)
            except Exception as exc:
                traces.append(dict(path=path,decoder=decoder,error=str(exc),crc_valid=False,runtime_s=time.monotonic()-t))
    if first_result is None:raise ValueError('No symbol path could be decoded: '+str(traces))
    return dict(first_result,decode_path=None,decode_backend=None,decode_traces=traces)
