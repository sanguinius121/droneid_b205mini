"""Shared, opt-in bandwidth admission and coarse-CFO hypotheses.

Keep the legacy Welch defaults and DC patch. This module does not assert that
mean-based admission is reliable at low SNR.
"""
import math
import numpy as np
from scipy import signal

CONFIG = dict(version=1, nfft=2048, nperseg=256, noverlap=128,
              window='hann', dc_patch_bins=20, dc_patch_factor=1.1,
              admission_factor=0.5, bandwidth_mhz=[8., 11.])


def analyze_bands(samples, sample_rate):
    if isinstance(sample_rate, bool) or not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError('Positive finite sample rate required')
    samples = np.asarray(samples)
    if samples.ndim != 1 or not np.iscomplexobj(samples) or not np.all(np.isfinite(samples)):
        raise ValueError('Finite one-dimensional complex IQ required')
    result = dict(config=dict(CONFIG), mean_psd=None, old_bands=[], new_bands=[],
                  old_offset_hz=None, new_offset_hz=None, admitted=False)
    if len(samples) < 2048:
        return result
    _, psd = signal.welch(samples, sample_rate, nfft=2048, nperseg=256,
                          noverlap=128, window='hann', return_onesided=False)
    psd = np.fft.fftshift(psd)
    psd[1014:1034] = 1.1 * psd.mean()
    result['mean_psd'] = float(psd.mean())
    for name, factor in [('old', 1.), ('new', .5)]:
        indices = np.flatnonzero(psd > factor * psd.mean())
        groups = np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
        for band in groups:
            if not len(band):
                continue
            width = float((band[-1] - band[0]) * sample_rate / 2048)
            center = float(((band[0] + band[-1]) / 2 - 1024) * sample_rate / 2048)
            accepted = 8e6 < width < 11e6
            result[name + '_bands'].append(dict(first_bin=int(band[0]), last_bin=int(band[-1]),
                width_hz=width, center_hz=center, accepted=accepted))
            if accepted and result[name + '_offset_hz'] is None:
                result[name + '_offset_hz'] = center
    result['admitted'] = result['new_offset_hz'] is not None
    return result
