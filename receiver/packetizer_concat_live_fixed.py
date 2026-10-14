#!/usr/bin/env python3
import argparse
import signal
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import scipy.signal as sp_signal
import uhd

from helpers import estimate_offset

RECV_BUFFER_LEN = 4096
DEFAULT_FREQUENCIES_MHZ = [
    2414.5, 2429.502441, 2434.5, 2444.5, 2459.5, 2474.5,
    5721.5, 5731.5, 5741.5, 5756.5, 5761.5, 5771.5,
    5786.5, 5801.5, 5816.5, 5831.5,
]
exit_requested = False

def initialize_usrp(args):
    """Select the network X310 or USB B205mini (B200 family)."""
    if args.device == "b205mini":
        device_args = "type=b200,recv_frame_size=8200,num_recv_frames=512"
        if args.serial:
            device_args += f",serial={args.serial}"
        subdev = args.subdev
    else:
        device_args = f"addr={args.addr},recv_frame_size=8200,num_recv_frames=512"
        subdev = args.subdev or "A:0"

    print(f"SDR: {args.device}, device args: {device_args}")
    usrp = uhd.usrp.MultiUSRP(device_args)
    if subdev:
        usrp.set_rx_subdev_spec(uhd.usrp.SubdevSpec(subdev))
    return usrp

def signal_handler(sig, frame):
    global exit_requested
    exit_requested = True


def set_sdr(usrp, sample_rate=25e6, duration_s=1.3, gain=None):
    # RX2 port for 2.4 GHz antenna
    usrp.set_rx_antenna("RX2",0)
    if gain is not None and gain > 0:
        usrp.set_rx_gain(gain, 0)
    else:
        usrp.set_rx_agc(True, 0)

    num_samps = duration_s*sample_rate

    usrp.set_rx_rate(sample_rate, 0)
    dev_samp_rate = usrp.get_rx_rate()

    # Set up the stream and receive buffer
    st_args = uhd.usrp.StreamArgs("fc32","sc16")
    st_args.channels = [0]
    _metadata = uhd.types.RXMetadata()
    _streamer = usrp.get_rx_stream(st_args)
    _recv_buffer = np.zeros((1, RECV_BUFFER_LEN), dtype=np.complex64)

    return num_samps, _metadata, _streamer, _recv_buffer


def receive_samples(streamer, metadata, recv_buffer, num_samps):
    num_samps = int(num_samps)
    samples = np.zeros(num_samps, dtype=np.complex64)

    stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
    stream_cmd.num_samps = num_samps
    stream_cmd.stream_now = True
    streamer.issue_stream_cmd(stream_cmd)

    copied = 0
    while copied < num_samps:
        num_rx = streamer.recv(recv_buffer, metadata, timeout=2.0)
        if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
            print(f"RX error: {metadata.strerror()}")
            return None
        if num_rx <= 0:
            print('RX error: zero samples received')
            return None

        take = min(num_rx, num_samps - copied)
        samples[copied:copied + take] = recv_buffer[0, :take]
        copied += take

    return samples


def append_packets_to_iq_file(packets, output_file):
    """Append detected packets to one IQ file, float32 interleaved I/Q."""
    if not packets:
        return 0

    merged_packets = np.concatenate(packets).astype(np.complex64)
    with open(output_file, 'ab') as f:
        merged_packets.view(np.float32).tofile(f)
    return len(packets)



