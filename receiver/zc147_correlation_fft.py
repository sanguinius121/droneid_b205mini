"""Shared-input FFT backend for the experimental ZC147 detector.

Float64 normalization and complex128 arithmetic preserve the reference scale.
No change to the CFO bank, threshold, or peak selection is made here.
"""
from functools import lru_cache
import math
import numpy as np
from scipy.fft import fft, ifft, next_fast_len
from zc147_detector import FS, NFFT, template


@lru_cache(maxsize=64)
def _kernel_fft(root, cfo_hz, fft_length):
    # Moving CFO rotation from the input to the reference changes only a
    # unit-magnitude phase factor at each correlation lag.
    ref = template(root) * np.exp(2j*np.pi*cfo_hz*np.arange(NFFT)/FS)
    result = fft(np.conj(ref[::-1]), fft_length)
    result.flags.writeable = False
    return result


def best_correlation_scores(samples, cfo_bank_hz, root=147):
    """Return maximum squared normalized score and winning CFO at every lag."""
    samples = np.asarray(samples)
    if samples.ndim != 1 or not np.iscomplexobj(samples) or not np.isfinite(samples).all():
        raise ValueError('Finite one-dimensional complex IQ required')
    bank = [float(cfo) for cfo in cfo_bank_hz]
    if not bank or not all(math.isfinite(cfo) for cfo in bank):
        raise ValueError('Nonempty finite CFO bank required')
    if len(samples) < NFFT:
        return np.empty(0), np.empty(0)
    x = np.asarray(samples, dtype=np.complex128)
    fft_length = next_fast_len(len(x) + NFFT - 1)
    spectrum = fft(x, fft_length)
    energy = np.r_[0., np.cumsum(abs(x)**2, dtype=np.float64)]
    ref = template(root)
    denominator = (energy[NFFT:] - energy[:-NFFT]) * float(np.vdot(ref, ref).real)
    usable = denominator > np.finfo(float).tiny
    best = None
    selected = None
    for cfo in bank:
        correlation = ifft(spectrum * _kernel_fft(root, cfo, fft_length))[NFFT-1:len(x)]
        scores = np.zeros(len(correlation))
        np.divide(abs(correlation)**2, denominator, out=scores, where=usable)
        np.clip(scores, 0, 1, out=scores)
        if best is None:
            best = scores
            selected = np.full(len(scores), cfo)
        else:
            take = scores > best
            best[take] = scores[take]
            selected[take] = cfo
    return best, selected


def overlap_save_scores(samples, cfo_bank_hz, fft_length=8192, root=147):
    """Block convolution with NFFT-1 input overlap; return all valid lags.

    Each block owns fft_length-NFFT+1 output lags. The preceding invalid
    circular-convolution samples are discarded, including at the final block.
    """
    samples = np.asarray(samples)
    if samples.ndim != 1 or not np.iscomplexobj(samples) or not np.isfinite(samples).all():
        raise ValueError('Finite one-dimensional complex IQ required')
    bank = np.asarray(list(cfo_bank_hz), dtype=float)
    if bank.ndim != 1 or not len(bank) or not np.isfinite(bank).all():
        raise ValueError('Nonempty finite CFO bank required')
    if not isinstance(fft_length, int) or fft_length < NFFT:
        raise ValueError('FFT length must be an integer >= NFFT')
    count = max(0, len(samples)-NFFT+1)
    if not count:
        return np.empty(0), np.empty(0)
    x = np.asarray(samples, dtype=np.complex128)
    energy = np.r_[0., np.cumsum(abs(x)**2, dtype=np.float64)]
    ref = template(root)
    denominator = (energy[NFFT:]-energy[:-NFFT])*float(np.vdot(ref,ref).real)
    kernels = np.stack([_kernel_fft(root, float(cfo), fft_length) for cfo in bank])
    best = np.zeros(count)
    selected = np.empty(count)
    step = fft_length-NFFT+1
    for start in range(0, count, step):
        length = min(step, count-start)
        spectrum = fft(x[start:start+length+NFFT-1], fft_length)
        values = abs(ifft(kernels*spectrum[None,:], axis=1)[:,NFFT-1:NFFT-1+length])**2
        den = denominator[start:start+length]
        np.divide(values,den[None,:],out=values,where=den[None,:]>np.finfo(float).tiny)
        values[:,den<=np.finfo(float).tiny] = 0.
        np.clip(values,0,1,out=values)
        indices = np.argmax(values,axis=0)
        best[start:start+length] = values[indices,np.arange(length)]
        selected[start:start+length] = bank[indices]
    return best, selected
