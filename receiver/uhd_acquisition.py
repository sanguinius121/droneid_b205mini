"""Finite single-channel UHD acquisition, injectable for offline verification.

Never concatenate samples across an RX error or a measured timestamp gap.
No UHD import is needed until the caller supplies its installed module.
"""
import time
import numpy as np
from capture_records import make_acquisition


def receive_acquisition(streamer, metadata, buffer, num_samples, uhd_module,
                        device, frequency_requested, frequency_actual,
                        fs_requested, fs_actual, should_stop=lambda: False):
    """Return owned complex64 IQ and its G0 acquisition record.

    Partial clean prefixes are retained and explicitly marked on interruption.
    Error-bearing receive calls are discarded because their sample continuity
    is unknown. Device clock epoch/continuity identity remains caller-owned.
    """
    if isinstance(num_samples, bool) or int(num_samples) != num_samples or num_samples <= 0:
        raise ValueError('num_samples must be a positive integer')
    buf = np.asarray(buffer)
    if buf.dtype != np.complex64 or buf.ndim not in (1, 2) or not buf.size:
        raise ValueError('buffer must be a nonempty complex64 single-channel buffer')
    if buf.ndim == 2 and buf.shape[0] != 1:
        raise ValueError('only one RX channel is supported')
    flat = buf.reshape(-1)
    samples = np.empty(int(num_samples), dtype=np.complex64)
    copied = 0
    first_time = None
    errors = []
    reason = 'stream_start'
    started = time.monotonic()

    def error(code, message):
        errors.append({'code': str(code), 'message': str(message)})

    try:
        command = uhd_module.types.StreamCMD(uhd_module.types.StreamMode.num_done)
        command.num_samps = int(num_samples)
        command.stream_now = True
        if should_stop():
            error('stopped', 'Stop requested before acquisition')
            reason = 'rx_gap'
        else:
            streamer.issue_stream_cmd(command)
            while copied < num_samples:
                if should_stop():
                    error('stopped', 'Stop requested during acquisition')
                    reason = 'rx_gap'
                    break
                count = streamer.recv(buffer, metadata, timeout=2.0)
                if metadata.error_code != uhd_module.types.RXMetadataErrorCode.none:
                    code = getattr(metadata.error_code, 'name', str(metadata.error_code))
                    error(code, metadata.strerror())
                    reason = 'overflow' if 'overflow' in str(code).lower() else 'rx_gap'
                    break
                if count <= 0:
                    error('zero_read', 'Receive returned no samples')
                    reason = 'rx_gap'
                    break
                if count > flat.size or count > num_samples - copied:
                    # Never truncate an unexpectedly oversized receive invisibly.
                    error('invalid_count', 'Receive count exceeds buffer or requested samples')
                    reason = 'rx_gap'
                    break
                stamp = None
                if getattr(metadata, 'has_time_spec', False):
                    stamp = float(metadata.time_spec.get_real_secs())
                if copied == 0:
                    first_time = stamp
                elif first_time is not None and stamp is not None and fs_actual is not None:
                    if abs(stamp - (first_time + copied / fs_actual)) * fs_actual > 0.5:
                        error('timestamp_gap', 'Receive timestamp differs by more than half a sample')
                        reason = 'rx_gap'
                        break
                samples[copied:copied + count] = flat[:count]
                copied += count
    except Exception as exc:
        error('exception', f'{type(exc).__name__}: {exc}')
        reason = 'rx_gap'
    finally:
        try:
            stop = uhd_module.types.StreamCMD(uhd_module.types.StreamMode.stop_cont)
            streamer.issue_stream_cmd(stop)
        except Exception as exc:
            error('stop_error', f'{type(exc).__name__}: {exc}')
            reason = 'rx_gap'
    ended = time.monotonic()
    record = make_acquisition(
        device=device, center_requested_hz=frequency_requested,
        center_actual_hz=frequency_actual, sample_rate_requested_sps=fs_requested,
        sample_rate_actual_sps=fs_actual, sample_count=copied,
        first_sample_device_time_s=first_time,
        receive_start_monotonic_s=started, receive_end_monotonic_s=ended,
        rx_errors=errors, dropped_samples=None if errors else 0,
        discontinuity_reason=reason)
    return samples[:copied], record