def find_packet_candidate_time(raw_data, Fs, debug=False, packet_type="droneid", legacy=False):
    """Find packets with the right length by looking at signal power."""

    if packet_type == "droneid":
        if legacy:
            min_packet_len_t = 565e-6
            max_packet_len_t = 600e-6
        else:
            min_packet_len_t = 630e-6
            max_packet_len_t = 665e-6
    elif packet_type == "c2":
        min_packet_len_t = 500e-6
        max_packet_len_t = 520e-6
    elif packet_type in ["beacon", "pairing"]:
        min_packet_len_t = 490e-6
        max_packet_len_t = 540e-6
    elif packet_type == "video":
        min_packet_len_t = 630e-6
        max_packet_len_t = 665e-6
    else:
        raise ValueError(f"Unsupported packet_type: {packet_type}")

    print("Packet Type:", packet_type)

    start_offset = 15 * 15e-6
    end_offset = 15 * 15e-6

    f, t, Zxx = sp_signal.stft(raw_data, Fs, nfft=64, nperseg=64)
    if len(t) < 2:
        return [], []

    res_abs = np.mean(np.abs(Zxx), axis=0)
    noise_floor = np.mean(np.abs(Zxx))
    above_level = res_abs > 1.3 * noise_floor

    signal_length_min_samples = int(min_packet_len_t / (t[1] - t[0]))
    signal_length_max_samples = int(max_packet_len_t / (t[1] - t[0]))

    peaks, properties = sp_signal.find_peaks(
        above_level,
        width=[signal_length_min_samples, signal_length_max_samples],
        wlen=max(10, 100 * signal_length_max_samples),
    )

    packets = []
    packet_ranges = []

    for i, _ in enumerate(peaks):
        start = properties["left_bases"][i] * (t[1] - t[0])
        end = properties["right_bases"][i] * (t[1] - t[0])
        length = properties["widths"][i] * (t[1] - t[0])

        start_idx = int((start - start_offset) * Fs)
        end_idx = int((end + end_offset) * Fs)

        start_idx = max(0, start_idx)
        end_idx = min(len(raw_data), end_idx)

        if end_idx <= start_idx:
            if debug:
                print(f"Packet #{i}: invalid slice range [{start_idx}:{end_idx}]")
            continue

        packet_data = raw_data[start_idx:end_idx]
        center_freq_offset, found = estimate_offset(packet_data, Fs)

        if not found:
            if debug:
                print(
                    "Packet #%i, start %f, end %f, length %f, cfo MISMATCH"
                    % (i, start, end, length)
                )
            continue

        print(
            "Packet #%i, start %f, end %f, length %f, cfo %f"
            % (i, start, end, length, center_freq_offset)
        )

        packets.append(packet_data.copy())
        packet_ranges.append((start_idx, end_idx))

    if debug:
        plt.figure()
        plt.plot(t, above_level)
        if len(peaks) > 0:
            plt.scatter(t[peaks], np.abs(above_level[peaks]), marker="x", color="C5")
        plt.title("Packet candidates")
        plt.xlabel("Time [s]")
        plt.tight_layout()
        plt.show()

    return packets, packet_ranges



def process_samples_in_chunks(raw_samples, output_file, Fs, debug=False, packet_type="droneid",
                              legacy=False, chunk_time_s=0.5, base_sample_offset=0):
    """Process an in-memory IQ array in small chunks and append valid packets."""
    chunk_samples = int(chunk_time_s * Fs)
    if chunk_samples <= 0:
        raise ValueError("chunk_time_s too small")

    total_chunks = (len(raw_samples) + chunk_samples - 1) // chunk_samples
    print(f"Total samples in capture: {len(raw_samples)}")
    print(f"Chunk size: {chunk_samples} samples ({chunk_time_s} s)")
    print(f"Total chunks: {total_chunks}")

    total_detected = 0
    all_packet_ranges_global = []

    for chunk_idx in range(total_chunks):
        chunk_start = chunk_idx * chunk_samples
        chunk_end = min((chunk_idx + 1) * chunk_samples, len(raw_samples))
        chunk_data = raw_samples[chunk_start:chunk_end]

        print(f"\n=== Processing chunk {chunk_idx + 1}/{total_chunks} [{chunk_start}:{chunk_end}] ===")
        packets, packet_ranges = find_packet_candidate_time(
            chunk_data,
            Fs,
            debug=debug,
            packet_type=packet_type,
            legacy=legacy
        )

        print(f"Detected {len(packets)} valid packets in this chunk")
        if packets:
            appended = append_packets_to_iq_file(packets, output_file)
            total_detected += appended

        for start_idx, end_idx in packet_ranges:
            global_start = base_sample_offset + chunk_start + start_idx
            global_end = base_sample_offset + chunk_start + end_idx
            all_packet_ranges_global.append((global_start, global_end))

    return total_detected, all_packet_ranges_global



