"""Experimental single-channel energy/duration gate plus local ZC147/CP.

Designed for a centered 15.36--25 MS/s channel. A qualified candidate is not a
CRC-valid message. Wideband channelization and rate switching are separate.
"""
import math
import numpy as np
from scipy import signal
from fractions import Fraction
from zc147_detector import FS,FRAME_SAMPLES,channelize,find_zc147
from zc147_correlation_fft import overlap_save_scores


def cp_frame_possible(x,threshold):
    """Necessary CP condition for admission, before the expensive FFT bank.

    Same nine CP windows as cp_score. A median above threshold requires at
    least five windows above it. The tiny margin covers accumulated roundoff.
    """
    count=len(x)-FRAME_SAMPLES+1
    if count<=0:return False
    if threshold<=0:return True
    product=np.r_[0j,np.cumsum(x[:-1024]*np.conj(x[1024:]),dtype=np.complex128)]
    energy=np.r_[0.,np.cumsum(abs(x)**2,dtype=np.float64)]
    votes=np.zeros(count,dtype=np.uint8);offset=0
    for cp in [80]+[72]*7+[80]:
        a=slice(offset,offset+count);b=slice(offset+cp,offset+cp+count)
        numerator=abs(product[b]-product[a])**2
        denominator=(energy[b]-energy[a])*(energy[offset+1024+cp:offset+1024+cp+count]-energy[offset+1024:offset+1024+count])
        votes+=(denominator>np.finfo(float).tiny)&(numerator>=(threshold-1e-5)*denominator)
        offset+=cp+1024
    return bool(np.any(votes>=5))


def energy_candidates(samples,sample_rate,bin_us=4.,factor=2.5,gap_us=16.,
                      duration_us=(400.,5000.)):
    x=np.asarray(samples)
    if x.ndim!=1 or not np.iscomplexobj(x) or not np.isfinite(x).all():raise ValueError('Finite complex IQ required')
    if not math.isfinite(sample_rate) or sample_rate<=0:raise ValueError('Positive sample rate required')
    if factor<=1 or bin_us<=0 or gap_us<0 or not 0<duration_us[0]<=duration_us[1]:raise ValueError('Invalid energy gate')
    step=max(1,int(round(bin_us*1e-6*sample_rate)));n=len(x)//step
    if n<2:return [],dict(noise_power=0.,energy_candidates=0)
    power=(abs(x[:n*step])**2).reshape(n,step).mean(axis=1)
    floor=float(np.median(power));above=power>factor*floor
    edges=np.flatnonzero(np.diff(np.r_[False,above,False]))
    runs=[];gap=int(round(gap_us*1e-6*sample_rate/step))
    for a,b in zip(edges[::2],edges[1::2]):
        if runs and a-runs[-1][1]<=gap:runs[-1][1]=int(b)
        else:runs.append([int(a),int(b)])
    # Collision/interference can join a valid 644-us frame to a longer run.
    # Keep bounded runs up to 5 ms for local ZC rather than a strict upper gate.
    accepted=[dict(start_sample=a*step,end_sample=b*step) for a,b in runs
              if duration_us[0]<=((b-a)*step/sample_rate*1e6)<=duration_us[1]]
    return accepted,dict(noise_power=floor,threshold_factor=factor,energy_candidates=len(accepted),runs=len(runs))


def detect_hybrid(samples,sample_rate,zc_threshold=.08,cp_threshold=.02,coarse_zc_threshold=.02,cp_prefilter=False):
    if not FS<=sample_rate<=25e6:raise ValueError('Hybrid requires centered single-channel IQ at 15.36--25 MS/s')
    x=np.asarray(samples)
    ranges,diagnostic=energy_candidates(x,sample_rate)
    padding=math.ceil(.0003*sample_rate);found=[];local_seconds=0.;cp_rejected=0;coarse_rejected=0
    import time
    for body in ranges:
        lo=max(0,body['start_sample']-padding);hi=min(len(x),body['end_sample']+padding)
        started=time.perf_counter()
        # The receiver already centers this channel. Avoid allocating a zero-
        # frequency mixer and promoting the entire slice to complex128.
        ratio=Fraction(FS/sample_rate).limit_denominator(100000)
        y=signal.resample_poly(x[lo:hi],ratio.numerator,ratio.denominator)
        if cp_prefilter and not cp_frame_possible(y,cp_threshold):
            local_seconds+=time.perf_counter()-started;cp_rejected+=1
            continue
        # Lossy development gate: one CFO only for a permissive coarse score,
        # followed by the unchanged full bank. Never use this coarse peak's
        # timing/CFO as the final decision (ZC timing/CFO ambiguity).
        coarse,_=overlap_save_scores(y,[0.],fft_length=4096)
        if not len(coarse) or float(coarse.max())<coarse_zc_threshold:
            local_seconds+=time.perf_counter()-started;coarse_rejected+=1
            continue
        hits,_=find_zc147(y,threshold=zc_threshold,backend='overlap_save',fft_length=4096)
        local_seconds+=time.perf_counter()-started
        for h in hits:
            if not h['frame_complete'] or h['cp_median'] is None or h['cp_median']<cp_threshold:continue
            start=int(round(lo+h['frame_start_sample']*sample_rate/FS))
            end=int(round(lo+(h['frame_start_sample']+FRAME_SAMPLES)*sample_rate/FS))
            if not 0<=start<end<=len(x):continue
            if any(abs(start-p['burst_range']['start_sample'])<.0002*sample_rate for p in found):continue
            found.append(dict(burst_range=dict(start_sample=start,end_sample=end),cfo_hz=0.,
                              zc147_score=h['score'],cp_median=h['cp_median']))
    diagnostic.update(qualified_candidates=len(found),local_zc_seconds=local_seconds,cp_rejected_windows=cp_rejected,
                      coarse_zc_rejected_windows=coarse_rejected,coarse_zc_threshold=coarse_zc_threshold)
    return sorted(found,key=lambda h:h['burst_range']['start_sample']),diagnostic


def find_hybrid_candidates(samples,sample_rate):
    found,_=detect_hybrid(samples,sample_rate)
    return found
