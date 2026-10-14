"""Explicit offline ablations; the legacy Packet/default decoder stays unchanged.

integer_cfo_zc selects -2..2 subcarrier offsets by the absolute mean adjacent
phase slope of the second (root147) pilot channel, without using payload CRC.
It then refits pilot channels in the corrected FFT basis and tracks their
common phase drift. This multi-operation repair is compared to individual
ablation variants; it is not claimed to isolate CFO from equalization gains.
All returned confidence amplitudes remain uncalibrated. Fractional sync/ZC
admission is unchanged. Baseline-first acceptance is in decode_packet.py.
"""
import numpy as np
from Packet import Packet
from zcsequence import zcsequence_f, zcsequence_t

MODES=('baseline','cached_channel','refit_fft_reference','refit_zc_reference','interpolated_zc','phase_tracking_zc','integer_cfo_zc')

class MeasuredPacket(Packet):
    def __init__(self,*args,**kwargs):
        self.detected_roots=[]
        super().__init__(*args,**kwargs)
    def find_zc_seq(self,symbol):
        root=super().find_zc_seq(symbol)
        self.detected_roots.append(int(root))
        return root


def _equalize(symbol,channel):
    """Small regularizer avoids amplification at a deep channel null."""
    power=np.abs(channel)**2
    positive=power[power>0]
    floor=(np.median(positive)*1e-6) if len(positive) else 1e-12
    return symbol*np.conj(channel)/np.maximum(power,floor)


def symbols_for_mode(packet,mode):
    if mode not in MODES:raise ValueError('Unknown DSP ablation')
    raw=np.asarray(packet.get_symbol_data(skip_zc=False))
    if mode=='baseline':output=raw.copy()
    elif mode=='cached_channel':output=np.asarray([_equalize(s,packet.channel) for s in raw])
    else:
        if mode=='integer_cfo_zc':
            reference=zcsequence_t(147,raw.shape[1]);dc=raw.shape[1]//2
            scored=[]
            for shift in range(-2,3):
                candidate=np.asarray(packet.get_symbol_data(skip_zc=False,tune=shift*packet.Fs/1024))
                channel=candidate[packet.ZC_SYMBOL_IDX[1]]/reference
                adjacent=channel[1:]*np.conj(channel[:-1])
                adjacent[dc-1:dc+1]=0
                score=abs(np.angle(np.sum(adjacent)))
                scored.append((score,abs(shift),shift,candidate))
            selected=min(scored,key=lambda entry:entry[:3])
            packet.integer_cfo_bins=selected[2]
            packet.integer_cfo_slope=selected[0]
            raw=selected[3]
        if len(packet.detected_roots)!=2:raise ValueError('Measured pilot roots required')
        pilots=packet.ZC_SYMBOL_IDX
        channels=[]
        for index,root in zip(pilots,packet.detected_roots):
            reference=(zcsequence_f if mode=='refit_fft_reference' else zcsequence_t)(root,raw.shape[1]).copy()
            # DC is not data; do not divide by the deliberately zero pilot DC.
            reference[len(reference)//2]=1
            channels.append(raw[index]/reference)
        if mode in ('phase_tracking_zc','integer_cfo_zc'):
            mask=np.ones(raw.shape[1],dtype=bool);mask[raw.shape[1]//2]=False
            delta=np.angle(np.sum(channels[1][mask]*np.conj(channels[0][mask])))
            common=(channels[0]+channels[1]*np.exp(-1j*delta))/2
            output=np.asarray([_equalize(symbol,common*np.exp(1j*delta*(i-pilots[0])/(pilots[1]-pilots[0]))) for i,symbol in enumerate(raw)])
        elif mode=='interpolated_zc':
            output=np.asarray([_equalize(s,channels[0]+(channels[1]-channels[0])*(i-pilots[0])/(pilots[1]-pilots[0])) for i,s in enumerate(raw)])
        else:
            channel=(channels[0]+channels[1])/2
            output=np.asarray([_equalize(s,channel) for s in raw])
    return [s for i,s in enumerate(output) if i not in packet.ZC_SYMBOL_IDX]


def constellation_quality(symbols):
    """Blind QPSK EVM proxy; never a CRC or independent SNR measurement."""
    data=np.delete(np.asarray(symbols),300,axis=1).ravel()
    scale=np.sqrt(np.mean(np.abs(data)**2))
    if not np.isfinite(scale) or scale<=0:return dict(qpsk_evm_proxy=None)
    unit=data/scale
    reference=(np.where(unit.real>=0,1.,-1.)+1j*np.where(unit.imag>=0,1.,-1.))/np.sqrt(2)
    return dict(qpsk_evm_proxy=float(np.sqrt(np.mean(np.abs(unit-reference)**2))))
