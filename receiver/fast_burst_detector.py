"""Bounded STFT envelope backend for the existing energy detector.

CPU is the default; CUDA is explicit and optional. FFT roundoff and float32
reduction order can change values close to the energy threshold. This is an
experimental interchangeable detector, not a new DroneID validation oracle.
"""
import math
import numpy as np
from scipy import signal, fft
from helpers import estimate_offset


def stft_envelope(samples, batch_frames=16384, backend='cpu'):
    """Match scipy STFT(64, hop=32, Hann, zero boundary, padded=True).

    Materialize only a bounded FFT batch and a one-dimensional envelope, rather
    than the complete complex STFT. CUDA includes transfers in this function.
    """
    samples = np.asarray(samples)
    if samples.ndim != 1 or not np.iscomplexobj(samples):
        raise ValueError('Expected one-dimensional complex IQ')
    if isinstance(batch_frames, bool) or not isinstance(batch_frames, int) or batch_frames <= 0:
        raise ValueError('batch_frames must be a positive integer')
    if backend not in ('cpu', 'cuda'):
        raise ValueError('backend must be cpu or cuda')
    if len(samples) < 64:
        raise ValueError('At least 64 samples required')
    dtype = np.result_type(samples.dtype, np.complex64)
    real_dtype = np.empty((), dtype=dtype).real.dtype
    frame_count = (len(samples) + 31)//32 + 1
    envelope = np.empty(frame_count, dtype=real_dtype)
    window = signal.get_window('hann', 64).astype(dtype)
    scale = np.sqrt(1.0 / window.sum()**2)
    if backend == 'cuda':
        import cupy as cp
        gpu_window = cp.asarray(window)
    for first in range(0, frame_count, batch_frames):
        count = min(batch_frames, frame_count-first)
        # The conceptual padded input begins with 32 zeros. Align every batch
        # to the same global hop grid; independently padded batches would not.
        start = first*32 - 32
        end = (first+count-1)*32 + 32
        block = np.zeros(end-start, dtype=dtype)
        left, right = max(0,start), min(len(samples),end)
        if right > left:
            block[left-start:right-start] = samples[left:right]
        if backend == 'cpu':
            frames = np.lib.stride_tricks.sliding_window_view(block,64)[::32]
            transformed = fft.fft(frames*window, axis=-1)
            transformed *= scale
            envelope[first:first+count] = np.mean(np.abs(transformed),axis=-1)
        else:
            gpu_block = cp.asarray(block)
            frames = cp.lib.stride_tricks.sliding_window_view(gpu_block,64)[::32]
            transformed = cp.fft.fft(frames*gpu_window,axis=-1)
            transformed *= scale
            envelope[first:first+count] = cp.mean(cp.abs(transformed),axis=-1).get()
    return envelope


def binary_burst_edges(above, sample_rate):
    """Closed constant-height plateaus have half-height width equal to run length.

    scipy find_peaks excludes peaks touching either boundary. The existing wlen
    is 100 times the maximum accepted width, so accepted runs always have both
    zero-valued bases within that window. Reject other lengths before CFO work.
    """
    above = np.asarray(above, dtype=np.bool_)
    changes = np.flatnonzero(above[1:] != above[:-1]) + 1
    if not len(changes):
        return []
    starts = changes[above[changes]]
    ends = changes[~above[changes]]
    # Leading/trailing open runs are intentionally excluded.
    if len(ends) and (not len(starts) or ends[0] < starts[0]):
        ends = ends[1:]
    if len(starts) > len(ends):
        starts = starts[:-1]
    width = ends - starts
    keep = (width >= 630e-6*sample_rate/32) & (width <= 665e-6*sample_rate/32)
    return list(zip(starts[keep]-0.5, ends[keep]-0.5))

def find_burst_candidates_fast(samples, sample_rate, batch_frames=16384, backend='cpu', psd_split=False):
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError('Positive finite sample rate required')
    if len(samples) < 128:
        return []
    envelope = stft_envelope(samples,batch_frames,backend)
    above = envelope > 1.3*np.mean(envelope)
    dt = 32/sample_rate
    edges = binary_burst_edges(above, sample_rate)
    padding = math.ceil(225e-6*sample_rate)
    found = []
    for left,right in edges:
        begin = max(0,int(round(left*dt*sample_rate)))
        end = min(len(samples),int(round(right*dt*sample_rate)))
        if end <= begin:
            continue
        iq=samples[max(0,begin-padding):min(len(samples),end+padding)]
        if psd_split:
            from psd_band_analysis import analyze_bands
            analysis=analyze_bands(iq,sample_rate)
            ok=analysis['admitted']
            cfo=analysis['old_offset_hz'] if analysis['old_offset_hz'] is not None else analysis['new_offset_hz']
        else:
            cfo,ok = estimate_offset(iq,sample_rate)
        if ok:
            found.append(dict(burst_range=dict(start_sample=begin,end_sample=end),cfo_hz=float(cfo)))
    return found
