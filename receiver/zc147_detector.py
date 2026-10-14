"""Offline experimental time-domain ZC147 matched filter; no energy/PSD gate.

Input is one already channelized 15.36 MS/s stream. Coarse channel selection is
outside this module. A matched peak is a hypothesis, never CRC confirmation.
"""
import math
import numpy as np
from scipy import signal
from fractions import Fraction
from zcsequence import zcsequence_t
from helpers import itfft

FS=15.36e6
NFFT=1024
PILOT5_USEFUL_OFFSET=5560
FRAME_SAMPLES=9880  # 9*1024 + 2*80 + 7*72


def channelize(samples,sample_rate,coarse_hz):
    """Known coarse center only; polyphase low-pass/resample, index0 aligned.

    Continuous multi-channel scanning is intentionally not implemented here.
    """
    if not math.isfinite(sample_rate) or sample_rate<FS or not math.isfinite(coarse_hz):
        raise ValueError('Finite Fs>=15.36MS/s and coarse CFO required')
    samples=np.asarray(samples)
    if samples.ndim!=1 or not np.iscomplexobj(samples) or not np.all(np.isfinite(samples)):
        raise ValueError('Finite one-dimensional complex IQ required')
    x=samples*np.exp(-2j*np.pi*coarse_hz*np.arange(len(samples))/sample_rate)
    ratio=Fraction(FS/sample_rate).limit_denominator(100000)
    if abs(float(ratio)*sample_rate-FS)>.01:raise ValueError('Unsupported resample ratio')
    return signal.resample_poly(x,ratio.numerator,ratio.denominator)


def template(root=147):
    carriers=zcsequence_t(root,601).copy();carriers[300]=0
    return itfft(carriers).astype(np.complex128)


def correlation_scores(samples,cfo_hz=0.,root=147):
    samples=np.asarray(samples)
    if samples.ndim!=1 or not np.iscomplexobj(samples) or not np.all(np.isfinite(samples)):
        raise ValueError('Finite one-dimensional complex IQ required')
    if not math.isfinite(cfo_hz):raise ValueError('Finite CFO required')
    ref=template(root)
    if len(samples)<len(ref):return np.empty(0)
    x=samples*np.exp(-2j*np.pi*cfo_hz*np.arange(len(samples))/FS)
    numerator=abs(signal.fftconvolve(x,np.conj(ref[::-1]),mode='valid'))**2
    energy=np.r_[0.,np.cumsum(abs(x)**2,dtype=np.float64)]
    denominator=(energy[NFFT:]-energy[:-NFFT])*float(np.vdot(ref,ref).real)
    scores=np.zeros(len(numerator));np.divide(numerator,denominator,out=scores,where=denominator>np.finfo(float).tiny)
    return np.clip(scores,0,1)


def cp_score(samples,frame_start):
    # CP train diagnostic, not an admission gate in this first prototype.
    start=int(round(frame_start));values=[]
    for cp in [80]+[72]*7+[80]:
        if start<0 or start+cp+NFFT>len(samples):return None
        a=samples[start:start+cp];b=samples[start+NFFT:start+NFFT+cp]
        den=float(np.vdot(a,a).real*np.vdot(b,b).real)
        values.append(float(abs(np.vdot(a,b))**2/den) if den>0 else 0.)
        start+=cp+NFFT
    return float(np.median(values))


def find_zc147(samples,threshold=.08,cfo_bank_hz=None,min_distance=8192,backend='reference',fft_length=8192):
    """Squared normalized correlation; score in [0,1]; fixed CFO bank.

    Peaks at valid pilot useful-start indices; all peaks above the preselected
    threshold returned, with frame/padding/CP diagnostics. Slice edges may mean
    an incomplete frame. Whole-stream false alarm rates need continuous IQ.
    backend='reference' keeps the original per-CFO calculation;
    backend='shared_fft' reuses the input FFT and normalization across CFOs.
    """
    if not 0<threshold<=1:raise ValueError('threshold must be in (0,1]')
    if not isinstance(min_distance,int) or min_distance<=0:raise ValueError('Positive integer peak distance required')
    bank=list(cfo_bank_hz) if cfo_bank_hz is not None else np.arange(-30000,30001,7500).tolist()
    if not bank:raise ValueError('Nonempty CFO bank required')
    if backend=='shared_fft':
        from zc147_correlation_fft import best_correlation_scores
        best,selected=best_correlation_scores(samples,bank)
    elif backend=='overlap_save':
        from zc147_correlation_fft import overlap_save_scores
        best,selected=overlap_save_scores(samples,bank,fft_length=fft_length)
    elif backend=='reference':
        best=None;selected=None
        for cfo in bank:
            scores=correlation_scores(samples,cfo)
            if best is None:best=scores.copy();selected=np.full(len(scores),float(cfo))
            else:
                take=scores>best;best[take]=scores[take];selected[take]=cfo
    else:raise ValueError('Unknown correlation backend')
    if not len(best):return [],best
    # Include possible boundary peaks, while suppressing adjacent multipath/CFO peaks.
    peaks,_=signal.find_peaks(np.r_[-1.,best,-1.],height=threshold,distance=min_distance)
    found=[]
    for pos in peaks-1:
        frame=int(pos)-PILOT5_USEFUL_OFFSET
        found.append(dict(pilot_start_sample=int(pos),frame_start_sample=frame,
                          score=float(best[pos]),cfo_hz=float(selected[pos]),
                          frame_complete=bool(0<=frame and frame+FRAME_SAMPLES<=len(samples)),
                          cp_median=cp_score(samples,frame)))
    return found,best