def parse_frequency_list(freq_args):
    if not freq_args:
        return [f * 1e6 for f in DEFAULT_FREQUENCIES_MHZ]
    freqs_hz = []
    for item in freq_args:
        for part in item.split(','):
            part = part.strip()
            if not part:
                continue
            freqs_hz.append(float(part) * 1e6)
    return freqs_hz



def main(args):
    global exit_requested
    from live_capture_runner import run_capture
    if args.truncate_output:
        raise ValueError('G2 requires a fresh output path; --truncate-output is disabled')
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    usrp = initialize_usrp(args)
    _, metadata, streamer, recv_buffer = set_sdr(
        usrp, args.sample_rate, args.duration, args.gain)
    return run_capture(args, usrp, streamer, metadata, recv_buffer, uhd,
                       parse_frequency_list(args.frequencies), lambda: exit_requested)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', choices=['x310', 'b205mini'], default='x310', help='SDR model (default: x310)')
    parser.add_argument('--addr', default='192.168.40.2', help='X310 IP address')
    parser.add_argument('--serial', default=None, help='B205mini serial number (optional)')
    parser.add_argument('--subdev', default=None, help='RX subdevice (default: A:0 for X310, automatic for B205mini)')
    parser.add_argument('-o', '--output-file', required=True, help='Output IQ file for detected packets')
    parser.add_argument('-s', '--sample-rate', default=50e6, type=float, help='Sample Rate')
    parser.add_argument('-p', '--packet-type', default='droneid', help='Packet type')
    parser.add_argument('-l', '--legacy', default=False, action='store_true', help='Legacy support')
    parser.add_argument('-d', '--debug', default=False, action='store_true', help='Enable debug output')
    parser.add_argument('-c', '--chunk-time', default=0.25, type=float, help='Chunk time in seconds')
    parser.add_argument('-t', '--duration', default=2, type=float, help='Capture time per frequency hop in seconds')
    parser.add_argument('-g', '--gain', default=0, type=float, help='RX gain in dB, zero or negative value for AGC')
    parser.add_argument('-f', '--frequencies', nargs='*', help='Center frequencies in MHz, space or comma separated')
    parser.add_argument('--settle-time', default=0.03, type=float, help='Settle time after retune in seconds')
    parser.add_argument('--run-once', action='store_true', help='Run through the frequency list once and stop')
    parser.add_argument('--lock-on-hit', action='store_true', help='Stay on the current frequency when at least one packet is detected')
    parser.add_argument('--lock-rounds', default=4, type=int, help='Number of additional hops to stay on the same frequency after a hit')
    parser.add_argument('--truncate-output', action='store_true', help='Deprecated: rejected to protect capture evidence')

    parser.add_argument('--detector-backend', choices=['legacy','cpu','cuda'], default='legacy', help='Explicit detector experiment; CUDA needs CuPy/runtime')
    parser.add_argument('--async-processing', action='store_true', help='Bounded processing worker; default synchronous baseline')
    parser.add_argument('--queue-mib', type=float, default=1024, help='IQ queue budget including in-flight worker block')
    parser.add_argument('--max-seconds', type=float, default=180, help='Maximum acquisition wall time (drain may extend shutdown)')
    parser.add_argument('--max-candidates', type=int, default=100, help='Stop saving at this many candidates, without CRC verification')
    parser.add_argument('--raw-mib', type=float, default=0, help='Disk budget for complete raw acquisitions; zero disables')
    parser.add_argument('--revisit-interval', type=float, default=60, help='Adaptive revisit deadline in seconds')
    main(parser.parse_args())
