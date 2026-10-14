"""Baseline-first DSP/Turbo decoder with bounded, pilot-selected CFO fallback."""
import time
import numpy as np
from SpectrumCapture import SpectrumCapture
from dsp_variants import MeasuredPacket
from decode_packet import decode_packet
from helpers import NCARRIERS,NFFT,ZC_SYMBOL_IDX,corr
from zcsequence import zcsequence_t

class CFOPacket(MeasuredPacket):
    def find_fine_start(self,samples):
        return self.fixed_start,self.fixed_ffo

def _decode_at_offset(samples,sample_rate,cfo_fallback=True,coarse_offset_hz=None):
    started=time.monotonic()
    result=dict(frequency_resample='not_run',zc_ofdm='not_run',crc_valid=False,
                payload_parsed=False,cfo_fallback_used=False,cfo_attempts=[])
    try:
        capture=SpectrumCapture(np.asarray(samples),Fs=sample_rate,skip_detection=True)
        data=capture.get_packet_samples(0) if coarse_offset_hz is None else capture.get_packet_samples(0,coarse_offset_hz=coarse_offset_hz)
        if isinstance(data,Exception):raise data
        result['frequency_resample']='passed'
        packet=MeasuredPacket(data.copy(),enable_zc_detection=True,debug=False)
        result['zc_ofdm']='passed'
        baseline=decode_packet(packet,dsp_mode='fallback_zc',use_turbo=True)
        result['baseline_result']=baseline
        result['decode_result']=baseline
        if baseline['crc_valid']:
            result.update(crc_valid=True,payload_parsed=baseline['payload_parsed'])
            return result
        if not cfo_fallback:return result
        reference=zcsequence_t(147,NCARRIERS);hypotheses=[]
        for bins in [-2,-1,0,1,2]:
            ffo=packet.detected_ffo+bins*packet.Fs/NFFT
            _,symbols=packet.raw_data_to_symbols(packet.raw_samples_orig.copy(),packet.start,ffo=ffo)
            pilot=symbols[ZC_SYMBOL_IDX[1]]
            denominator=float(np.linalg.norm(pilot)*np.linalg.norm(reference))
            score=float(np.max(np.abs(corr(pilot,reference)))/denominator) if denominator else 0.
            hypotheses.append(dict(bins=bins,ffo_hz=float(ffo),pilot_score=score))
        hypotheses.sort(key=lambda h:(-h['pilot_score'],abs(h['bins'])))
        result['cfo_selected']=[dict(h) for h in hypotheses[:3]]
        for h in hypotheses[:3]:
            if h['bins']==0:continue # Identical baseline already failed CRC.
            attempt=dict(h,zc_ofdm='not_run',crc_valid=False)
            result['cfo_attempts'].append(attempt)
            try:
                alternate=CFOPacket.__new__(CFOPacket)
                alternate.fixed_start=packet.start;alternate.fixed_ffo=h['ffo_hz']
                CFOPacket.__init__(alternate,data.copy(),enable_zc_detection=True,debug=False)
                attempt['zc_ofdm']='passed'
                decoded=decode_packet(alternate,dsp_mode='fallback_zc',use_turbo=True)
                attempt['decode_result']=decoded;attempt['crc_valid']=decoded['crc_valid']
                if decoded['crc_valid']:
                    result.update(decode_result=decoded,crc_valid=True,payload_parsed=decoded['payload_parsed'],
                                  cfo_fallback_used=True,cfo_bins=h['bins'])
                    return result
            except Exception as exc:attempt['error']=f'{type(exc).__name__}: {exc}'
        return result
    except Exception as exc:
        stage='frequency_resample' if result['frequency_resample']!='passed' else 'zc_ofdm'
        if result[stage]!='passed':result[stage]='failed'
        result['error']=f'{type(exc).__name__}: {exc}'
        return result
    finally:result['runtime_s']=time.monotonic()-started


def decode_burst(samples,sample_rate,cfo_fallback=True,psd_split=False):
    """Opt-in half-mean admission, legacy CFO first, at most one new-center retry."""
    if not psd_split:
        return _decode_at_offset(samples,sample_rate,cfo_fallback)
    from psd_band_analysis import analyze_bands
    started=time.monotonic()
    empty=dict(frequency_resample='not_run',zc_ofdm='not_run',crc_valid=False,
               payload_parsed=False,cfo_fallback_used=False,cfo_attempts=[],
               coarse_retry_used=False,coarse_attempts=[])
    try:
        analysis=analyze_bands(samples,sample_rate)
    except ValueError as exc:
        empty.update(frequency_resample='failed',error=f'ValueError: {exc}',runtime_s=time.monotonic()-started)
        return empty
    empty['psd_analysis']=analysis
    if not analysis['admitted']:
        empty.update(frequency_resample='failed',error='Half-mean bandwidth admission rejected',
                     runtime_s=time.monotonic()-started)
        return empty
    old,new=analysis['old_offset_hz'],analysis['new_offset_hz']
    hypotheses=[('old_band',old)] if old is not None else []
    if old is None or new != old:
        hypotheses.append(('new_band',new))
    attempts=[]
    selected=None
    for source,offset in hypotheses:
        attempt=_decode_at_offset(samples,sample_rate,cfo_fallback,offset)
        attempts.append(dict(source=source,offset_hz=offset,result=attempt))
        # Preserve the first attempt's stage information if all hypotheses fail.
        if selected is None or attempt['crc_valid']:
            selected=dict(attempt)
            selected['decode_used_coarse_offset_hz']=offset
            selected['coarse_source']=source
        if attempt['crc_valid']:
            break
    selected.update(psd_analysis=analysis,coarse_attempts=attempts,
                    coarse_retry_used=len(attempts)>1,runtime_s=time.monotonic()-started)
    return selected


def decode_centered_burst(samples,sample_rate,cfo_fallback=True):
    """Opt-in for a centered single channel already qualified by ZC/CP.

    Center=0 is only a coarse hypothesis; OFDM/fine CFO/CRC checks still run.
    A failed CRC triggers the unchanged legacy PSD path, at most once.
    """
    if not np.isfinite(sample_rate) or not 15.36e6<=sample_rate<=25e6:
        raise ValueError('Center-first requires centered 15.36--25 MS/s IQ')
    started=time.monotonic()
    center=_decode_at_offset(samples,sample_rate,cfo_fallback,coarse_offset_hz=0.)
    attempts=[dict(source='known_center',offset_hz=0.,result=center)]
    selected=center;source='known_center';psd=None
    if not center['crc_valid']:
        psd=decode_burst(samples,sample_rate,cfo_fallback)
        attempts.append(dict(source='legacy_psd',offset_hz=None,result=psd))
        rank=lambda x:(x['crc_valid'],x['zc_ofdm']=='passed',x['frequency_resample']=='passed')
        if rank(psd)>rank(center):selected=psd;source='legacy_psd'
    result=dict(selected)
    result.update(center_first=True,coarse_source=source,coarse_attempts=attempts,
                  coarse_fallback_used=psd is not None,
                  coarse_fallback_rescued=bool(psd is not None and psd['crc_valid']),
                  runtime_s=time.monotonic()-started)
    return result
