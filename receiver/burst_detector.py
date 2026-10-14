"""Offline-testable packetization with overlap within one acquisition only."""
import math
import numpy as np
from scipy import signal
from helpers import estimate_offset


def find_burst_candidates(samples, sample_rate):
    """Live-style energy/length/bandwidth candidates; does not validate DroneID CRC."""
    if len(samples) < 128:
        return []
    _, times, stft = signal.stft(samples, sample_rate, nfft=64, nperseg=64,
                                return_onesided=False)
    if len(times) < 2:
        return []
    envelope = np.mean(np.abs(stft), axis=0)
    above = envelope > 1.3 * np.mean(np.abs(stft))
    dt = times[1] - times[0]
    _, properties = signal.find_peaks(above, width=[630e-6/dt, 665e-6/dt],
                                     wlen=max(10, int(100*665e-6/dt)))
    found = []
    padding = int(math.ceil(225e-6*sample_rate))
    for left, right in zip(properties['left_ips'], properties['right_ips']):
        begin = max(0, int(round(left * dt * sample_rate)))
        end = min(len(samples), int(round(right * dt * sample_rate)))
        if end <= begin:
            continue
        iq = samples[max(0, begin-padding):min(len(samples), end+padding)]
        cfo, ok = estimate_offset(iq, sample_rate)
        if ok:
            found.append(dict(burst_range=dict(start_sample=begin, end_sample=end),
                              cfo_hz=float(cfo)))
    return found


class BurstDetector:
    def __init__(self, chunk_samples, padding_samples, overlap_samples, detector=None):
        for name, value in [('chunk_samples',chunk_samples),('padding_samples',padding_samples),('overlap_samples',overlap_samples)]:
            if isinstance(value, bool) or not isinstance(value, (int,np.integer)) or value < 0:
                raise ValueError(f'{name} must be a nonnegative integer')
        if chunk_samples <= 0 or overlap_samples >= chunk_samples:
            raise ValueError('Require 0 <= overlap_samples < chunk_samples')
        if overlap_samples < padding_samples:
            raise ValueError('Overlap must cover padding')
        self.chunk_samples = int(chunk_samples)
        self.padding_samples = int(padding_samples)
        self.overlap_samples = int(overlap_samples)
        self.detector = detector or find_burst_candidates
        self.custom_detector = detector is not None

    def process_acquisition(self, samples, acquisition):
        """Return source-local ranges, resetting at each acquisition (never join RF hops)."""
        samples = np.asarray(samples)
        if samples.ndim != 1 or not np.iscomplexobj(samples):
            raise ValueError('IQ must be a one-dimensional complex array')
        if acquisition.get('sample_count') != len(samples):
            raise ValueError('Acquisition sample_count mismatch')
        fs = acquisition.get('sample_rate_actual_sps')
        if isinstance(fs, bool) or fs is None or not math.isfinite(fs) or fs <= 0:
            raise ValueError('Measured actual sample rate is required for packetization')
        minimum_overlap = math.ceil(665e-6*fs) + self.padding_samples
        if not self.custom_detector and self.overlap_samples < minimum_overlap:
            raise ValueError('Overlap must cover maximum burst duration plus padding')
        results = []
        for core_start in range(0, len(samples), self.chunk_samples):
            core_end = min(len(samples), core_start + self.chunk_samples)
            begin = max(0, core_start-self.overlap_samples)
            end = min(len(samples), core_end+self.overlap_samples)
            for candidate in self.detector(samples[begin:end], fs):
                body = candidate['burst_range']
                left, right = body['start_sample'], body['end_sample']
                if any(isinstance(n,bool) or not isinstance(n,(int,np.integer)) for n in [left,right]):
                    raise ValueError('Detector ranges must use integer sample coordinates')
                if not 0 <= left < right <= end-begin:
                    raise ValueError('Detector returned invalid source range')
                left, right = begin+int(left), begin+int(right)
                center = (left+right)/2
                # A frame belongs to precisely one core, even when its padded slice overlaps.
                if not core_start <= center < core_end:
                    continue
                cfo = candidate.get('cfo_hz')
                if cfo is not None and (isinstance(cfo,bool) or not math.isfinite(cfo)):
                    raise ValueError('Invalid CFO')
                # STFT windows can shift a body's estimated edges by a few bins.
                # Compare body coordinates and RF bin, never the overlapping padding.
                duplicate = False
                for prior in results:
                    body = prior['burst_range']
                    a, b = body['start_sample'], body['end_sample']
                    intersection = max(0, min(right,b)-max(left,a))
                    union = max(right,b)-min(left,a)
                    same_rf = (prior['cfo_hz'] is None and cfo is None) or (
                        prior['cfo_hz'] is not None and cfo is not None and
                        abs(prior['cfo_hz']-cfo) <= 2*fs/2048)
                    if same_rf and intersection/union >= 0.95 and abs(left-a)<=64 and abs(right-b)<=64:
                        duplicate = True
                        break
                if duplicate:
                    continue
                results.append(dict(burst_range=dict(start_sample=left,end_sample=right),
                    slice_range=dict(start_sample=max(0,left-self.padding_samples),
                                     end_sample=min(len(samples),right+self.padding_samples)),
                    cfo_hz=cfo))
        return sorted(results,key=lambda r:(r['burst_range']['start_sample'],r['burst_range']['end_sample']))
